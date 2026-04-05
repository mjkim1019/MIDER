"""Markdown Report Formatter: JSON 분석 결과를 Markdown 리포트로 변환.

4개 JSON 결과(issue_list, checklist, summary, deployment_checklist)를
사람이 읽을 수 있는 Markdown 리포트 문자열로 변환한다.
LLM 호출 없이 순수 문자열 가공만 수행한다.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 심각도 정렬 우선순위
_SEVERITY_ORDER = ["critical", "high", "medium", "low"]

# 심각도 → Markdown 배지
_SEVERITY_BADGE: dict[str, str] = {
    "critical": "CRITICAL",
    "high": "HIGH",
    "medium": "MEDIUM",
    "low": "LOW",
}

# language → 코드블록 언어 힌트
_LANG_MAP: dict[str, str] = {
    "javascript": "javascript",
    "c": "c",
    "proc": "c",
    "sql": "sql",
    "xml": "xml",
}

# 카테고리 한국어 매핑
_CATEGORY_KR: dict[str, str] = {
    "memory_safety": "메모리 안전성",
    "null_safety": "NULL 안전성",
    "data_integrity": "데이터 무결성",
    "error_handling": "에러 처리",
    "security": "보안",
    "performance": "성능",
    "code_quality": "코드 품질",
}

# 배포 위험도 한국어
_RISK_KR: dict[str, str] = {
    "CRITICAL": "CRITICAL (즉시 조치 필요)",
    "HIGH": "HIGH (조치 권고)",
    "MEDIUM": "MEDIUM (검토 권고)",
    "LOW": "LOW (양호)",
    "UNABLE_TO_ANALYZE": "분석 불가",
}


def format_markdown_report(
    *,
    issue_list: dict[str, Any],
    checklist: dict[str, Any],
    summary: dict[str, Any],
    deployment_checklist: dict[str, Any],
    source_files: list[str],
    json_filenames: list[str],
) -> str:
    """4개 JSON 결과를 하나의 Markdown 리포트 문자열로 변환한다.

    Args:
        issue_list: issue-list.json 데이터
        checklist: checklist.json 데이터
        summary: summary.json 데이터
        deployment_checklist: deployment-checklist.json 데이터
        source_files: 분석 대상 파일 경로 리스트
        json_filenames: 생성된 JSON 파일명 리스트

    Returns:
        완성된 Markdown 문자열
    """
    sections = [
        _build_title_section(summary, source_files),
        _build_summary_section(summary),
        _build_critical_high_section(issue_list),
        _build_full_issue_list_section(issue_list),
        _build_checklist_section(checklist),
        _build_deployment_checklist_section(deployment_checklist),
        _build_metadata_footer(json_filenames, summary),
    ]
    return "\n".join(sections)


# ── 섹션 빌더 ──────────────────────────────────────


def _build_title_section(
    summary: dict[str, Any],
    source_files: list[str],
) -> str:
    """제목 + 분석 시각 + 분석 대상."""
    metadata = summary.get("analysis_metadata", {})
    analyzed_at = metadata.get("analyzed_at", "")
    timestamp = _format_timestamp(analyzed_at)

    lines = [
        "# Mider 분석 리포트",
        "",
        f"- **분석 시각**: {timestamp}",
        "- **분석 대상**:",
    ]

    if source_files:
        for f in source_files:
            lines.append(f"  - `{f}`")
    else:
        lines.append("  - (없음)")

    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _build_summary_section(summary: dict[str, Any]) -> str:
    """분석 요약 테이블 + risk description."""
    metadata = summary.get("analysis_metadata", {})
    issue_summary = summary.get("issue_summary", {})
    risk = summary.get("risk_assessment", {})
    by_severity = issue_summary.get("by_severity", {})

    total = issue_summary.get("total", 0)
    critical = by_severity.get("critical", 0)
    high = by_severity.get("high", 0)
    medium = by_severity.get("medium", 0)
    low = by_severity.get("low", 0)

    deployment_risk = risk.get("deployment_risk", "")
    deployment_allowed = risk.get("deployment_allowed", False)
    allowed_text = "가능" if deployment_allowed else "불가"

    duration = metadata.get("analysis_duration_seconds", 0)
    tokens = metadata.get("total_llm_tokens", 0)

    risk_label = _RISK_KR.get(deployment_risk, deployment_risk)

    lines = [
        "## 분석 요약",
        "",
        "| 항목 | 값 |",
        "|------|-----|",
        f"| 총 이슈 수 | {total} |",
        f"| Critical | {critical} |",
        f"| High | {high} |",
        f"| Medium | {medium} |",
        f"| Low | {low} |",
        f"| 배포 위험도 | {risk_label} |",
        f"| 배포 가능 여부 | {allowed_text} |",
        f"| 분석 시간 | {_format_duration(duration)} |",
        f"| LLM 토큰 | {tokens:,} |",
        "",
    ]

    risk_desc = risk.get("risk_description", "")
    if risk_desc:
        lines.append(f"> {risk_desc}")
        lines.append("")

    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _build_critical_high_section(issue_list: dict[str, Any]) -> str:
    """Critical/High 이슈 상세 카드."""
    issues = issue_list.get("issues", [])
    critical_high = [
        i for i in issues if i.get("severity") in ("critical", "high")
    ]

    lines = ["## 핵심 이슈 (Critical / High)", ""]

    if not critical_high:
        lines.append("> 핵심 이슈 없음")
        lines.append("")
        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    # severity 순 정렬
    critical_high.sort(
        key=lambda x: _SEVERITY_ORDER.index(x.get("severity", "low"))
    )

    for issue in critical_high:
        lines.append(_render_issue_card(issue, heading_level="###"))
        lines.append("")

    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _build_full_issue_list_section(issue_list: dict[str, Any]) -> str:
    """전체 이슈 목록 (severity 순)."""
    issues = issue_list.get("issues", [])

    lines = ["## 전체 이슈 목록", ""]

    if not issues:
        lines.append("> 주요 이슈 없음")
        lines.append("")
        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    # severity 순 정렬
    sorted_issues = sorted(
        issues,
        key=lambda x: _SEVERITY_ORDER.index(x.get("severity", "low")),
    )

    for issue in sorted_issues:
        lines.append(_render_issue_card(issue, heading_level="###"))
        lines.append("")

    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _build_checklist_section(checklist: dict[str, Any]) -> str:
    """검증 체크리스트."""
    items = checklist.get("items", [])

    lines = ["## 검증 체크리스트", ""]

    if not items:
        lines.append("> 해당 없음")
        lines.append("")
        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    for item in items:
        item_id = item.get("id", "")
        severity = item.get("severity", "").upper()
        desc = item.get("description", "")
        related = item.get("related_issues", [])
        cmd = item.get("verification_command", "")
        expected = item.get("expected_result", "")

        lines.append(f"- [ ] **[{severity}]** {item_id}: {desc}")
        if related:
            lines.append(f"  - 관련 이슈: {', '.join(related)}")
        if cmd:
            lines.append(f"  - 검증: `{cmd}`")
        if expected:
            lines.append(f"  - 기대 결과: {expected}")

    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _build_deployment_checklist_section(
    deployment_checklist: dict[str, Any],
) -> str:
    """배포 체크리스트."""
    sections = deployment_checklist.get("sections", [])

    lines = ["## 배포 체크리스트", ""]

    if not sections:
        lines.append("> 해당 없음")
        lines.append("")
        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    for section in sections:
        title = section.get("title", "")
        files = section.get("files", [])
        items = section.get("items", [])

        lines.append(f"### {title}")
        if files:
            lines.append("")
            for f in files:
                lines.append(f"- 파일: `{f}`")
        lines.append("")
        for item in items:
            item_id = item.get("id", "")
            desc = item.get("item", "")
            lines.append(f"- [ ] {item_id}: {desc}")
        lines.append("")

    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _build_metadata_footer(
    json_filenames: list[str],
    summary: dict[str, Any],
) -> str:
    """메타데이터 (세션 ID, 생성 파일)."""
    metadata = summary.get("analysis_metadata", {})
    session_id = metadata.get("session_id", "")
    total_files = metadata.get("total_files", 0)
    total_lines = metadata.get("total_lines", 0)

    lines = [
        "## 메타데이터",
        "",
        f"- **세션 ID**: `{session_id}`",
        f"- **분석 파일 수**: {total_files}",
        f"- **총 라인 수**: {total_lines:,}줄",
        "- **생성된 파일**:",
    ]

    for fn in json_filenames:
        lines.append(f"  - `{fn}`")

    lines.append("")
    return "\n".join(lines)


# ── 이슈 카드 렌더러 ──────────────────────────────


def _render_issue_card(issue: dict[str, Any], heading_level: str) -> str:
    """단일 이슈를 Markdown 카드로 렌더링한다.

    Args:
        issue: 이슈 딕셔너리
        heading_level: Markdown 헤딩 수준 ("##", "###" 등)
    """
    severity = issue.get("severity", "").upper()
    issue_id = issue.get("issue_id", "")
    title = issue.get("title", "")
    language = issue.get("language", "")
    category = issue.get("category", "")
    description = issue.get("description", "")
    source = issue.get("source", "")

    location = issue.get("location", {})
    loc_file = location.get("file", "")
    line_start = location.get("line_start", 0)

    fix = issue.get("fix", {})
    before = fix.get("before", "")
    after = fix.get("after", "")
    fix_desc = fix.get("description", "")

    category_kr = _CATEGORY_KR.get(category, category)
    code_lang = _LANG_MAP.get(language, "")

    sub = "#" * (len(heading_level) + 1)  # heading_level + 1

    lines = [
        f"{heading_level} [{severity}] {issue_id} {title}",
        f"- **위치**: `{loc_file}:{line_start}`",
        f"- **분류**: `{category}` ({category_kr})",
        f"- **출처**: {source}",
        "",
    ]

    if description:
        lines.append(f"{sub} 설명")
        lines.append(description)
        lines.append("")

    if before:
        lines.append(f"{sub} 수정 전")
        lines.append(f"```{code_lang}")
        lines.append(before)
        lines.append("```")
        lines.append("")

    if after:
        lines.append(f"{sub} 수정 후")
        lines.append(f"```{code_lang}")
        lines.append(after)
        lines.append("```")
        lines.append("")

    if fix_desc:
        lines.append(f"{sub} 조치 가이드")
        lines.append(fix_desc)
        lines.append("")

    return "\n".join(lines)


# ── 유틸리티 ──────────────────────────────────────


def _format_duration(seconds: float) -> str:
    """초 단위 시간을 'N분 N초' 형식으로 변환한다."""
    if seconds < 60:
        return f"{seconds:.0f}초"
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{minutes}분 {secs}초"


def _format_timestamp(iso_str: str) -> str:
    """ISO 8601 타임스탬프를 읽기 쉬운 형식으로 변환한다."""
    if not iso_str:
        return "(알 수 없음)"
    # "2026-04-04T22:31:00.123456Z" → "2026-04-04 22:31"
    try:
        date_part = iso_str.replace("T", " ").split(".")[0]
        if date_part.endswith("Z"):
            date_part = date_part[:-1]
        return date_part[:16]
    except Exception:
        return iso_str
