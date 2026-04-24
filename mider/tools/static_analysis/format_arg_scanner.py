"""format_arg_scanner: printf 계열 format/arg 개수 불일치 스캐너.

s?n?printf/fprintf 호출의 format 문자열 내 변환 지정자 개수와
실제 인자 개수를 대조하여 불일치를 탐지한다.

동적 SQL 조립(여러 줄 문자열 연속 결합) 케이스를 정확히 처리하기 위해:
- C 주석(/* */, //) 제거
- 문자열 내부와 밖을 구분하는 문자 단위 파서
- 괄호 depth 추적으로 top-level 콤마만 인자 구분자로 사용
- 인접 문자열 리터럴 자동 concatenation

실사례 (zinvbprt10130.pc, 2025-12-17 배포):
- prt_insert_proc() snprintf: %ld 27개 vs 인자 26개 → off-by-one
"""

import re
from typing import Any

# printf 계열 함수 호출 탐지
_PRINTF_CALL_RE = re.compile(
    r"\b(sprintf|snprintf|fprintf|vsprintf|vsnprintf)\s*\("
)
# C 변환 지정자 — %% 제외
# flags: -+ #0  width: 숫자|* 또는 .숫자|.*  length: lL/hh/j/z/t  conversion 문자
_FORMAT_SPEC_RE = re.compile(
    r"%"
    r"[-+ #0]*"                            # flags
    r"(?:\d+|\*)?"                          # width
    r"(?:\.\d+|\.\*)?"                      # precision
    r"(?:hh|ll|[hljzt])?"                   # length modifier
    r"[diouxXeEfFgGaAcspn]"                 # conversion
)


def _find_call_end(content: str, open_idx: int) -> int:
    """content[open_idx]가 `(`일 때 짝이 되는 `)`의 인덱스를 반환.

    문자열/주석/이스케이프를 정확히 건너뛴다. 실패 시 -1.
    """
    depth = 1
    i = open_idx + 1
    in_string = False
    in_line_comment = False
    in_block_comment = False
    escape = False
    n = len(content)
    while i < n:
        ch = content[i]
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and i + 1 < n and content[i + 1] == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if escape:
            escape = False
            i += 1
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        # outside string/comment
        if ch == '"':
            in_string = True
        elif ch == "/" and i + 1 < n:
            nxt = content[i + 1]
            if nxt == "/":
                in_line_comment = True
                i += 2
                continue
            if nxt == "*":
                in_block_comment = True
                i += 2
                continue
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_top_level_args(call_body: str) -> list[str]:
    """함수 호출 본문(괄호 내부)을 top-level 콤마로 분할.

    문자열과 중첩 괄호 안의 콤마는 분리자로 보지 않는다.
    주석은 제거된 상태로 들어온다고 가정.
    """
    args: list[str] = []
    depth = 0
    in_string = False
    escape = False
    current: list[str] = []
    for ch in call_body:
        if escape:
            current.append(ch)
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
                current.append(ch)
                continue
            if ch == '"':
                in_string = False
            current.append(ch)
            continue
        if ch == '"':
            in_string = True
            current.append(ch)
            continue
        if ch in "({[":
            depth += 1
            current.append(ch)
        elif ch in ")}]":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        args.append("".join(current))
    return [a.strip() for a in args]


def _concat_string_literals(s: str) -> str | None:
    """`"abc" "def"` 형태의 인접 문자열 리터럴을 하나로 합친다.

    주어진 인자 전체가 문자열 리터럴 연속(+ 공백)이면 합친 문자열을,
    그게 아니면 None을 반환한다.
    """
    chunks: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch in " \t\n\r":
            i += 1
            continue
        if ch != '"':
            return None  # 리터럴이 아닌 토큰 발견 (변수 등)
        # 리터럴 파싱
        i += 1
        buf: list[str] = []
        while i < n:
            c = s[i]
            if c == "\\" and i + 1 < n:
                buf.append(s[i + 1])  # 내용만 취함(%%에서 %는 살아남음)
                i += 2
                continue
            if c == '"':
                i += 1
                break
            buf.append(c)
            i += 1
        chunks.append("".join(buf))
    return "".join(chunks) if chunks else None


def _count_format_specs(fmt: str) -> int:
    """format 문자열에서 `%%`를 제외한 변환 지정자 개수를 센다."""
    # %% 먼저 제거
    cleaned = fmt.replace("%%", "")
    return len(_FORMAT_SPEC_RE.findall(cleaned))


def _strip_c_comments(src: str) -> str:
    """블록/라인 C 주석 제거."""
    return re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", src)


