"""CommentRemover: 소스코드에서 주석을 제거하는 전처리 도구.

개인정보(이름, 사번 등)가 주석에 포함될 수 있으므로,
LLM 분석 전에 주석을 제거하여 개인정보 노출을 방지한다.

지원 언어: JavaScript, C, Pro*C, SQL, XML
핵심 원칙: 줄번호 보존 (주석을 삭제하지 않고 공백으로 치환)
"""

import logging
from enum import Enum, auto
from typing import Any

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult

logger = logging.getLogger(__name__)


class _State(Enum):
    """상태 머신의 상태 정의."""

    CODE = auto()
    STRING_DOUBLE = auto()
    STRING_SINGLE = auto()
    STRING_BACKTICK = auto()
    LINE_COMMENT = auto()
    BLOCK_COMMENT = auto()
    CDATA = auto()


# ──────────────────────────────────────────────
# C 스타일 주석 제거 (C, JavaScript 공통 기반)
# ──────────────────────────────────────────────


def _remove_c_style_comments(
    content: str,
    *,
    support_backtick: bool = False,
    support_regex: bool = False,
    support_sql_dash: bool = False,
) -> tuple[str, int]:
    """C 스타일 주석(//, /* */)을 제거한다.

    Args:
        content: 원본 소스코드
        support_backtick: JS 템플릿 리터럴(`) 지원 여부
        support_regex: JS 정규식 리터럴(/pattern/) 지원 여부
        support_sql_dash: EXEC SQL 블록 내 -- 주석 지원 여부 (Pro*C)

    Returns:
        (주석 제거된 코드, 제거된 주석 수) 튜플
    """
    result: list[str] = []
    state = _State.CODE
    i = 0
    length = len(content)
    removed_count = 0
    escape_next = False

    # Shebang(#!) 처리: 첫 줄을 그대로 출력하고 건너뜀
    if support_regex and length >= 2 and content[0] == "#" and content[1] == "!":
        nl_idx = content.find("\n")
        if nl_idx == -1:
            return content, 0
        result.append(content[: nl_idx + 1])
        i = nl_idx + 1

    # Pro*C: EXEC SQL 블록 추적
    in_exec_sql = False

    # JS 정규식 판별용: 마지막 의미 있는 토큰
    last_significant_char = ""
    _REGEX_PREV_CHARS = frozenset("=([!&|?:,;{}~^%+-*/><")

    # JS 템플릿 리터럴 ${} 중첩 추적
    template_brace_stack: list[int] = []

    while i < length:
        ch = content[i]

        # ── CODE 상태 ──
        if state == _State.CODE:
            if escape_next:
                escape_next = False
                result.append(ch)
                i += 1
                continue

            # 이스케이프
            if ch == "\\":
                escape_next = True
                result.append(ch)
                if ch not in (" ", "\t", "\n"):
                    last_significant_char = ch
                i += 1
                continue

            # 쌍따옴표 문자열 시작
            if ch == '"':
                state = _State.STRING_DOUBLE
                result.append(ch)
                last_significant_char = ch
                i += 1
                continue

            # 홑따옴표: C/Pro*C에서는 문자 리터럴, 그 외는 문자열
            if ch == "'":
                state = _State.STRING_SINGLE
                result.append(ch)
                last_significant_char = ch
                i += 1
                continue

            # JS 템플릿 리터럴 ${} 중첩: 닫는 } 처리
            if support_backtick and template_brace_stack and ch == "}":
                if template_brace_stack[-1] == 0:
                    # 템플릿 표현식 종료, 백틱 문자열로 복귀
                    template_brace_stack.pop()
                    state = _State.STRING_BACKTICK
                    result.append(ch)
                    i += 1
                    continue
                else:
                    template_brace_stack[-1] -= 1

            # JS 템플릿 리터럴 ${} 중첩: 여는 { 추적
            if support_backtick and template_brace_stack and ch == "{":
                template_brace_stack[-1] += 1

            # JS 백틱 템플릿 리터럴
            if support_backtick and ch == "`":
                state = _State.STRING_BACKTICK
                result.append(ch)
                last_significant_char = ch
                i += 1
                continue

            # 슬래시: 주석 시작 또는 나눗셈 또는 정규식
            if ch == "/":
                # 다음 문자 확인
                if i + 1 < length:
                    next_ch = content[i + 1]
                    # // 한 줄 주석
                    if next_ch == "/":
                        state = _State.LINE_COMMENT
                        removed_count += 1
                        i += 2  # '//' 건너뜀
                        continue
                    # /* 블록 주석
                    if next_ch == "*":
                        state = _State.BLOCK_COMMENT
                        removed_count += 1
                        i += 2  # '/*' 건너뜀
                        continue

                # JS 정규식 리터럴 판별
                if support_regex and last_significant_char in _REGEX_PREV_CHARS:
                    # 정규식으로 처리: 닫는 / 까지 코드로 유지
                    result.append(ch)
                    i += 1
                    _in_char_class = False
                    while i < length:
                        rc = content[i]
                        result.append(rc)
                        if rc == "\\" and i + 1 < length:
                            # 이스케이프된 문자
                            i += 1
                            result.append(content[i])
                        elif rc == "[":
                            _in_char_class = True
                        elif rc == "]":
                            _in_char_class = False
                        elif rc == "/" and not _in_char_class:
                            i += 1
                            # 플래그 (g, i, m, s, u, y 등)
                            while i < length and content[i].isalpha():
                                result.append(content[i])
                                i += 1
                            break
                        i += 1
                    last_significant_char = "/"
                    continue

                # 일반 나눗셈
                result.append(ch)
                last_significant_char = ch
                i += 1
                continue

            # Pro*C: EXEC SQL 블록 추적
            if support_sql_dash:
                # EXEC SQL 시작 감지
                if not in_exec_sql and ch in ("E", "e"):
                    candidate = content[i : i + 8].upper()
                    if candidate == "EXEC SQL" and (
                        i + 8 >= length or not content[i + 8].isalnum()
                    ):
                        in_exec_sql = True

                # EXEC SQL 블록 종료 (세미콜론)
                if in_exec_sql and ch == ";":
                    in_exec_sql = False

                # EXEC SQL 내 -- 주석
                if (
                    in_exec_sql
                    and ch == "-"
                    and i + 1 < length
                    and content[i + 1] == "-"
                ):
                    state = _State.LINE_COMMENT
                    removed_count += 1
                    i += 2
                    continue

            # 줄바꿈
            if ch == "\n":
                result.append(ch)
                i += 1
                continue

            # 일반 코드 문자
            result.append(ch)
            if ch not in (" ", "\t"):
                last_significant_char = ch
            i += 1

        # ── LINE_COMMENT 상태 ──
        elif state == _State.LINE_COMMENT:
            if ch == "\n":
                # 줄바꿈 보존, 주석 끝
                result.append(ch)
                state = _State.CODE
            elif ch == "\r":
                # CRLF 줄바꿈의 \r 보존
                result.append(ch)
            # 주석 내용은 버림 (줄번호 보존)
            i += 1

        # ── BLOCK_COMMENT 상태 ──
        elif state == _State.BLOCK_COMMENT:
            if ch == "\n":
                # 블록 주석 내 줄바꿈 보존
                result.append(ch)
            elif ch == "\r":
                # CRLF 줄바꿈의 \r 보존
                result.append(ch)
            elif ch == "*" and i + 1 < length and content[i + 1] == "/":
                # 블록 주석 종료
                state = _State.CODE
                i += 2  # '*/' 건너뜀
                continue
            # 주석 내용은 버림
            i += 1

        # ── STRING_DOUBLE 상태 ──
        elif state == _State.STRING_DOUBLE:
            result.append(ch)
            if escape_next:
                escape_next = False
            elif ch == "\\":
                escape_next = True
            elif ch == '"':
                state = _State.CODE
                last_significant_char = ch
            i += 1

        # ── STRING_SINGLE 상태 ──
        elif state == _State.STRING_SINGLE:
            result.append(ch)
            if escape_next:
                escape_next = False
            elif ch == "\\":
                escape_next = True
            elif ch == "'":
                state = _State.CODE
                last_significant_char = ch
            i += 1

        # ── STRING_BACKTICK 상태 ──
        elif state == _State.STRING_BACKTICK:
            result.append(ch)
            if escape_next:
                escape_next = False
            elif ch == "\\":
                escape_next = True
            elif ch == "$" and i + 1 < length and content[i + 1] == "{":
                # 템플릿 표현식 시작: ${...}
                result.append(content[i + 1])
                template_brace_stack.append(0)
                state = _State.CODE
                last_significant_char = "{"
                i += 2
                continue
            elif ch == "`":
                state = _State.CODE
                last_significant_char = ch
            i += 1

        else:
            result.append(ch)
            i += 1

    return "".join(result), removed_count


