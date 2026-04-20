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
    functions = find_function_boundaries(lines, language)

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


def find_function_boundaries(
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
        # C / ProC — 한 줄 선언
        func_pattern = re.compile(
            r"^(?!\s*(?:if|else|for|while|switch|return|#|typedef|struct|union|enum)\b)"
            r"\s*(?:static\s+|extern\s+|inline\s+)*"
            r"(?:void|int|char|long|short|unsigned|float|double|size_t|ssize_t|\w+_t|\w+)\s*\*?\s+"
            r"\w+\s*\([^;]*$"
        )
        # C / ProC — 반환형만 있는 줄 (다음 줄에 함수명)
        return_type_only = re.compile(
            r"^\s*(?:static\s+|extern\s+|inline\s+)*"
            r"(?:void|int|char|long|short|unsigned|float|double|size_t|ssize_t|\w+_t)\s*\*?\s*$"
        )

    functions: list[tuple[int, int]] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        matched = func_pattern.match(line)

        # C/ProC: 반환형만 있는 줄 → 다음 줄과 합쳐서 매칭
        if not matched and language in ("c", "proc"):
            if return_type_only.match(line) and i + 1 < len(lines):
                combined = line.rstrip() + " " + lines[i + 1].lstrip()
                matched = func_pattern.match(combined)

        if matched:
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


def build_all_functions_summary(
    file_content: str,
    language: str,
) -> str:
    """전체 함수의 시그니처 + 위치 + 줄 수 요약을 생성한다.

    2-Pass 분석의 Pass 1에서 LLM이 전체 함수 목록을 파악하여
    regex 미히트 함수도 선별할 수 있도록 전체 함수 정보를 제공한다.

    Args:
        file_content: 파일 전체 내용
        language: 파일 언어 ("c", "proc", "javascript")

    Returns:
        함수 요약 문자열. 예:
        [L142-L268] int c400_get_rcv(...) — 127줄
    """
    lines = file_content.splitlines()
    boundaries = find_function_boundaries(lines, language)

    if not boundaries:
        return "(함수 없음)"

    # 함수 시그니처 추출 (선언 라인에서)
    func_sig_pattern = re.compile(
        r"^(?!\s*(?:if|else|for|while|switch|return|#|typedef|struct|union|enum)\b)"
        r"\s*((?:static\s+|extern\s+|inline\s+)*"
        r"(?:void|int|char|long|short|unsigned|float|double|size_t|ssize_t|\w+_t|\w+)\s*\*?\s+"
        r"\w+\s*\([^;{]*\))"
    ) if language in ("c", "proc") else re.compile(
        r"^\s*(.+)"
    )

    parts: list[str] = []
    for start, end in boundaries:
        line_count = end - start + 1
        idx = start - 1  # 1-based → 0-based
        sig_line = lines[idx].strip()

        # C/ProC: 2줄 선언 합치기
        if language in ("c", "proc"):
            m = func_sig_pattern.match(lines[idx])
            if not m and idx + 1 < len(lines):
                sig_line = lines[idx].rstrip() + " " + lines[idx + 1].lstrip()
                sig_line = sig_line.strip()
            elif m:
                sig_line = m.group(1).strip()

        # 긴 시그니처 축약: 파라미터를 "..."으로
        if len(sig_line) > 80:
            paren_start = sig_line.find("(")
            if paren_start > 0:
                sig_line = sig_line[:paren_start] + "(...)"

        parts.append(f"[L{start}~L{end}] {sig_line} — {line_count}줄")

    return "\n".join(parts)


def classify_proc_functions(
    file_content: str,
    boundaries: list[tuple[int, int]],
    func_names: dict[int, str],
    *,
    hard_cap_lines: int = 1200,
) -> dict[str, list]:
    """ProC 함수를 패턴별로 분류한다.

    분류 규칙:
    - boilerplate: main, *_init_proc, *_exit_proc, 모듈명 함수
    - dispatch: 숫자 접두사 함수 + 동일 접두사+번호 함수 → 줄 수 기반 그룹핑
    - utility_groups: z/s/rep 접두사별 그룹

    Args:
        file_content: 파일 전체 내용
        boundaries: 함수 경계 리스트 [(start, end), ...]
        func_names: {start_line: func_name} 딕셔너리

    Returns:
        {
            "boilerplate": [func_name, ...],
            "dispatch": [func_name, ...],
            "dispatch_groups": [[func_name, ...], ...],
            "utility_groups": [[func_name, ...], ...],
        }
    """
    all_names = [func_names[start] for start, _end in boundaries if start in func_names]

    boilerplate: list[str] = []
    utility: dict[str, list[str]] = {}  # prefix → [func_names]
    dispatch: list[str] = []
    remaining: list[str] = []

    # 숫자 접두사 패턴: a00_, b10_, c100_, z99_ 등
    num_prefix_pat = re.compile(r"^([a-z])(\d{2,3})_")
    # 디스패치 패턴: xxx_work_procN, xxx_ins_xxx_guid, xxx_insert_xxx_yyy 등
    dispatch_pat = re.compile(r"^(\w+?)(\d+)$")

    for name in all_names:
        lower = name.lower()

        # 1. 보일러플레이트
        if lower == "main" or lower.endswith("_init_proc") or lower.endswith("_exit_proc"):
            boilerplate.append(name)
            continue
        # 모듈명 함수 (파일명과 동일한 함수: zordbs0600450, zinvbprt23000 등)
        if re.match(r"^z\w{3}b\w+\d+$", lower):
            boilerplate.append(name)
            continue

        # 2. 숫자 접두사 (a00, b10, c100, z99 등)
        m = num_prefix_pat.match(lower)
        if m:
            letter = m.group(1)
            if letter in ("z", "s"):
                # z/s 계열은 유틸 그룹
                utility.setdefault(letter, []).append(name)
            else:
                # 계층형이었던 함수들 → dispatch로 통합
                dispatch.append(name)
            continue

        # 3. rep_ 접두사 → 유틸
        if lower.startswith("rep_"):
            utility.setdefault("rep", []).append(name)
            continue

        # 4. 나머지: 디스패치 패턴 감지 (동일 접두사+번호)
        remaining.append(name)

    # remaining에서 디스패치형 분류: 접두사가 같고 숫자 접미사가 있는 함수들
    prefix_groups: dict[str, list[str]] = {}
    non_dispatch: list[str] = []
    for name in remaining:
        m = dispatch_pat.match(name)
        if m:
            prefix = m.group(1)
            prefix_groups.setdefault(prefix, []).append(name)
        else:
            non_dispatch.append(name)

    for prefix, funcs in prefix_groups.items():
        if len(funcs) >= 3:
            # 3개 이상 동일 접두사+번호 → 디스패치형
            dispatch.extend(funcs)
        else:
            # 2개 이하는 그냥 개별 분석
            dispatch.extend(funcs)

    # non_dispatch도 개별 분석
    dispatch.extend(non_dispatch)

    # utility_groups: 단일 함수 그룹은 dispatch로 이동
    utility_groups: list[list[str]] = []
    for _key, funcs in sorted(utility.items()):
        if len(funcs) >= 2:
            utility_groups.append(funcs)
        else:
            dispatch.extend(funcs)

    # dispatch를 줄 수 기반 그룹으로 묶기
    dispatch_groups = group_dispatch_functions(
        dispatch, boundaries, func_names,
        hard_cap=hard_cap_lines,
    )

    return {
        "boilerplate": boilerplate,
        "dispatch": dispatch,
        "dispatch_groups": dispatch_groups,
        "utility_groups": utility_groups,
    }


# ──────────────────────────────────────────────
# dispatch 줄 수 기반 그룹핑
# ──────────────────────────────────────────────

_DISPATCH_GROUP_HARD_CAP = 1200  # 기본 허용 상한 (settings.yaml로 오버라이드 가능)


def group_dispatch_functions(
    dispatch_names: list[str],
    boundaries: list[tuple[int, int]],
    func_names: dict[int, str],
    *,
    hard_cap: int = _DISPATCH_GROUP_HARD_CAP,
) -> list[list[str]]:
    """dispatch 함수들을 줄 수 기준으로 큰 그룹으로 묶는다.

    정책:
    - 함수를 원래 순서대로 순회하면서 그룹에 추가
    - 현재 그룹 + 다음 함수 ≤ hard_cap → 추가
    - 현재 그룹 + 다음 함수 > hard_cap → 현재 그룹 확정, 새 그룹 시작
    - 단독으로 hard_cap 초과 함수 → 단독 그룹

    Args:
        dispatch_names: dispatch로 분류된 함수명 리스트
        boundaries: 함수 경계 리스트 [(start, end), ...]
        func_names: {start_line: func_name}
        hard_cap: 그룹 허용 상한 줄 수 (기본 1200)

    Returns:
        [[func_name, ...], ...] 그룹 리스트
    """
    if not dispatch_names:
        return []

    # 함수별 줄 수 맵 (원래 순서 유지)
    func_line_counts: dict[str, int] = {}
    for start, end in boundaries:
        name = func_names.get(start)
        if name and name in set(dispatch_names):
            func_line_counts[name] = end - start + 1

    # 원래 순서 유지하면서 그룹핑
    groups: list[list[str]] = []
    current_group: list[str] = []
    current_lines = 0

    for name in dispatch_names:
        func_lines = func_line_counts.get(name, 0)
        if func_lines == 0:
            continue

        if not current_group:
            # 새 그룹 시작
            current_group.append(name)
            current_lines = func_lines
        elif current_lines + func_lines <= hard_cap:
            # 상한 이내 → 추가
            current_group.append(name)
            current_lines += func_lines
        else:
            # 상한 초과 → 현재 그룹 확정, 새 그룹 시작
            groups.append(current_group)
            current_group = [name]
            current_lines = func_lines

    if current_group:
        groups.append(current_group)

    return groups


def extract_proc_global_context(file_content: str) -> str:
    """Pro*C 파일에서 글로벌 컨텍스트를 추출한다.

    추출 대상:
    - EXEC SQL BEGIN/END DECLARE SECTION (호스트 변수)
    - #include / EXEC SQL INCLUDE 목록
    - typedef / struct 정의 (함수 밖)
    - 함수 밖 전역 변수 선언

    Args:
        file_content: 파일 전체 내용

    Returns:
        글로벌 컨텍스트 문자열
    """
    lines = file_content.splitlines()
    parts: list[str] = []

    # 1. #include / EXEC SQL INCLUDE
    includes: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#include"):
            includes.append(stripped)
        elif re.match(r"EXEC\s+SQL\s+INCLUDE\b", stripped, re.IGNORECASE):
            includes.append(stripped.rstrip(";").strip() + ";")
    if includes:
        parts.append("\n".join(includes))

    # 2. EXEC SQL BEGIN/END DECLARE SECTION 블록
    declare_blocks: list[str] = []
    in_declare = False
    current_block: list[str] = []
    for line in lines:
        stripped = line.strip()
        if re.match(r"EXEC\s+SQL\s+BEGIN\s+DECLARE\s+SECTION", stripped, re.IGNORECASE):
            in_declare = True
            current_block = [stripped]
            continue
        if in_declare:
            current_block.append(line.rstrip())
            if re.match(r"EXEC\s+SQL\s+END\s+DECLARE\s+SECTION", stripped, re.IGNORECASE):
                in_declare = False
                declare_blocks.append("\n".join(current_block))
                current_block = []
    if declare_blocks:
        parts.append("\n\n".join(declare_blocks))

    # 함수 경계 계산 (3, 4번 공통)
    boundaries = find_function_boundaries(lines, "proc")
    func_ranges: set[int] = set()
    for start, end in boundaries:
        func_ranges.update(range(start, end + 1))

    # 3. typedef / struct 정의 (함수 밖)
    typedef_lines: list[str] = []
    for i, line in enumerate(lines):
        if (i + 1) in func_ranges:
            continue
        stripped = line.strip()
        if stripped.startswith("typedef ") or re.match(r"^struct\s+\w+", stripped):
            typedef_lines.append(stripped)
    if typedef_lines:
        parts.append("\n".join(typedef_lines[:20]))

    global_pattern = re.compile(
        r"^(?:static\s+|extern\s+)?(?:const\s+)?"
        r"(?:int|char|long|short|unsigned|float|double|size_t|\w+_t)\s+"
        r"\w+\s*(?:=|;|\[)"
    )
    global_vars: list[str] = []
    for i, line in enumerate(lines):
        line_num = i + 1  # 1-based
        if line_num in func_ranges:
            continue
        if global_pattern.match(line):
            global_vars.append(line.rstrip())
    if global_vars:
        parts.append("\n".join(global_vars[:30]))

    if not parts:
        return "(글로벌 컨텍스트 없음)"

    return "\n\n".join(parts)


def build_cursor_lifecycle_map(file_content: str) -> str:
    """Pro*C 파일에서 커서 라이프사이클 맵을 생성한다.

    EXEC SQL 구문에서 커서명을 추적하여 DECLARE/OPEN/FETCH/CLOSE
    위치와 함수명을 매핑한다.

    Args:
        file_content: 파일 전체 내용

    Returns:
        커서 라이프사이클 요약 문자열
    """
    lines = file_content.splitlines()
    boundaries = find_function_boundaries(lines, "proc")

    # 함수명 추출용 시그니처 패턴
    func_sig_pattern = re.compile(
        r"^(?!\s*(?:if|else|for|while|switch|return|#|typedef|struct|union|enum)\b)"
        r"\s*(?:static\s+|extern\s+|inline\s+)*"
        r"(?:void|int|char|long|short|unsigned|float|double|size_t|ssize_t|\w+_t|\w+)\s*\*?\s+"
        r"(\w+)\s*\("
    )

    def _get_func_name(line_num: int) -> str:
        """라인 번호에 해당하는 함수명을 반환한다."""
        for start, end in boundaries:
            if start <= line_num <= end:
                idx = start - 1
                m = func_sig_pattern.match(lines[idx])
                if m:
                    return m.group(1)
                # 2줄 선언
                if idx + 1 < len(lines):
                    combined = lines[idx].rstrip() + " " + lines[idx + 1].lstrip()
                    m = func_sig_pattern.match(combined)
                    if m:
                        return m.group(1)
                return f"(L{start})"
        return "(global)"

    # 커서 이벤트 패턴
    declare_pat = re.compile(
        r"EXEC\s+SQL\s+DECLARE\s+(\w+)\s+CURSOR", re.IGNORECASE,
    )
    open_pat = re.compile(r"EXEC\s+SQL\s+OPEN\s+(\w+)", re.IGNORECASE)
    fetch_pat = re.compile(r"EXEC\s+SQL\s+FETCH\s+(\w+)", re.IGNORECASE)
    close_pat = re.compile(r"EXEC\s+SQL\s+CLOSE\s+(\w+)", re.IGNORECASE)

    # cursor_name → {DECLARE: [...], OPEN: [...], FETCH: [...], CLOSE: [...]}
    cursors: dict[str, dict[str, list[tuple[int, str]]]] = {}

    for i, line in enumerate(lines):
        line_num = i + 1

        for pat, event in [
            (declare_pat, "DECLARE"),
            (open_pat, "OPEN"),
            (fetch_pat, "FETCH"),
            (close_pat, "CLOSE"),
        ]:
            m = pat.search(line)
            if m:
                cursor_name = m.group(1)
                func_name = _get_func_name(line_num)
                if cursor_name not in cursors:
                    cursors[cursor_name] = {
                        "DECLARE": [], "OPEN": [], "FETCH": [], "CLOSE": [],
                    }
                cursors[cursor_name][event].append((line_num, func_name))

    if not cursors:
        return "(커서 없음)"

    parts: list[str] = []
    for cursor_name, events in sorted(cursors.items()):
        part_lines = [f"{cursor_name}:"]
        for event in ["DECLARE", "OPEN", "FETCH", "CLOSE"]:
            locations = events[event]
            if locations:
                loc_strs = [f"{func} (L{ln})" for ln, func in locations]
                part_lines.append(f"  {event:7s} → {', '.join(loc_strs)}")
            else:
                part_lines.append(f"  {event:7s} → ⚠ 미발견")
        parts.append("\n".join(part_lines))

    return "\n\n".join(parts)


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


def split_js_into_chunks(
    file_content: str,
    target_lines: int = 2000,
    hard_cap_lines: int = 2400,
) -> list[tuple[str, int, int]]:
    """JS 파일을 함수 경계를 고려하여 청크로 분할한다.

    함수 중간에서 잘리지 않도록 함수 끝 경계를 우선 탐색한다.
    target_lines 근처에서 함수 경계를 찾지 못하면 hard_cap_lines까지 확장한다.

    Args:
        file_content: 파일 전체 내용
        target_lines: 목표 청크 크기 (줄)
        hard_cap_lines: 허용 상한 (줄)

    Returns:
        [(chunk_code, start_line, end_line), ...]  (1-based line numbers)
    """
    lines = file_content.splitlines()
    total = len(lines)

    if total <= hard_cap_lines:
        return [(file_content, 1, total)]

    # 함수 경계 탐색 → 함수 끝 라인 집합 (좋은 분할 지점)
    boundaries = find_function_boundaries(lines, "javascript")
    func_ends = {end for _, end in boundaries}

    chunks: list[tuple[str, int, int]] = []
    i = 0  # 0-based index

    while i < total:
        # 마지막 청크: 남은 줄이 hard_cap 이내면 한꺼번에
        if total - i <= hard_cap_lines:
            chunks.append(("\n".join(lines[i:]), i + 1, total))
            break

        # target 지점부터 함수 끝 경계 탐색
        best = i + target_lines
        found = False
        for j in range(i + target_lines, min(i + hard_cap_lines, total)):
            if (j + 1) in func_ends:  # j는 0-based, func_ends는 1-based
                best = j + 1  # 함수 끝 라인 포함
                found = True
                break

        # 함수 경계를 못 찾으면 target에서 빈 줄 탐색 (함수 없는 코드)
        if not found:
            for j in range(i + target_lines, min(i + hard_cap_lines, total)):
                if not lines[j].strip():
                    best = j + 1
                    break

        chunks.append(("\n".join(lines[i:best]), i + 1, best))
        i = best

    return chunks


def build_datalist_summary(data_lists: list[dict]) -> str:
    """dataList 전체를 이름+컬럼수 요약으로 변환한다.

    ~23K 토큰의 전체 dataList JSON 대신 ~2K 토큰 요약을 생성한다.

    Args:
        data_lists: XMLParser가 추출한 data_lists 리스트

    Returns:
        요약 문자열
    """
    if not data_lists:
        return "[dataList 요약] 없음"

    lines = [f"[dataList 요약] 총 {len(data_lists)}개"]
    for dl in data_lists:
        dl_id = dl.get("id", "(no id)")
        col_count = len(dl.get("columns", []))
        lines.append(f"  {dl_id}: {col_count} columns")

    return "\n".join(lines)