def _line_of_offset(content: str, offset: int) -> int:
    """content의 offset 위치에 해당하는 1-based 라인 번호."""
    return content.count("\n", 0, offset) + 1


def scan_format_arg_mismatch(content: str) -> list[dict[str, Any]]:
    """s?n?printf/fprintf 호출의 format 지정자와 인자 개수 불일치를 탐지.

    Returns:
        findings 리스트:
            pattern_id="FORMAT_ARG_MISMATCH", severity="critical",
            line(함수 호출 시작 라인), function_call(예: "snprintf"),
            format_count, arg_count, code(호출 첫 줄 요약), description
    """
    findings: list[dict[str, Any]] = []

    # 주석 제거한 content로 호출 찾기 — 단, 원본 offset을 보존하기 위해
    # 제거된 영역은 공백으로 치환 (길이 유지). 문자열 리터럴 내부의 `/*+ */`
    # (Oracle SQL 힌트) 는 주석이 아니므로 건너뛴다.
    def _blank_comments(src: str) -> str:
        out: list[str] = []
        i = 0
        n = len(src)
        in_string = False
        in_char = False
        escape = False
        while i < n:
            ch = src[i]
            if escape:
                out.append(ch)
                escape = False
                i += 1
                continue
            if in_string:
                if ch == "\\":
                    escape = True
                    out.append(ch)
                    i += 1
                    continue
                if ch == '"':
                    in_string = False
                out.append(ch)
                i += 1
                continue
            if in_char:
                if ch == "\\":
                    escape = True
                    out.append(ch)
                    i += 1
                    continue
                if ch == "'":
                    in_char = False
                out.append(ch)
                i += 1
                continue
            # 문자열/문자 리터럴 바깥
            if ch == '"':
                in_string = True
                out.append(ch)
                i += 1
                continue
            if ch == "'":
                in_char = True
                out.append(ch)
                i += 1
                continue
            if ch == "/" and i + 1 < n and src[i + 1] == "*":
                j = src.find("*/", i + 2)
                end = j + 2 if j >= 0 else n
                # 개행만 보존, 나머지는 공백
                for c in src[i:end]:
                    out.append("\n" if c == "\n" else " ")
                i = end
                continue
            if ch == "/" and i + 1 < n and src[i + 1] == "/":
                j = src.find("\n", i)
                end = j if j >= 0 else n
                out.append(" " * (end - i))
                i = end
                continue
            out.append(ch)
            i += 1
        return "".join(out)

    cleaned = _blank_comments(content)

    for m in _PRINTF_CALL_RE.finditer(cleaned):
        func_name = m.group(1)
        open_paren_idx = cleaned.find("(", m.start())
        if open_paren_idx < 0:
            continue
        close_idx = _find_call_end(cleaned, open_paren_idx)
        if close_idx < 0:
            continue
        body = cleaned[open_paren_idx + 1:close_idx]
        args = _split_top_level_args(body)
        if not args:
            continue

        # format 인자의 위치: sprintf=1, fprintf=1, snprintf=2, vsnprintf=2
        fmt_idx = {
            "sprintf": 1, "fprintf": 1, "vsprintf": 1,
            "snprintf": 2, "vsnprintf": 2,
        }.get(func_name, -1)
        if fmt_idx < 0 or fmt_idx >= len(args):
            continue

        fmt_literal = _concat_string_literals(args[fmt_idx])
        if fmt_literal is None:
            # format이 변수/매크로이거나 리터럴이 아님 — 탐지 불가, 스킵
            continue

        fmt_count = _count_format_specs(fmt_literal)
        value_args = args[fmt_idx + 1:]
        # 마지막 인자가 빈 문자열이면(trailing comma 등) 제거
        value_args = [a for a in value_args if a.strip()]
        arg_count = len(value_args)

        if fmt_count == arg_count:
            continue

        call_line = _line_of_offset(content, m.start())
        # 호출 전체가 매우 길 수 있으므로 첫 줄만 요약에 사용
        first_line = content.splitlines()[call_line - 1].strip()[:120]
        findings.append({
            "pattern_id": "FORMAT_ARG_MISMATCH",
            "severity": "critical",
            "line": call_line,
            "function_call": func_name,
            "format_count": fmt_count,
            "arg_count": arg_count,
            "code": first_line,
            "description": (
                f"{func_name} 호출의 포맷 지정자({fmt_count}개)와 "
                f"실제 인자({arg_count}개) 개수 불일치. "
                "런타임에 정의되지 않은 동작(메모리 오염/잘못된 값 바인딩) 발생."
            ),
        })

    return findings