# ──────────────────────────────────────────────
# SQL 주석 제거
# ──────────────────────────────────────────────


def _remove_sql_comments(content: str) -> tuple[str, int]:
    """SQL 주석(--, /* */)을 제거한다.

    문자열 리터럴('...') 내부는 보존한다.
    """
    result: list[str] = []
    state = _State.CODE
    i = 0
    length = len(content)
    removed_count = 0

    while i < length:
        ch = content[i]

        if state == _State.CODE:
            # 홑따옴표 문자열 (SQL 표준)
            if ch == "'":
                state = _State.STRING_SINGLE
                result.append(ch)
                i += 1
                continue

            # -- 한 줄 주석
            if ch == "-" and i + 1 < length and content[i + 1] == "-":
                state = _State.LINE_COMMENT
                removed_count += 1
                i += 2
                continue

            # /* 블록 주석
            if ch == "/" and i + 1 < length and content[i + 1] == "*":
                state = _State.BLOCK_COMMENT
                removed_count += 1
                i += 2
                continue

            result.append(ch)
            i += 1

        elif state == _State.LINE_COMMENT:
            if ch == "\n":
                result.append(ch)
                state = _State.CODE
            elif ch == "\r":
                result.append(ch)
            i += 1

        elif state == _State.BLOCK_COMMENT:
            if ch == "\n":
                result.append(ch)
            elif ch == "\r":
                result.append(ch)
            elif ch == "*" and i + 1 < length and content[i + 1] == "/":
                state = _State.CODE
                i += 2
                continue
            i += 1

        elif state == _State.STRING_SINGLE:
            result.append(ch)
            # SQL에서 '' 는 이스케이프된 홑따옴표
            if ch == "'" and i + 1 < length and content[i + 1] == "'":
                result.append(content[i + 1])
                i += 2
                continue
            elif ch == "'":
                state = _State.CODE
            i += 1

        else:
            result.append(ch)
            i += 1

    return "".join(result), removed_count


