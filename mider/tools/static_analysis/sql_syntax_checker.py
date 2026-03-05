"""SQLSyntaxChecker: SQL 문법 검증 도구.

sqlparse를 활용하여 Oracle SQL 파일의 문법 오류를 탐지한다.
- 괄호 불일치
- 따옴표 미닫힘
- 빈 SQL 문
- SELECT 문의 FROM 절 누락
- UPDATE/DELETE 문의 WHERE 절 누락 (경고)
"""

import logging
from pathlib import Path
from typing import Any

import sqlparse
from sqlparse.sql import Parenthesis, Statement
from sqlparse.tokens import (
    DML,
    Keyword,
    Punctuation,
    String,
)

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult

logger = logging.getLogger(__name__)


class SQLSyntaxChecker(BaseTool):
    """SQL 문법 검증 도구.

    sqlparse 토큰화 + 커스텀 규칙으로 Oracle SQL의
    구조적 문법 오류를 탐지한다.
    """

    def execute(self, **kwargs: Any) -> ToolResult:
        """SQL 파일의 문법을 검증한다.

        Args:
            file: SQL 파일 경로

        Returns:
            ToolResult(data={"syntax_errors": [...], "warnings": [...]})
        """
        file_path = kwargs.get("file", "")
        if not file_path:
            raise ToolExecutionError("SQLSyntaxChecker", "file 파라미터가 필요합니다")

        path = Path(file_path)
        if not path.exists():
            raise ToolExecutionError("SQLSyntaxChecker", f"파일 없음: {file_path}")

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = path.read_text(encoding="euc-kr")

        syntax_errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        # 빈 파일 체크
        stripped = content.strip()
        if not stripped:
            return ToolResult(
                success=True,
                data={"syntax_errors": [], "warnings": []},
            )

        # 1. 괄호 불일치 검사
        paren_errors = self._check_parentheses(content)
        syntax_errors.extend(paren_errors)

        # 2. 따옴표 미닫힘 검사
        quote_errors = self._check_unclosed_quotes(content)
        syntax_errors.extend(quote_errors)

        # 3. SQL 문 단위 검사
        statements = sqlparse.parse(content)
        for stmt in statements:
            stmt_text = str(stmt).strip()
            if not stmt_text:
                continue

            stmt_errors, stmt_warnings = self._check_statement(stmt, content)
            syntax_errors.extend(stmt_errors)
            warnings.extend(stmt_warnings)

        logger.info(
            f"SQL 문법 검증 완료: {file_path} → "
            f"{len(syntax_errors)}개 오류, {len(warnings)}개 경고"
        )

        return ToolResult(
            success=True,
            data={
                "syntax_errors": syntax_errors,
                "warnings": warnings,
            },
        )

    @staticmethod
    def _check_parentheses(content: str) -> list[dict[str, Any]]:
        """괄호 불일치를 검사한다."""
        errors: list[dict[str, Any]] = []
        stack: list[tuple[str, int]] = []
        in_single_quote = False
        in_double_quote = False
        in_line_comment = False
        in_block_comment = False

        lines = content.split("\n")
        for line_num, line in enumerate(lines, start=1):
            i = 0
            in_line_comment = False
            while i < len(line):
                ch = line[i]
                next_ch = line[i + 1] if i + 1 < len(line) else ""

                # 블록 주석 시작/종료
                if not in_single_quote and not in_double_quote:
                    if ch == "/" and next_ch == "*" and not in_block_comment:
                        in_block_comment = True
                        i += 2
                        continue
                    if ch == "*" and next_ch == "/" and in_block_comment:
                        in_block_comment = False
                        i += 2
                        continue

                if in_block_comment:
                    i += 1
                    continue

                # 라인 주석
                if not in_single_quote and not in_double_quote:
                    if ch == "-" and next_ch == "-":
                        in_line_comment = True
                        break

                if in_line_comment:
                    break

                # 따옴표 토글
                if ch == "'" and not in_double_quote:
                    in_single_quote = not in_single_quote
                elif ch == '"' and not in_single_quote:
                    in_double_quote = not in_double_quote

                # 문자열 내부면 괄호 무시
                if not in_single_quote and not in_double_quote:
                    if ch == "(":
                        stack.append(("(", line_num))
                    elif ch == ")":
                        if stack:
                            stack.pop()
                        else:
                            errors.append({
                                "line": line_num,
                                "message": "닫는 괄호 ')'에 대응하는 여는 괄호 없음",
                                "rule": "unmatched_paren",
                            })

                i += 1

        for paren, line_num in stack:
            errors.append({
                "line": line_num,
                "message": "여는 괄호 '('에 대응하는 닫는 괄호 없음",
                "rule": "unmatched_paren",
            })

        return errors

    @staticmethod
    def _check_unclosed_quotes(content: str) -> list[dict[str, Any]]:
        """따옴표 미닫힘을 검사한다."""
        errors: list[dict[str, Any]] = []
        in_block_comment = False

        lines = content.split("\n")
        for line_num, line in enumerate(lines, start=1):
            # 블록 주석 추적
            i = 0
            temp_line = ""
            while i < len(line):
                ch = line[i]
                next_ch = line[i + 1] if i + 1 < len(line) else ""

                if ch == "/" and next_ch == "*" and not in_block_comment:
                    in_block_comment = True
                    i += 2
                    continue
                if ch == "*" and next_ch == "/" and in_block_comment:
                    in_block_comment = False
                    i += 2
                    continue

                if not in_block_comment:
                    temp_line += ch
                i += 1

            if in_block_comment:
                continue

            # 라인 주석 제거
            line_no_comment = temp_line.split("--", 1)[0]

            # 작은따옴표 카운트 (''은 이스케이프이므로 제거)
            cleaned = line_no_comment.replace("''", "")
            single_count = cleaned.count("'")
            if single_count % 2 != 0:
                errors.append({
                    "line": line_num,
                    "message": "작은따옴표(') 미닫힘",
                    "rule": "unclosed_quote",
                })

        return errors

    def _check_statement(
        self,
        stmt: Statement,
        full_content: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """개별 SQL 문의 구조를 검사한다."""
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        stmt_text = str(stmt).strip()

        if not stmt_text:
            return errors, warnings

        # 문의 시작 라인 번호 계산
        stmt_start = self._find_stmt_line(stmt_text, full_content)

        # DML 유형 파악
        dml_type = self._get_dml_type(stmt)

        if dml_type == "SELECT":
            # SELECT 문에 FROM 절 누락 검사 (단순 값 SELECT 제외)
            if not self._has_keyword(stmt, "FROM"):
                upper_text = stmt_text.upper()
                # SELECT 1, SELECT SYSDATE 등 단순 값은 제외
                if not self._is_simple_select(upper_text):
                    errors.append({
                        "line": stmt_start,
                        "message": "SELECT 문에 FROM 절이 누락되었습니다",
                        "rule": "missing_from",
                    })

        elif dml_type == "UPDATE":
            if not self._has_keyword(stmt, "WHERE"):
                warnings.append({
                    "line": stmt_start,
                    "message": "UPDATE 문에 WHERE 절이 없습니다 (전체 행 갱신 위험)",
                    "rule": "update_without_where",
                })

        elif dml_type == "DELETE":
            if not self._has_keyword(stmt, "WHERE"):
                warnings.append({
                    "line": stmt_start,
                    "message": "DELETE 문에 WHERE 절이 없습니다 (전체 행 삭제 위험)",
                    "rule": "delete_without_where",
                })

        return errors, warnings

    @staticmethod
    def _get_dml_type(stmt: Statement) -> str | None:
        """SQL 문의 DML 유형을 반환한다."""
        for token in stmt.tokens:
            if token.ttype is DML:
                return token.value.upper()
        return None

    @staticmethod
    def _has_keyword(stmt: Statement, keyword: str) -> bool:
        """SQL 문에 특정 키워드가 존재하는지 확인한다."""
        for token in stmt.flatten():
            if token.ttype is Keyword and token.value.upper() == keyword:
                return True
        return False

    @staticmethod
    def _is_simple_select(upper_text: str) -> bool:
        """단순 값 SELECT인지 확인한다 (FROM 불필요)."""
        # SELECT 1, SELECT SYSDATE, SELECT seq.NEXTVAL 등
        simple_patterns = [
            "SELECT 1",
            "SELECT SYSDATE",
            "SELECT SYSTIMESTAMP",
            "SELECT USER",
            "SELECT SYS_CONTEXT",
        ]
        for pattern in simple_patterns:
            if upper_text.startswith(pattern):
                return True
        # SELECT expression FROM DUAL 패턴은 FROM이 있으므로 여기 안 옴
        # 함수 호출만 있는 경우: SELECT func() → FROM 없어도 가능 (Oracle DUAL 생략)
        return False

    @staticmethod
    def _find_stmt_line(stmt_text: str, full_content: str) -> int:
        """SQL 문의 시작 라인 번호를 찾는다."""
        first_line = stmt_text.split("\n", 1)[0].strip()
        if not first_line:
            return 1

        lines = full_content.split("\n")
        for i, line in enumerate(lines):
            if first_line in line:
                return i + 1
        return 1
