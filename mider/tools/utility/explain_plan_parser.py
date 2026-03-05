"""ExplainPlanParser: Oracle Explain Plan 결과 파싱 도구.

Explain Plan 텍스트 파일을 파싱하여 실행 계획 단계별
Operation, Cost, Rows, Bytes 등을 추출하고 비효율 오퍼레이션을 탐지한다.

지원하는 Explain Plan 형식:
- DBMS_XPLAN.DISPLAY_CURSOR 출력
- EXPLAIN PLAN + SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY) 출력
- 표 형식 (구분선 '---' 포함)
"""

import logging
import re
from pathlib import Path
from typing import Any

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult

logger = logging.getLogger(__name__)

# 비효율 오퍼레이션 패턴
_INEFFICIENT_OPERATIONS: dict[str, str] = {
    "TABLE ACCESS FULL": "Full Table Scan — 인덱스 생성 또는 WHERE 조건 추가 권장",
    "CARTESIAN": "Cartesian Join — JOIN 조건이 누락되었을 수 있음",
    "SORT MERGE JOIN": "Sort Merge Join — 대량 데이터에서 성능 저하 가능, Nested Loop 또는 Hash Join 검토",
    "MERGE JOIN CARTESIAN": "Cartesian Merge Join — JOIN 조건 누락 확인 필요",
    "SORT ORDER BY": "대량 정렬 — 인덱스 활용 또는 정렬 최소화 검토",
    "HASH JOIN": "Hash Join — 대량 데이터 JOIN 시 메모리 사용량 확인 필요",
    "NESTED LOOPS": "Nested Loops — 드라이빙 테이블 크기가 크면 성능 저하",
}

# 높은 Cost 임계값
_HIGH_COST_THRESHOLD = 1000
_HIGH_ROWS_THRESHOLD = 100000


