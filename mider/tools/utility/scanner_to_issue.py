"""scanner_to_issue: 고신뢰도 scanner finding을 AnalysisResult Issue 스키마로 변환.

LLM 응답이 잘리거나 finding을 누락해도 scanner가 잡은 결정적 패턴은 최종 보고서에
반드시 포함되도록 하는 안전망. high-confidence 패턴 화이트리스트만 promotion한다.

LLM 결과와 합쳐 dedupe할 때 같은 (파일, 라인, 룰) 키가 충돌하면 source="static_analysis"
버전을 우선 유지한다.
"""

from typing import Any

# 직접 promotion할 high-confidence pattern_id 화이트리스트
# 각 패턴은 regex/로직이 정밀해서 finding == 진짜 버그에 가까움
_PROMOTABLE_PATTERNS: frozenset[str] = frozenset({
    "LOOP_INIT_MISSING",            # proc: 구조체별 초기화 누락 추적
    "CURSOR_DUPLICATE_CLOSE",       # proc/c: 같은 함수 같은 cursor 2회+ close
    "FORMAT_ARG_MISMATCH",          # proc/c: printf 포맷 수 vs 인자 수
    "FORMAT_STRUCT",                # proc: %s에 구조체 직접 전달 (Core Dump)
    # MEMSET_SIZEOF_MISMATCH / MEMSET_SIZE_MISMATCH는 ProFrame 명명규약(prefix 차이)
    # 때문에 이름 비교 휴리스틱이 false positive를 다수 만든다. 이미 scanner가
    # 선언 타입 lookup을 1차 필터링하지만 누락 가능성이 있어 LLM 검토를 거치도록
    # whitelist에서 제외 — scanner_findings를 통해 LLM에 finding은 그대로 전달됨.
})

_PATTERN_TO_CATEGORY: dict[str, str] = {
    "LOOP_INIT_MISSING": "data_integrity",
    "CURSOR_DUPLICATE_CLOSE": "error_handling",
    "FORMAT_ARG_MISMATCH": "memory_safety",
    "FORMAT_STRUCT": "memory_safety",
    "MEMSET_SIZEOF_MISMATCH": "memory_safety",
    "MEMSET_SIZE_MISMATCH": "memory_safety",
}

_PATTERN_TO_TITLE: dict[str, str] = {
    "LOOP_INIT_MISSING": "루프 내 구조체 초기화 누락",
    "CURSOR_DUPLICATE_CLOSE": "cursor 중복 close",
    "FORMAT_ARG_MISMATCH": "printf 포맷 지정자/인자 개수 불일치",
    "FORMAT_STRUCT": "%s 포맷에 구조체 직접 전달",
    "MEMSET_SIZEOF_MISMATCH": "memset 변수/sizeof 타입 불일치",
    "MEMSET_SIZE_MISMATCH": "memset 변수/sizeof 타입 불일치",
}


def is_promotable(pattern_id: str) -> bool:
    """해당 pattern_id가 직접 promotion 대상인지."""
    return pattern_id in _PROMOTABLE_PATTERNS


def _build_after(pid: str, finding: dict[str, Any]) -> str:
    """패턴별 수정 권고 텍스트."""
    var = finding.get("variable") or "<var>"
    if pid == "LOOP_INIT_MISSING":
        return (
            f"루프 시작에 INIT2VCHAR({var}); 또는 "
            f"memset(&{var}, 0, sizeof({var})); 추가"
        )
    if pid == "CURSOR_DUPLICATE_CLOSE":
        return (
            f"cursor {var}의 close 경로를 한 곳으로 통합 "
            "(조건문/break 정리하여 제어 흐름 단일화)"
        )
    if pid == "FORMAT_ARG_MISMATCH":
        return (
            f"포맷 지정자 {finding.get('format_count', '?')}개와 "
            f"실제 인자 {finding.get('arg_count', '?')}개를 일치시키도록 정렬"
        )
    if pid == "FORMAT_STRUCT":
        return "구조체 전체가 아닌 문자열 멤버(.field) 접근으로 변경"
    if pid in {"MEMSET_SIZEOF_MISMATCH", "MEMSET_SIZE_MISMATCH"}:
        return "변수의 실제 타입과 sizeof 타입을 일치시킴"
    return "코드 검토 필요"


def finding_to_issue(
    finding: dict[str, Any],
    *,
    file: str,
    issue_id: str,
    static_tool: str = "proc_heuristic",
) -> dict[str, Any] | None:
    """scanner finding을 Issue dict로 변환. promotable이 아니면 None."""
    pid = finding.get("pattern_id", "")
    if pid not in _PROMOTABLE_PATTERNS:
        return None

    line_start = int(finding.get("line", 0) or 0)
    # CURSOR_DUPLICATE_CLOSE는 all_lines로 범위 추정, 그 외는 단일 라인
    line_end = line_start
    all_lines = finding.get("all_lines")
    if isinstance(all_lines, list) and all_lines:
        line_end = max(int(x) for x in all_lines)

    title = _PATTERN_TO_TITLE[pid]
    var = finding.get("variable")
    if var:
        title = f"{title} ({var})"

    code_excerpt = (finding.get("code") or finding.get("content") or "")[:120]

    return {
        "issue_id": issue_id,
        "category": _PATTERN_TO_CATEGORY[pid],
        "severity": finding.get("severity", "medium"),
        "title": title,
        "description": finding.get("description", ""),
        "location": {
            "file": file,
            "line_start": line_start,
            "line_end": line_end,
        },
        "fix": {
            "before": code_excerpt or "(원본 코드 라인)",
            "after": _build_after(pid, finding),
            "description": _build_after(pid, finding),
        },
        "source": "static_analysis",
        "static_tool": static_tool,
        "static_rule": pid,
    }


def promote_findings(
    findings: list[dict[str, Any]],
    *,
    file: str,
    id_prefix: str = "PC-S",
    static_tool: str = "proc_heuristic",
) -> list[dict[str, Any]]:
    """findings 중 promotable한 것만 Issue 리스트로 변환한다."""
    issues: list[dict[str, Any]] = []
    counter = 0
    for f in findings:
        if not is_promotable(f.get("pattern_id", "")):
            continue
        counter += 1
        iid = f"{id_prefix}-{counter:03d}"
        issue = finding_to_issue(f, file=file, issue_id=iid, static_tool=static_tool)
        if issue:
            issues.append(issue)
    return issues


def dedupe_issues(
    issues: list[dict[str, Any]],
    *,
    prefer_static: bool = True,
) -> list[dict[str, Any]]:
    """중복 이슈 제거. 키: (file, line_start, static_rule 또는 정규화 title).

    같은 키가 충돌하면 prefer_static=True일 때 source="static_analysis" 우선,
    같은 source끼리는 먼저 등장한 항목 유지 (입력 순서 안정).
    """
    seen: dict[tuple[str, int, str], dict[str, Any]] = {}
    order: list[tuple[str, int, str]] = []
    for issue in issues:
        loc = issue.get("location", {}) or {}
        file = str(loc.get("file", ""))
        line = int(loc.get("line_start", 0) or 0)
        rule = issue.get("static_rule") or (issue.get("title", "")[:40])
        key = (file, line, rule)
        if key not in seen:
            seen[key] = issue
            order.append(key)
            continue
        if prefer_static:
            existing = seen[key]
            new_is_static = issue.get("source") == "static_analysis"
            old_is_static = existing.get("source") == "static_analysis"
            if new_is_static and not old_is_static:
                seen[key] = issue
    return [seen[k] for k in order]