# ──────────────────────────────────────────────
# XML 주석 제거
# ──────────────────────────────────────────────


def _remove_xml_comments(content: str) -> tuple[str, int]:
    """XML 주석(<!-- -->)을 제거한다.

    CDATA 섹션(<![CDATA[...]]>) 내부는 보존한다.
    단, <script> 태그 내부의 CDATA 섹션에 포함된 JS 주석은 제거한다.
    """
    result: list[str] = []
    state = _State.CODE
    i = 0
    length = len(content)
    removed_count = 0
    in_script_tag = False
    cdata_buffer: list[str] = []

    while i < length:
        ch = content[i]

        if state == _State.CODE:
            # <script 태그 감지 (대소문자 무시)
            if content[i : i + 7].lower() == "<script" and (
                i + 7 >= length or content[i + 7] in (" ", ">", "\t", "\n", "\r")
            ):
                in_script_tag = True
            # </script> 태그 감지 (대소문자 무시)
            elif content[i : i + 9].lower() == "</script>":
                in_script_tag = False

            # CDATA 시작
            if content[i : i + 9] == "<![CDATA[":
                state = _State.CDATA
                result.append(content[i : i + 9])
                if in_script_tag:
                    cdata_buffer = []
                i += 9
                continue

            # XML 주석 시작
            if content[i : i + 4] == "<!--":
                state = _State.BLOCK_COMMENT
                removed_count += 1
                i += 4
                continue

            result.append(ch)
            i += 1

        elif state == _State.BLOCK_COMMENT:
            if ch == "\n":
                result.append(ch)
            elif ch == "\r":
                result.append(ch)
            elif content[i : i + 3] == "-->":
                state = _State.CODE
                i += 3
                continue
            i += 1

        elif state == _State.CDATA:
            if content[i : i + 3] == "]]>":
                if in_script_tag:
                    # script CDATA 내용에서 JS 주석 제거
                    cdata_content = "".join(cdata_buffer)
                    cleaned, js_removed = _remove_c_style_comments(
                        cdata_content,
                        support_backtick=True,
                        support_regex=True,
                    )
                    result.append(cleaned)
                    removed_count += js_removed
                    cdata_buffer = []
                result.append("]]>")
                state = _State.CODE
                i += 3
                continue
            if in_script_tag:
                cdata_buffer.append(ch)
            else:
                result.append(ch)
            i += 1

        else:
            result.append(ch)
            i += 1

    return "".join(result), removed_count