class ExplainPlanParser(BaseTool):
    """Oracle Explain Plan 결과를 파싱하는 도구.

    표 형식의 Explain Plan 출력을 파싱하여 각 단계의
    Operation, Cost, Rows, Bytes를 추출하고 튜닝 포인트를 생성한다.
    """

    def execute(self, **kwargs: Any) -> ToolResult:
        """Explain Plan 파일을 파싱한다.

        Args:
            file: Explain Plan 결과 파일 경로

        Returns:
            ToolResult(data={"steps": [...], "tuning_points": [...], "raw_text": "..."})
        """
        file_path = kwargs.get("file", "")
        if not file_path:
            raise ToolExecutionError("ExplainPlanParser", "file 파라미터가 필요합니다")

        path = Path(file_path)
        if not path.exists():
            raise ToolExecutionError("ExplainPlanParser", f"파일 없음: {file_path}")

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = path.read_text(encoding="euc-kr")

        stripped = content.strip()
        if not stripped:
            return ToolResult(
                success=True,
                data={"steps": [], "tuning_points": [], "raw_text": ""},
            )

        # Explain Plan 파싱
        steps = self._parse_plan_table(stripped)
        tuning_points = self._detect_tuning_points(steps)

        logger.info(
            f"Explain Plan 파싱 완료: {file_path} → "
            f"{len(steps)}개 단계, {len(tuning_points)}개 튜닝 포인트"
        )

        return ToolResult(
            success=True,
            data={
                "steps": steps,
                "tuning_points": tuning_points,
                "raw_text": stripped,
            },
        )

    def _parse_plan_table(self, content: str) -> list[dict[str, Any]]:
        """Explain Plan 표 형식을 파싱한다.

        DBMS_XPLAN.DISPLAY 출력 형식:
        ---------------------------------------------------------------------------
        | Id  | Operation          | Name   | Rows  | Bytes | Cost (%CPU)| Time   |
        ---------------------------------------------------------------------------
        |   0 | SELECT STATEMENT   |        |    50 |  2400 |   234  (1) | 00:00:01|
        |   1 |  TABLE ACCESS FULL | ORDERS |    50 |  2400 |   234  (1) | 00:00:01|
        ---------------------------------------------------------------------------
        """
        steps: list[dict[str, Any]] = []
        lines = content.split("\n")

        # 헤더 행과 데이터 행 식별
        header_pattern = re.compile(
            r"\|\s*Id\s*\|\s*Operation\s*\|", re.IGNORECASE,
        )
        data_pattern = re.compile(
            r"\|\s*(\d+)\s*\|(.+?)(?:\||$)",
        )

        header_found = False
        header_columns: list[str] = []
        separator_count = 0

        for line in lines:
            stripped_line = line.strip()

            # 구분선 카운트
            if re.match(r"^[-|+]+$", stripped_line.replace(" ", "")):
                separator_count += 1
                continue

            # 헤더 행 식별
            if header_pattern.search(stripped_line):
                header_found = True
                header_columns = self._parse_header(stripped_line)
                continue

            # 데이터 행 파싱
            if header_found and stripped_line.startswith("|"):
                step = self._parse_data_row(stripped_line, header_columns)
                if step is not None:
                    steps.append(step)

        # 표 형식을 찾지 못한 경우 줄 단위 파싱 시도
        if not steps:
            steps = self._parse_plain_format(content)

        return steps

    @staticmethod
    def _parse_header(header_line: str) -> list[str]:
        """헤더 행에서 컬럼명을 추출한다."""
        parts = header_line.split("|")
        columns: list[str] = []
        for part in parts:
            col = part.strip()
            if col:
                columns.append(col.lower().replace("(%cpu)", "").strip().replace(" ", "_"))
        return columns

    @staticmethod
    def _parse_data_row(
        line: str,
        header_columns: list[str],
    ) -> dict[str, Any] | None:
        """데이터 행을 파싱한다."""
        # | 로 분할 후 양 끝 빈 요소 제거 (positional alignment 유지)
        parts = line.split("|")
        # 앞뒤 빈 문자열 제거 (| 시작/끝)
        if parts and parts[0].strip() == "":
            parts = parts[1:]
        if parts and parts[-1].strip() == "":
            parts = parts[:-1]

        if not parts:
            return None

        step: dict[str, Any] = {}

        for i, part in enumerate(parts):
            val = part.strip()
            if i < len(header_columns):
                col_name = header_columns[i]
            else:
                col_name = f"col_{i}"

            # 빈 값은 건너뜀 (Name이 비어있는 경우 등)
            if not val:
                continue

            # 숫자 필드 변환
            if col_name in ("id", "rows", "bytes", "cost"):
                # Cost에서 "(1)" 같은 CPU% 제거
                cleaned = re.sub(r"\s*\(\d+\)\s*", "", val).strip()
                try:
                    step[col_name] = int(cleaned)
                except ValueError:
                    step[col_name] = val
            else:
                step[col_name] = val

        # 최소한 id가 있어야 유효
        if "id" not in step:
            return None

        return step

    @staticmethod
    def _parse_plain_format(content: str) -> list[dict[str, Any]]:
        """표 형식이 아닌 일반 텍스트에서 Explain Plan 정보를 추출한다."""
        steps: list[dict[str, Any]] = []
        # Operation 패턴 매칭 (들여쓰기 기반)
        op_pattern = re.compile(
            r"(\d+)\s+[-]?\s*((?:TABLE ACCESS|INDEX|SORT|HASH|"
            r"NESTED|MERGE|FILTER|SELECT|VIEW|UNION|"
            r"PARTITION|BUFFER|SEQUENCE|COUNT|MINUS|"
            r"INTERSECT|CONNECT|INLIST|UPDATE|DELETE|INSERT|"
            r"CARTESIAN|MAT_VIEW)[A-Z\s]*)"
            r"(?:\s+(\S+))?"  # object name
            r"(?:.*?cost[=:]\s*(\d+))?"  # cost
            r"(?:.*?rows[=:]\s*(\d+))?"  # rows
            r"(?:.*?bytes[=:]\s*(\d+))?",  # bytes
            re.IGNORECASE,
        )

        for match in op_pattern.finditer(content):
            step: dict[str, Any] = {
                "id": int(match.group(1)),
                "operation": match.group(2).strip(),
            }
            if match.group(3):
                step["name"] = match.group(3)
            if match.group(4):
                step["cost"] = int(match.group(4))
            if match.group(5):
                step["rows"] = int(match.group(5))
            if match.group(6):
                step["bytes"] = int(match.group(6))
            steps.append(step)

        return steps

    @staticmethod
    def _detect_tuning_points(
        steps: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """파싱된 실행 계획에서 튜닝 포인트를 탐지한다."""
        tuning_points: list[dict[str, Any]] = []

        for step in steps:
            operation = step.get("operation", "")
            if not isinstance(operation, str):
                continue

            upper_op = operation.upper().strip()
            cost = step.get("cost", 0)
            rows = step.get("rows", 0)
            object_name = step.get("name", "")

            # 비효율 오퍼레이션 탐지
            for pattern, suggestion in _INEFFICIENT_OPERATIONS.items():
                if pattern in upper_op:
                    tuning_points.append({
                        "step_id": step.get("id"),
                        "operation": operation.strip(),
                        "object": object_name,
                        "cost": cost,
                        "rows": rows,
                        "severity": _classify_severity(upper_op, cost, rows),
                        "suggestion": suggestion,
                    })
                    break

            # 높은 Cost 탐지
            if isinstance(cost, int) and cost >= _HIGH_COST_THRESHOLD:
                # 이미 비효율 오퍼레이션으로 잡힌 경우 중복 방지
                already_reported = any(
                    tp.get("step_id") == step.get("id")
                    for tp in tuning_points
                )
                if not already_reported:
                    tuning_points.append({
                        "step_id": step.get("id"),
                        "operation": operation.strip(),
                        "object": object_name,
                        "cost": cost,
                        "rows": rows,
                        "severity": "high" if cost >= _HIGH_COST_THRESHOLD * 10 else "medium",
                        "suggestion": f"Cost가 {cost}으로 높음 — 쿼리 최적화 필요",
                    })

            # 대량 Rows 탐지
            if isinstance(rows, int) and rows >= _HIGH_ROWS_THRESHOLD:
                already_reported = any(
                    tp.get("step_id") == step.get("id")
                    for tp in tuning_points
                )
                if not already_reported:
                    tuning_points.append({
                        "step_id": step.get("id"),
                        "operation": operation.strip(),
                        "object": object_name,
                        "cost": cost,
                        "rows": rows,
                        "severity": "medium",
                        "suggestion": f"예상 행 수가 {rows:,}건으로 많음 — 필터링 조건 검토",
                    })

        return tuning_points


def _classify_severity(operation: str, cost: int, rows: int) -> str:
    """오퍼레이션 + 비용/행수 기반으로 심각도를 분류한다."""
    # Cartesian Join은 항상 critical
    if "CARTESIAN" in operation:
        return "critical"

    # Full Table Scan + 높은 Cost
    if "TABLE ACCESS FULL" in operation:
        if isinstance(cost, int) and cost >= _HIGH_COST_THRESHOLD:
            return "high"
        if isinstance(rows, int) and rows >= _HIGH_ROWS_THRESHOLD:
            return "high"
        return "medium"

    # 나머지는 Cost 기반
    if isinstance(cost, int) and cost >= _HIGH_COST_THRESHOLD * 10:
        return "high"

    return "medium"
