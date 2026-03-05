"""TokenOptimizer: LLM 토큰 최적화 유틸리티.

대형 파일에서 에러 관련 함수만 추출하거나 구조 요약을 생성하여
LLM에 전달하는 토큰 수를 줄인다.
"""

import re
from dataclasses import dataclass


@dataclass
class CodeBlock:
    """추출된 코드 블록."""

    content: str
    line_start: int
    line_end: int


def extract_error_functions(
    file_content: str,
    error_lines: list[int],
    language: str,
) -> list[CodeBlock]:
    """에러가 포함된 함수 전체를 추출한다.

    Args:
        file_content: 파일 전체 내용
        error_lines: 에러가 발생한 라인 번호 리스트 (1-based)
        language: 파일 언어 ("javascript", "c", "proc", "sql")

    Returns:
        추출된 CodeBlock 리스트 (라인 범위 포함)
    """
    if not file_content or not error_lines:
        return []

    if language == "sql":
        return _extract_sql_statements(file_content, error_lines)

    return _extract_function_blocks(file_content, error_lines, language)


def _extract_function_blocks(
    file_content: str,
    error_lines: list[int],
    language: str,
) -> list[CodeBlock]:
    """JS/C/ProC 파일에서 에러 포함 함수를 추출한다."""
    lines = file_content.splitlines()
    total_lines = len(lines)

    # 함수 경계 탐색
    functions = _find_function_boundaries(lines, language)

    blocks: list[CodeBlock] = []
    covered_lines: set[int] = set()

    for error_line in sorted(set(error_lines)):
        if error_line in covered_lines:
            continue

        # 에러를 포함하는 함수 찾기
        enclosing = None
        for func_start, func_end in functions:
            if func_start <= error_line <= func_end:
                enclosing = (func_start, func_end)
                break

        if enclosing:
            start, end = enclosing
        else:
            # 함수 밖 에러: ±20줄 추출
            start = max(1, error_line - 20)
            end = min(total_lines, error_line + 20)

        # 이미 커버된 범위면 건너뛰기
        block_range = set(range(start, end + 1))
        if block_range.issubset(covered_lines):
            continue

        covered_lines.update(block_range)
        block_content = "\n".join(lines[start - 1:end])
        blocks.append(CodeBlock(
            content=block_content,
            line_start=start,
            line_end=end,
        ))

    return blocks


def _count_braces_in_line(line: str, count_ref: list[int]) -> None:
    """한 줄에서 문자열/주석을 무시하고 중괄호를 세어 count_ref[0]을 갱신한다."""
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        # 한 줄 주석 (//) → 나머지 무시
        if ch == "/" and i + 1 < n and line[i + 1] == "/":
            break
        # 문자열 리터럴 건너뛰기
        if ch in ('"', "'"):
            quote = ch
            i += 1
            while i < n and line[i] != quote:
                if line[i] == "\\":
                    i += 1  # 이스케이프 건너뛰기
                i += 1
            i += 1  # 닫는 따옴표
            continue
        if ch == "{":
            count_ref[0] += 1
        elif ch == "}":
            count_ref[0] -= 1
        i += 1