# ──────────────────────────────────────────────
# 언어별 디스패치
# ──────────────────────────────────────────────

_SUPPORTED_LANGUAGES = frozenset({"javascript", "c", "proc", "sql", "xml"})


class CommentRemover(BaseTool):
    """소스코드에서 주석을 제거하는 전처리 도구.

    줄번호를 보존하면서 주석 내용만 제거한다.
    블록 주석 내 줄바꿈은 유지되어 원본과 동일한 줄 수를 보장한다.
    """

    def execute(self, *, content: str, language: str) -> ToolResult:
        """주석을 제거한 소스코드를 반환한다.

        Args:
            content: 원본 소스코드
            language: "javascript" | "c" | "proc" | "sql" | "xml"

        Returns:
            ToolResult(data={"content": 주석 제거된 코드, "removed_count": 제거된 주석 수})

        Raises:
            ToolExecutionError: 지원하지 않는 언어
        """
        if language not in _SUPPORTED_LANGUAGES:
            raise ToolExecutionError(
                "comment_remover",
                f"지원하지 않는 언어: {language} "
                f"(지원: {', '.join(sorted(_SUPPORTED_LANGUAGES))})",
            )

        if not content or not content.strip():
            return ToolResult(
                success=True,
                data={"content": content, "removed_count": 0},
            )

        original_line_count = content.count("\n")

        if language == "javascript":
            cleaned, removed = _remove_c_style_comments(
                content,
                support_backtick=True,
                support_regex=True,
            )
        elif language == "c":
            cleaned, removed = _remove_c_style_comments(content)
        elif language == "proc":
            cleaned, removed = _remove_c_style_comments(
                content,
                support_sql_dash=True,
            )
        elif language == "sql":
            cleaned, removed = _remove_sql_comments(content)
        elif language == "xml":
            cleaned, removed = _remove_xml_comments(content)
        else:
            cleaned, removed = content, 0

        # 줄번호 보존 검증
        cleaned_line_count = cleaned.count("\n")
        if cleaned_line_count != original_line_count:
            logger.warning(
                f"줄 수 불일치: 원본={original_line_count + 1}, "
                f"결과={cleaned_line_count + 1} (language={language})"
            )

        logger.debug(
            f"주석 제거 완료: language={language}, "
            f"removed={removed}건, lines={original_line_count + 1}"
        )

        return ToolResult(
            success=True,
            data={"content": cleaned, "removed_count": removed},
        )
