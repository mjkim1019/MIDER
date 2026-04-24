"""cursor_close_scanner: cursor 중복 close 패턴 스캐너.

C와 Pro*C 코드에서 같은 cursor를 같은 함수 내에서 2회 이상 close하는
패턴을 탐지한다. .c (Pro*C 전처리 산출물 포함) 와 .pc 양쪽에서 공통 적용.

DBIO 매크로 방식(`mpfmdbio_cclose_ar("<cursor>")`)과
직접 EXEC SQL 방식(`EXEC SQL CLOSE <cursor>;`)을 모두 인식한다.

실사례:
- zinvmhot04020.c `b200_suces_pen_mth()`: 같은 커서를 3번 close
  - L515: 루프 내 RC_NFD 분기에서 close
  - L535: 루프 내 다른 분기에서 close
  - L583: 루프 종료 후 무조건 close → 런타임 에러 가능
"""

import re
from typing import Any

from mider.tools.utility.token_optimizer import find_function_boundaries

# DBIO 매크로 방식: mpfmdbio_cclose_ar("cursor_name")
_DBIO_CLOSE_RE = re.compile(
    r'mpfmdbio_cclose_ar\s*\(\s*"([^"]+)"\s*\)'
)
# 직접 EXEC SQL: EXEC SQL CLOSE cursor_name;
_EXEC_SQL_CLOSE_RE = re.compile(
    r'EXEC\s+SQL\s+CLOSE\s+(\w+)',
    re.IGNORECASE,
)
# 함수명 추출: `(` 앞 마지막 식별자
_FUNC_NAME_RE = re.compile(r"([A-Za-z_]\w*)\s*\(")
# C 주석 제거용
_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/|//[^\n]*")


def _strip_comments(text: str) -> str:
    """블록 + 라인 C 주석 제거."""
    return _COMMENT_RE.sub("", text)


def _extract_function_name(
    lines: list[str], start_1based: int,
) -> str:
    """함수 선언부에서 함수명을 추출한다 (최대 3줄 합쳐서 스캔)."""
    start_idx = start_1based - 1
    header = " ".join(lines[start_idx:start_idx + 3])
    # `(` 앞 마지막 식별자 — 여러 매칭 중 `(`에 가장 가까운 것
    matches = list(_FUNC_NAME_RE.finditer(header))
    if not matches:
        return "<unknown>"
    # 가장 먼저 나오는 `(`의 바로 앞 식별자 = 함수명
    return matches[0].group(1)


def scan_cursor_duplicate_close(content: str, language: str = "c") -> list[dict[str, Any]]:
    """같은 함수 안에서 같은 cursor가 2회 이상 close되는 패턴을 탐지.

    Args:
        content: 소스 파일 전체 텍스트
        language: "c" 또는 "proc" — find_function_boundaries 규칙 선택

    Returns:
        findings 리스트. 각 항목:
            pattern_id="CURSOR_DUPLICATE_CLOSE", severity="high",
            line(첫 중복 지점, 1-based),
            variable(cursor 이름),
            function(함수명), code(대표 라인),
            description, all_lines(해당 cursor 전체 close 라인들, 1-based)
    """
    lines = content.splitlines()
    findings: list[dict[str, Any]] = []

    # 함수 경계 추출 (1-based (start, end))
    ranges = find_function_boundaries(lines, language)
    has_real_boundaries = bool(ranges)
    # 파일에 함수 경계가 하나도 탐지 안 되면 파일 전체를 단일 범위로 처리
    # (EXEC SQL 중심 파일이나 간단 스크립트 형태 방어)
    if not ranges:
        ranges = [(1, len(lines))]

    for start_1, end_1 in ranges:
        start_idx = start_1 - 1
        end_idx = end_1 - 1
        func_name = (
            _extract_function_name(lines, start_1)
            if has_real_boundaries
            else "<file-level>"
        )

        cursor_lines: dict[str, list[tuple[int, str]]] = {}
        for offset in range(start_idx, end_idx + 1):
            raw = lines[offset]
            stripped = _strip_comments(raw)
            abs_line = offset + 1  # 1-based
            for m in _DBIO_CLOSE_RE.finditer(stripped):
                cursor_lines.setdefault(m.group(1), []).append(
                    (abs_line, raw.strip()[:120])
                )
            for m in _EXEC_SQL_CLOSE_RE.finditer(stripped):
                cursor_lines.setdefault(m.group(1), []).append(
                    (abs_line, raw.strip()[:120])
                )

        for cursor, occurrences in cursor_lines.items():
            unique_lines = sorted({ln for ln, _ in occurrences})
            if len(unique_lines) < 2:
                continue
            first_line = unique_lines[0]
            first_code = next(code for ln, code in occurrences if ln == first_line)
            findings.append({
                "pattern_id": "CURSOR_DUPLICATE_CLOSE",
                "severity": "high",
                "line": first_line,
                "variable": cursor,
                "function": func_name,
                "code": first_code,
                "all_lines": unique_lines,
                "description": (
                    f"함수 {func_name}()에서 cursor {cursor}에 대한 close가 "
                    f"{len(unique_lines)}회 호출됨 (라인 {unique_lines}). "
                    "이미 close된 cursor를 다시 close하면 DB 에러 반환 또는 "
                    "예상치 못한 동작 유발 가능. 제어 흐름 확인 필요."
                ),
            })

    return findings
