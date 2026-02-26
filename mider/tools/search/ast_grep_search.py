"""AstGrepSearch: AST 기반 구조 패턴 검색 Tool.

언어별 구문 패턴을 정규표현식으로 검색한다.
1차 PoC에서는 ast-grep 바이너리 없이 정규표현식 기반 패턴 매칭으로 구현.
"""

import logging
import re
from typing import Any

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult
from mider.tools.file_io.file_reader import FileReader

logger = logging.getLogger(__name__)

# 언어별 구조 패턴 정규표현식
LANGUAGE_PATTERNS: dict[str, dict[str, str]] = {
    "javascript": {
        "function_def": r"(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:function|\(.*?\)\s*=>))",
        "import": r"(?:import\s+.*?from\s+['\"](.+?)['\"]|require\s*\(\s*['\"](.+?)['\"]\s*\))",
        "class_def": r"class\s+(\w+)",
        "async_function": r"async\s+function\s+(\w+)",
        "event_listener": r"\.addEventListener\s*\(\s*['\"](\w+)['\"]",
        "dom_manipulation": r"(?:innerHTML|outerHTML|document\.write|eval)\s*[=(]",
    },
    "c": {
        "function_def": r"^\s*(?:\w+\s+)+(\w+)\s*\([^)]*\)\s*\{",
        "include": r'#include\s+[<"](.+?)[>"]',
        "malloc": r"\bmalloc\s*\(",
        "free": r"\bfree\s*\(",
        "strcpy": r"\bstrcpy\s*\(",
        "sprintf": r"\bsprintf\s*\(",
    },
    "proc": {
        "exec_sql": r"EXEC\s+SQL\b",
        "include": r'(?:EXEC\s+SQL\s+INCLUDE\s+(\w+)|#include\s+[<"](.+?)[>"])',
        "declare_section": r"EXEC\s+SQL\s+(?:BEGIN|END)\s+DECLARE\s+SECTION",
        "sqlca_check": r"sqlca\.sqlcode",
        "cursor_open": r"EXEC\s+SQL\s+OPEN\s+(\w+)",
        "cursor_close": r"EXEC\s+SQL\s+CLOSE\s+(\w+)",
        "commit": r"EXEC\s+SQL\s+COMMIT",
        "rollback": r"EXEC\s+SQL\s+ROLLBACK",
    },
    "sql": {
        "select_star": r"\bSELECT\s+\*",
        "function_in_where": r"WHERE\s+.*?(?:YEAR|MONTH|UPPER|LOWER|TRIM|TO_CHAR|TO_DATE|NVL)\s*\(",
        "like_wildcard": r"LIKE\s+['\"]%",
        "subquery": r"\(\s*SELECT\b",
        "join": r"\bJOIN\b",
        "or_condition": r"\bOR\b",
    },
}


class AstGrepSearch(BaseTool):
    """AST 기반 구조 패턴 검색 Tool.

    1차 PoC: 정규표현식 기반 패턴 매칭으로 구현.
    """

    def __init__(self) -> None:
        self._file_reader = FileReader()

    def execute(
        self,
        *,
        pattern: str,
        file: str,
        language: str,
        **kwargs: Any,
    ) -> ToolResult:
        """파일에서 AST 구조 패턴을 검색한다.

        Args:
            pattern: 패턴명 (LANGUAGE_PATTERNS의 키) 또는 커스텀 정규표현식
            file: 검색할 파일 경로
            language: 파일 언어 ("javascript", "c", "proc", "sql")

        Returns:
            ToolResult (data: matches, total_matches, pattern_name)

        Raises:
            ToolExecutionError: 파일 읽기 실패 또는 지원하지 않는 언어 시
        """
        if language not in LANGUAGE_PATTERNS:
            raise ToolExecutionError(
                "ast_grep_search",
                f"unsupported language: {language}. "
                f"supported: {list(LANGUAGE_PATTERNS.keys())}",
            )

        lang_patterns = LANGUAGE_PATTERNS[language]

        if pattern in lang_patterns:
            regex = lang_patterns[pattern]
            pattern_name = pattern
        else:
            regex = pattern
            pattern_name = "custom"

        # SQL, Pro*C는 대소문자 비구분, JS/C는 대소문자 구분
        _CASE_INSENSITIVE_LANGUAGES = {"sql", "proc"}
        flags = re.MULTILINE
        if language in _CASE_INSENSITIVE_LANGUAGES:
            flags |= re.IGNORECASE

        try:
            compiled = re.compile(regex, flags)
        except re.error as e:
            raise ToolExecutionError(
                "ast_grep_search", f"invalid pattern: {e}"
            ) from e

        read_result = self._file_reader.execute(path=file)
        content = read_result.data["content"]
        lines = content.splitlines()

        matches: list[dict[str, Any]] = []
        for line_num, line in enumerate(lines, start=1):
            match = compiled.search(line)
            if match:
                groups = [g for g in match.groups() if g is not None]
                matches.append({
                    "line": line_num,
                    "content": line.strip(),
                    "match": match.group(),
                    "captured": groups[0] if groups else None,
                })

        logger.debug(
            f"AST 패턴 검색 완료: {pattern_name} in {file} → {len(matches)}건"
        )

        return ToolResult(
            success=True,
            data={
                "matches": matches,
                "total_matches": len(matches),
                "pattern_name": pattern_name,
            },
        )
