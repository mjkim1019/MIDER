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

# PK 인덱스 고비용 Range Scan 임계값
_PK_INDEX_HIGH_COST = 100

# 텍스트 덤프 형식의 Operation 키워드 프리픽스
_OPERATION_PREFIXES = (
    "SELECT STATEMENT",
    "UPDATE STATEMENT",
    "DELETE STATEMENT",
    "INSERT STATEMENT",
    "TABLE ACCESS",
    "INDEX",
    "SORT",
    "FILTER",
    "COUNT",
    "VIEW",
    "NESTED LOOP",
    "MERGE JOIN",
    "HASH JOIN",
    "HASH GROUP",
    "HASH UNIQUE",
    "UNION",
    "PARTITION",
    "BUFFER",
    "SEQUENCE",
    "MINUS",
    "INTERSECT",
    "CONNECT BY",
    "INLIST",
    "CARTESIAN",
    "MAT_VIEW",
    "WINDOW",
    "COLLECTION",
    "PX",
    "CONCATENATION",
    "BITMAP",
    "REMOTE",
    "LOAD",
    "TEMP TABLE",
    "RECURSIVE",
)


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
                data={
                    "steps": [],
                    "tuning_points": [],
                    "raw_text": "",
                    "formatted_table": "",
                },
            )

        # Explain Plan 파싱
        steps = self._parse_plan_table(stripped)
        tuning_points = self._detect_tuning_points(steps)
        formatted_table = self._format_as_xplan_table(steps)

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
                "formatted_table": formatted_table,
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

        # 표 형식을 찾지 못한 경우 텍스트 덤프 또는 줄 단위 파싱 시도
        if not steps:
            if self._is_text_dump(content):
                steps = self._parse_text_dump(content)
            else:
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
        # Operation 패턴 매칭 (들여쓰기 기반, _OPERATION_PREFIXES에서 파생)
        _op_alts = "|".join(re.escape(p) for p in _OPERATION_PREFIXES)
        op_pattern = re.compile(
            rf"(\d+)\s+[-]?\s*((?:{_op_alts})[A-Z\s]*)"
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
    def _is_text_dump(content: str) -> bool:
        """텍스트 덤프 형식(각 컬럼이 줄 단위로 나열)인지 감지한다.

        헤더에 "Operation"과 "Access Pred"/"Filter Pred"가 있으면
        텍스트 덤프 형식으로 판단한다.
        """
        first_block = content[:500].lower()
        return "operation" in first_block and (
            "access pred" in first_block or "filter pred" in first_block
        )

    @staticmethod
    def _is_operation_line(line: str) -> bool:
        """텍스트 라인이 Operation 라인인지 판단한다."""
        upper = line.upper().strip()
        # DBMS_XPLAN 헤더 필드 "Filter Pred"가 FILTER prefix와 혼동됨
        # (ACCESS PRED는 _OPERATION_PREFIXES에 없어 자연 제외)
        if upper.startswith("FILTER PRED"):
            return False
        return upper.startswith(_OPERATION_PREFIXES)

    def _parse_text_dump(self, content: str) -> list[dict[str, Any]]:
        """텍스트 덤프 형식의 Explain Plan을 파싱한다.

        각 컬럼(Operation, Object Instance, Access Pred, Filter Pred 등)이
        줄 단위로 나열된 형식을 파싱하여 구조화된 실행 계획 목록을 반환한다.
        """
        steps: list[dict[str, Any]] = []
        lines = content.split("\n")

        # 헤더 섹션을 건너뛰고 첫 Operation 라인 찾기
        data_start = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and self._is_operation_line(stripped):
                data_start = i
                break

        current_step: dict[str, Any] | None = None
        step_id = 0

        for i in range(data_start, len(lines)):
            line = lines[i].strip()
            if not line:
                continue

            if self._is_operation_line(line):
                # 이전 step 저장
                if current_step is not None:
                    steps.append(current_step)
                # 새 operation 파싱
                current_step = self._parse_operation_detail(line, step_id)
                step_id += 1
            elif current_step is not None:
                # 현재 step에 메타데이터 연결
                if re.match(r"^\d+$", line):
                    pass  # Object Instance — 튜닝 분석에 불필요
                elif line == "KEY":
                    pass  # 파티션 키 정보
                else:
                    # Predicate 라인
                    if "predicates" not in current_step:
                        current_step["predicates"] = []
                    current_step["predicates"].append(line)

        # 마지막 step 저장
        if current_step is not None:
            steps.append(current_step)

        return steps

    @staticmethod
    def _parse_operation_detail(line: str, step_id: int) -> dict[str, Any]:
        """Operation 라인에서 오퍼레이션명, 객체명, Cost/Card/Bytes를 추출한다."""
        step: dict[str, Any] = {"id": step_id}

        # OF 'SCHEMA.TABLE' 에서 객체명 추출
        of_match = re.search(r"OF\s+'([^']+)'", line)
        if of_match:
            full_name = of_match.group(1)
            step["name"] = full_name.split(".")[-1] if "." in full_name else full_name
            op_part = line[:of_match.start()].strip()
        else:
            # Cost= 이전까지가 Operation
            op_part = re.sub(r"\s*\(Cost=.*$", "", line).strip()

        # 괄호 제거하여 DBMS_XPLAN 표준 형식으로 정규화
        # "TABLE ACCESS (FULL)" → "TABLE ACCESS FULL"
        # "INDEX (RANGE SCAN)" → "INDEX RANGE SCAN"
        op_clean = op_part.replace("(", " ").replace(")", " ")
        op_clean = re.sub(r"\s+", " ", op_clean).strip()
        step["operation"] = op_clean

        # Cost, Card(=Rows), Bytes 추출
        for field, db_field in [
            ("cost", "Cost"),
            ("rows", "Card"),
            ("bytes", "Bytes"),
        ]:
            m = re.search(rf"{db_field}=(\d+)", line)
            if m:
                step[field] = int(m.group(1))

        return step

    @staticmethod
    def _format_as_xplan_table(steps: list[dict[str, Any]]) -> str:
        """파싱된 실행 계획을 DBMS_XPLAN 테이블 형식 문자열로 변환한다."""
        if not steps:
            return ""

        lines: list[str] = []

        # 헤더
        lines.append("Id | Operation | Object | Cost | Rows")
        lines.append("---|-----------|--------|------|-----")

        for step in steps:
            sid = step.get("id", "")
            op = step.get("operation", "")
            name = step.get("name", "")
            cost = step.get("cost", "")
            rows = step.get("rows", "")
            lines.append(f"{sid} | {op} | {name} | {cost} | {rows}")

        # Predicate 섹션
        pred_lines: list[str] = []
        for step in steps:
            preds = step.get("predicates", [])
            if preds:
                sid = step.get("id", "?")
                for pred in preds:
                    # 200자 초과 시 truncate
                    if len(pred) > 200:
                        pred = pred[:200] + "..."
                    pred_lines.append(f"  {sid} - {pred}")

        if pred_lines:
            lines.append("")
            lines.append("Predicate Information:")
            lines.extend(pred_lines)

        return "\n".join(lines)

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

            # PK 인덱스 고비용 Range Scan 탐지
            # PK 인덱스를 사용하지만 Cost가 높으면 더 적절한 인덱스가 필요할 수 있음
            if (
                "INDEX" in upper_op
                and "RANGE SCAN" in upper_op
                and isinstance(cost, int)
                and cost >= _PK_INDEX_HIGH_COST
                and isinstance(object_name, str)
                and "_PK" in object_name.upper()
            ):
                already_reported = any(
                    tp.get("step_id") == step.get("id")
                    for tp in tuning_points
                )
                if not already_reported:
                    # predicate에서 조인 컬럼 추출
                    predicates = step.get("predicates", [])
                    join_cols = _extract_join_columns(predicates)
                    col_hint = f" ({', '.join(join_cols)})" if join_cols else ""

                    tuning_points.append({
                        "step_id": step.get("id"),
                        "operation": operation.strip(),
                        "object": object_name,
                        "cost": cost,
                        "rows": rows,
                        "severity": "high",
                        "suggestion": (
                            f"PK 인덱스 사용이지만 Cost={cost}으로 높음 "
                            f"— 조인 컬럼{col_hint} 기반 인덱스 힌트 검토: "
                            f"/*+ INDEX(alias{col_hint}) */"
                        ),
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


def _extract_join_columns(predicates: list[str]) -> list[str]:
    """Predicate 텍스트에서 조인 컬럼명을 추출한다.

    "B"."SVC_MGMT_NUM"="C"."SVC_MGMT_NUM" 형태에서
    SVC_MGMT_NUM을 추출한다.
    """
    columns: list[str] = []
    for pred in predicates:
        # "ALIAS"."COLUMN"= 패턴에서 컬럼명 추출
        matches = re.findall(r'"(\w+)"\."(\w+)"', pred)
        for _alias, col in matches:
            col_lower = col.lower()
            # PK 컬럼이 아닌 조인 컬럼만 (일반적인 PK 접미사 제외)
            if col_lower not in columns:
                columns.append(col_lower)
    return columns


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
