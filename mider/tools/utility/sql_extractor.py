"""SQLExtractor: Pro*C 파일에서 EXEC SQL 블록 추출 Tool.

Pro*C 소스 코드에서 EXEC SQL 구문을 파싱하여
SQL 블록, 호스트 변수, 인디케이터 변수, SQLCA 체크 여부를 추출한다.
"""

import logging
import re
from typing import Any

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult
from mider.tools.file_io.file_reader import FileReader
from mider.tools.utility.token_optimizer import find_function_boundaries

logger = logging.getLogger(__name__)

# EXEC SQL 블록 패턴 (EXEC SQL ... ; 까지)
_EXEC_SQL_PATTERN = re.compile(
    r"EXEC\s+SQL\s+(.*?)\s*;",
    re.IGNORECASE | re.DOTALL,
)

# 호스트 변수 패턴 (:변수명, :변수명:인디케이터 제외)
_HOST_VAR_PATTERN = re.compile(r":(\w+)(?!:)")

# 인디케이터 변수 패턴 (:변수명:인디케이터)
_INDICATOR_VAR_PATTERN = re.compile(r":(\w+):(\w+)")

# SQLCA 체크 패턴
_SQLCA_CHECK_PATTERN = re.compile(
    r"sqlca\.sqlcode|SQLCA\.SQLCODE|WHENEVER\s+SQLERROR|WHENEVER\s+NOT\s+FOUND",
    re.IGNORECASE,
)

# DECLARE SECTION 등 비-SQL 구문 (추출 대상 아님)
_NON_SQL_KEYWORDS = {
    "BEGIN", "END", "INCLUDE", "WHENEVER",
    "VAR", "TYPE", "DECLARE",
}


# 함수 시그니처에서 함수명 추출 패턴 (C/ProC)
_FUNC_NAME_PATTERN = re.compile(
    r"^(?!\s*(?:if|else|for|while|switch|return|#|typedef|struct|union|enum)\b)"
    r"\s*(?:static\s+|extern\s+|inline\s+)*"
    r"(?:void|int|char|long|short|unsigned|float|double|size_t|ssize_t|\w+_t|\w+)\s*\*?\s+"
    r"(\w+)\s*\("
)


def _extract_func_names(
    lines: list[str],
    boundaries: list[tuple[int, int]],
) -> dict[int, str]:
    """함수 경계의 시작 라인에서 함수명을 추출한다.

    Returns:
        {start_line: func_name} 딕셔너리
    """
    result: dict[int, str] = {}
    for start, _end in boundaries:
        idx = start - 1  # 1-based → 0-based
        m = _FUNC_NAME_PATTERN.match(lines[idx])
        if m:
            result[start] = m.group(1)
            continue
        # 2줄 선언
        if idx + 1 < len(lines):
            combined = lines[idx].rstrip() + " " + lines[idx + 1].lstrip()
            m = _FUNC_NAME_PATTERN.match(combined)
            if m:
                result[start] = m.group(1)
    return result


def _find_enclosing_function(
    line_num: int,
    boundaries: list[tuple[int, int]],
    func_names: dict[int, str],
) -> str | None:
    """주어진 라인 번호를 포함하는 함수명을 반환한다."""
    for start, end in boundaries:
        if start <= line_num <= end:
            return func_names.get(start)
    return None


class SQLExtractor(BaseTool):
    """Pro*C 파일에서 EXEC SQL 블록을 추출하는 Tool."""

    def __init__(self) -> None:
        self._file_reader = FileReader()

    def execute(self, *, file: str, **kwargs: Any) -> ToolResult:
        """Pro*C 파일에서 EXEC SQL 블록을 추출한다.

        Args:
            file: Pro*C 파일 경로

        Returns:
            ToolResult (data: sql_blocks, total_blocks)

        Raises:
            ToolExecutionError: 파일 읽기 실패 시
        """
        read_result = self._file_reader.execute(path=file)
        content = read_result.data["content"]
        lines = content.splitlines()

        # 함수 경계 + 함수명 매핑 (SQL 블록에 function 필드 추가용)
        boundaries = find_function_boundaries(lines, "proc")
        func_names = _extract_func_names(lines, boundaries)

        sql_blocks: list[dict[str, Any]] = []
        block_id = 0

        for match in _EXEC_SQL_PATTERN.finditer(content):
            sql_body = match.group(1).strip()

            # DECLARE SECTION, INCLUDE, WHENEVER 등은 SQL 블록이 아님
            first_word = sql_body.split()[0].upper() if sql_body else ""
            if first_word in _NON_SQL_KEYWORDS:
                continue

            # 호스트 변수 추출 (인디케이터 포함 패턴 먼저 처리)
            indicator_vars = _INDICATOR_VAR_PATTERN.findall(sql_body)
            indicator_var_names = [ind for _, ind in indicator_vars]

            # 인디케이터 변수 제거 후 호스트 변수 추출
            sql_cleaned = _INDICATOR_VAR_PATTERN.sub(r":\1", sql_body)
            host_vars = _HOST_VAR_PATTERN.findall(sql_cleaned)

            # EXEC SQL 구문 직후 줄부터 SQLCA 체크 확인
            match_end = match.end()
            after_block = content[match_end:match_end + 200]
            has_sqlca_check = bool(_SQLCA_CHECK_PATTERN.search(after_block))

            # EXEC SQL 구문의 라인 번호 계산
            line_number = content[:match.start()].count("\n") + 1

            # 함수 매핑
            func_name = _find_enclosing_function(
                line_number, boundaries, func_names,
            )

            sql_blocks.append({
                "id": block_id,
                "sql": sql_body,
                "host_variables": host_vars,
                "indicator_variables": indicator_var_names,
                "has_sqlca_check": has_sqlca_check,
                "line": line_number,
                "function": func_name,
            })
            block_id += 1

        logger.debug(
            f"SQL 블록 추출 완료: {file} → {len(sql_blocks)}개 블록"
        )

        return ToolResult(
            success=True,
            data={
                "sql_blocks": sql_blocks,
                "total_blocks": len(sql_blocks),
            },
        )