def _find_function_boundaries(
    lines: list[str],
    language: str,
) -> list[tuple[int, int]]:
    """함수의 시작/끝 라인을 찾는다 (1-based).

    중괄호 기반 언어(JS/C/ProC)에서 함수 선언을 찾고
    중괄호 매칭으로 함수 끝을 결정한다.
    """
    # 함수 선언 패턴 (JS: function/=>/class method, C/ProC: 반환형 함수명(...))
    if language == "javascript":
        func_pattern = re.compile(
            r"^\s*(?:"
            r"(?:async\s+)?function\s+\w+|"  # function name
            r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\(|"  # export function(
            r"(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?(?:function|\(.*?\)\s*=>|\w+\s*=>)|"  # arrow
            r"(?!(?:if|else|for|while|switch|return|catch|try|do)\s*\()\w+\s*\(.*?\)\s*\{|"  # method (제어문 제외)
            r"(?:get|set)\s+\w+\s*\("  # getter/setter
            r")"
        )
    else:
        # C / ProC
        func_pattern = re.compile(
            r"^(?!\s*(?:if|else|for|while|switch|return|#|typedef|struct|union|enum)\b)"
            r"\s*(?:static\s+|extern\s+|inline\s+)*"
            r"(?:void|int|char|long|short|unsigned|float|double|size_t|ssize_t|\w+_t|\w+)\s*\*?\s+"
            r"\w+\s*\([^;]*$"
        )

    functions: list[tuple[int, int]] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        if func_pattern.match(line):
            func_start = i + 1  # 1-based

            # 중괄호 시작을 찾기
            brace_count = 0
            found_brace = False
            j = i

            while j < len(lines):
                _count_braces_in_line(lines[j], brace_count_ref := [brace_count])
                brace_count = brace_count_ref[0]
                if not found_brace and brace_count > 0:
                    found_brace = True

                if found_brace and brace_count == 0:
                    functions.append((func_start, j + 1))
                    i = j
                    break
                j += 1
            else:
                # 중괄호 매칭 실패: 선언만 있는 경우
                pass
        i += 1

    return functions


def _extract_sql_statements(
    file_content: str,
    error_lines: list[int],
) -> list[CodeBlock]:
    """SQL 파일에서 에러 포함 SQL 문을 추출한다.

    SQL 문은 SELECT/INSERT/UPDATE/DELETE/MERGE/CREATE 키워드로 시작하여
    세미콜론(;)으로 끝나는 단위이다.
    """
    lines = file_content.splitlines()
    total_lines = len(lines)

    # SQL 문 경계 탐색
    statements = _find_sql_statement_boundaries(lines)

    blocks: list[CodeBlock] = []
    covered_lines: set[int] = set()

    for error_line in sorted(set(error_lines)):
        if error_line in covered_lines:
            continue

        enclosing = None
        for stmt_start, stmt_end in statements:
            if stmt_start <= error_line <= stmt_end:
                enclosing = (stmt_start, stmt_end)
                break

        if enclosing:
            start, end = enclosing
        else:
            start = max(1, error_line - 20)
            end = min(total_lines, error_line + 20)

        block_range = set(range(start, end + 1))
        if block_range.issubset(covered_lines):
            continue

        covered_lines.update(block_range)
        block_content = "\n".join(lines[start - 1:end])
        blocks.append(CodeBlock(
            content=block_content,
            line_start=start,
            line_end=end,
        ))

    return blocks


def _find_sql_statement_boundaries(
    lines: list[str],
) -> list[tuple[int, int]]:
    """SQL 문의 시작/끝 라인을 찾는다 (1-based)."""
    sql_start_pattern = re.compile(
        r"^\s*(?:SELECT|INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|DECLARE|BEGIN)\b",
        re.IGNORECASE,
    )

    statements: list[tuple[int, int]] = []
    current_start: int | None = None

    for i, line in enumerate(lines):
        stripped = line.strip()

        if current_start is None and sql_start_pattern.match(stripped):
            current_start = i + 1  # 1-based

        if current_start is not None and stripped.endswith(";"):
            statements.append((current_start, i + 1))
            current_start = None

    # 파일 끝까지 닫히지 않은 문
    if current_start is not None:
        statements.append((current_start, len(lines)))

    return statements


def build_structure_summary(
    file_content: str,
    file_context: dict | None,
    language: str,
) -> str:
    """파일의 구조 요약을 생성한다.

    Args:
        file_content: 파일 전체 내용
        file_context: Phase 1에서 수집한 파일 컨텍스트 (imports, calls, patterns 등)
        language: 파일 언어

    Returns:
        구조 요약 문자열
    """
    parts: list[str] = []

    # 1. 파일 기본 정보
    line_count = len(file_content.splitlines())
    parts.append(f"[파일 정보] {line_count}줄, 언어: {language}")

    # 2. Import/Include 정보
    if file_context:
        imports = file_context.get("imports", [])
        if imports:
            import_strs = []
            for imp in imports[:20]:
                if isinstance(imp, dict):
                    import_strs.append(imp.get("module", str(imp)))
                else:
                    import_strs.append(str(imp))
            parts.append(f"[Import] {', '.join(import_strs)}")

        # 3. 함수 호출 관계
        calls = file_context.get("calls", [])
        if calls:
            call_strs = []
            for call in calls[:20]:
                if isinstance(call, dict):
                    call_strs.append(call.get("function", str(call)))
                else:
                    call_strs.append(str(call))
            parts.append(f"[호출 관계] {', '.join(call_strs)}")

        # 4. 공통 패턴
        patterns = file_context.get("common_patterns", {})
        if isinstance(patterns, dict) and patterns:
            pattern_strs = [f"{k}({v})" for k, v in patterns.items() if v]
            if pattern_strs:
                parts.append(f"[공통 패턴] {', '.join(pattern_strs)}")

    # 5. 함수 시그니처 추출
    signatures = _extract_function_signatures(file_content, language)
    if signatures:
        parts.append("[함수 목록]")
        for sig in signatures[:30]:
            parts.append(f"  - {sig}")

    # 6. 전역 변수 추출
    globals_list = _extract_globals(file_content, language)
    if globals_list:
        parts.append(f"[전역 변수] {', '.join(globals_list[:20])}")

    return "\n".join(parts)


def _extract_function_signatures(
    file_content: str,
    language: str,
) -> list[str]:
    """파일에서 함수 시그니처를 추출한다."""
    signatures: list[str] = []
    lines = file_content.splitlines()

    if language == "javascript":
        pattern = re.compile(
            r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
            r"function\s+(\w+)\s*\(([^)]*)\)"
        )
        arrow_pattern = re.compile(
            r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*"
            r"(?:async\s+)?(?:\(([^)]*)\)\s*=>|\w+\s*=>)"
        )
        method_pattern = re.compile(
            r"^\s*(?:async\s+)?(\w+)\s*\(([^)]*)\)\s*\{"
        )

        for line in lines:
            m = pattern.match(line)
            if m:
                signatures.append(f"function {m.group(1)}({m.group(2)})")
                continue
            m = arrow_pattern.match(line)
            if m:
                params = m.group(2) or ""
                signatures.append(f"const {m.group(1)} = ({params}) =>")
                continue
            m = method_pattern.match(line)
            if m and m.group(1) not in ("if", "else", "for", "while", "switch", "return", "catch", "try"):
                signatures.append(f"{m.group(1)}({m.group(2)})")

    elif language in ("c", "proc"):
        pattern = re.compile(
            r"^(?!\s*(?:if|else|for|while|switch|return|#|typedef|struct|union|enum)\b)"
            r"\s*((?:static\s+|extern\s+|inline\s+)*"
            r"(?:void|int|char|long|short|unsigned|float|double|size_t|ssize_t|\w+_t|\w+)\s*\*?\s+"
            r"\w+\s*\([^;{]*\))"
        )
        for line in lines:
            m = pattern.match(line)
            if m:
                sig = m.group(1).strip()
                signatures.append(sig)

    elif language == "sql":
        pattern = re.compile(
            r"^\s*(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE)\s+(\w+))",
            re.IGNORECASE,
        )
        for line in lines:
            m = pattern.match(line)
            if m:
                signatures.append(m.group(0).strip())

    return signatures


def _extract_globals(
    file_content: str,
    language: str,
) -> list[str]:
    """파일에서 전역 변수를 추출한다."""
    globals_list: list[str] = []
    lines = file_content.splitlines()

    if language == "javascript":
        pattern = re.compile(
            r"^(?:var|let|const)\s+(\w+)\s*="
        )
        for line in lines:
            m = pattern.match(line)
            if m:
                globals_list.append(m.group(1))

    elif language in ("c", "proc"):
        # 들여쓰기 없는 변수 선언 (함수 밖)
        pattern = re.compile(
            r"^(?:static\s+|extern\s+)?(?:const\s+)?"
            r"(?:int|char|long|short|unsigned|float|double|size_t|\w+_t)\s+"
            r"(\w+)\s*(?:=|;|\[)"
        )
        for line in lines:
            m = pattern.match(line)
            if m:
                globals_list.append(m.group(1))

    return globals_list


def optimize_file_content(
    file_content: str,
    file_context: dict | None,
    language: str,
) -> str:
    """Heuristic 경로용 파일 내용 최적화.

    ≤500줄: 전체 유지
    >500줄: head(200줄) + tail(100줄) + 구조요약

    Args:
        file_content: 파일 전체 내용
        file_context: Phase 1에서 수집한 파일 컨텍스트
        language: 파일 언어

    Returns:
        최적화된 파일 내용 문자열
    """
    lines = file_content.splitlines()
    total = len(lines)

    if total <= 500:
        return file_content

    head = "\n".join(lines[:200])
    tail = "\n".join(lines[total - 100:])
    summary = build_structure_summary(file_content, file_context, language)

    return (
        f"[파일 앞부분 1~200줄]\n"
        f"{head}\n\n"
        f"[... 중략 ({total - 300}줄 생략) ...]\n\n"
        f"[파일 끝부분 {total - 99}~{total}줄]\n"
        f"{tail}\n\n"
        f"[구조 요약]\n"
        f"{summary}"
    )
