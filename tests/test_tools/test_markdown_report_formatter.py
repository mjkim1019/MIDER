"""Markdown Report Formatter 단위 테스트."""

import pytest

from mider.tools.utility.markdown_report_formatter import format_markdown_report


# ── fixture 헬퍼 ──


def _make_issue(
    issue_id: str = "C-001",
    severity: str = "high",
    language: str = "c",
    category: str = "memory_safety",
    title: str = "테스트 이슈",
    description: str = "테스트 설명",
    before: str = "old_code();",
    after: str = "new_code();",
    fix_desc: str = "수정 가이드",
    line_start: int = 10,
) -> dict:
    return {
        "issue_id": issue_id,
        "file": "/app/test.c",
        "language": language,
        "category": category,
        "severity": severity,
        "title": title,
        "description": description,
        "location": {
            "file": "/app/test.c",
            "line_start": line_start,
            "line_end": line_start,
            "column_start": None,
            "column_end": None,
        },
        "fix": {
            "before": before,
            "after": after,
            "description": fix_desc,
        },
        "source": "hybrid",
    }


def _make_issue_list(issues: list[dict] | None = None) -> dict:
    issues = issues or []
    by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for i in issues:
        sev = i.get("severity", "low")
        by_severity[sev] = by_severity.get(sev, 0) + 1
    return {
        "generated_at": "2026-04-04T22:31:00.000000Z",
        "session_id": "abc123",
        "total_issues": len(issues),
        "by_severity": by_severity,
        "issues": issues,
    }


def _make_checklist(items: list[dict] | None = None) -> dict:
    items = items or []
    return {
        "generated_at": "2026-04-04T22:31:00.000000Z",
        "session_id": "abc123",
        "total_checks": len(items),
        "items": items,
    }


def _make_summary(
    total: int = 0,
    by_severity: dict | None = None,
    deployment_risk: str = "LOW",
    deployment_allowed: bool = True,
    duration: float = 60.0,
    tokens: int = 5000,
    risk_description: str = "",
) -> dict:
    by_severity = by_severity or {
        "critical": 0, "high": 0, "medium": 0, "low": 0,
    }
    return {
        "analysis_metadata": {
            "session_id": "abc123",
            "analyzed_at": "2026-04-04T22:31:00.000000Z",
            "total_files": 1,
            "total_lines": 1000,
            "analysis_duration_seconds": duration,
            "total_llm_tokens": tokens,
        },
        "issue_summary": {
            "total": total,
            "by_severity": by_severity,
            "by_category": {},
            "by_language": {},
            "by_file": {},
        },
        "risk_assessment": {
            "deployment_risk": deployment_risk,
            "deployment_allowed": deployment_allowed,
            "blocking_issues": [],
            "risk_description": risk_description,
        },
    }


def _make_deployment_checklist(
    sections: list[dict] | None = None,
) -> dict:
    sections = sections or []
    total = sum(len(s.get("items", [])) for s in sections)
    return {
        "generated_at": "2026-04-04T22:31:00.000000Z",
        "session_id": "abc123",
        "total_items": total,
        "sections": sections,
    }


def _default_kwargs(**overrides) -> dict:
    """기본 format_markdown_report kwargs."""
    defaults = {
        "issue_list": _make_issue_list(),
        "checklist": _make_checklist(),
        "summary": _make_summary(),
        "deployment_checklist": _make_deployment_checklist(),
        "source_files": ["/app/test.c"],
        "json_filenames": ["test_issue-list.json", "test_checklist.json"],
    }
    defaults.update(overrides)
    return defaults


# ── 제목 섹션 ──


class TestTitleSection:
    def test_contains_title(self):
        md = format_markdown_report(**_default_kwargs())
        assert "# Mider 분석 리포트" in md

    def test_contains_timestamp(self):
        md = format_markdown_report(**_default_kwargs())
        assert "2026-04-04 22:31" in md

    def test_contains_source_files(self):
        md = format_markdown_report(
            **_default_kwargs(source_files=["/app/a.c", "/app/b.js"])
        )
        assert "`/app/a.c`" in md
        assert "`/app/b.js`" in md

    def test_empty_source_files(self):
        md = format_markdown_report(**_default_kwargs(source_files=[]))
        assert "(없음)" in md


# ── 분석 요약 섹션 ──


class TestSummarySection:
    def test_severity_counts(self):
        summary = _make_summary(
            total=10,
            by_severity={"critical": 2, "high": 3, "medium": 4, "low": 1},
        )
        md = format_markdown_report(**_default_kwargs(summary=summary))
        assert "| Critical | 2 |" in md
        assert "| High | 3 |" in md

    def test_deployment_risk(self):
        summary = _make_summary(deployment_risk="CRITICAL")
        md = format_markdown_report(**_default_kwargs(summary=summary))
        assert "CRITICAL" in md

    def test_deployment_allowed_true(self):
        summary = _make_summary(deployment_allowed=True)
        md = format_markdown_report(**_default_kwargs(summary=summary))
        assert "| 배포 가능 여부 | 가능 |" in md

    def test_deployment_allowed_false(self):
        summary = _make_summary(deployment_allowed=False)
        md = format_markdown_report(**_default_kwargs(summary=summary))
        assert "| 배포 가능 여부 | 불가 |" in md

    def test_duration_formatted(self):
        summary = _make_summary(duration=125.0)
        md = format_markdown_report(**_default_kwargs(summary=summary))
        assert "2분 5초" in md

    def test_tokens_with_comma(self):
        summary = _make_summary(tokens=145099)
        md = format_markdown_report(**_default_kwargs(summary=summary))
        assert "145,099" in md

    def test_risk_description(self):
        summary = _make_summary(risk_description="위험한 상황입니다.")
        md = format_markdown_report(**_default_kwargs(summary=summary))
        assert "> 위험한 상황입니다." in md


# ── 핵심 이슈 섹션 ──


class TestCriticalHighSection:
    def test_renders_critical_issues(self):
        issues = [_make_issue(issue_id="C-001", severity="critical")]
        il = _make_issue_list(issues)
        md = format_markdown_report(**_default_kwargs(issue_list=il))
        assert "[CRITICAL] C-001" in md

    def test_renders_high_issues(self):
        issues = [_make_issue(issue_id="C-002", severity="high")]
        il = _make_issue_list(issues)
        md = format_markdown_report(**_default_kwargs(issue_list=il))
        assert "[HIGH] C-002" in md

    def test_no_critical_high_message(self):
        issues = [_make_issue(severity="low")]
        il = _make_issue_list(issues)
        md = format_markdown_report(**_default_kwargs(issue_list=il))
        assert "핵심 이슈 없음" in md

    def test_empty_issues_message(self):
        md = format_markdown_report(**_default_kwargs())
        assert "핵심 이슈 없음" in md


# ── 전체 이슈 목록 ──


class TestFullIssueList:
    def test_sorted_by_severity(self):
        issues = [
            _make_issue(issue_id="I-1", severity="low"),
            _make_issue(issue_id="I-2", severity="critical"),
            _make_issue(issue_id="I-3", severity="medium"),
        ]
        il = _make_issue_list(issues)
        md = format_markdown_report(**_default_kwargs(issue_list=il))
        pos_critical = md.index("[CRITICAL] I-2")
        pos_medium = md.index("[MEDIUM] I-3")
        pos_low = md.index("[LOW] I-1")
        assert pos_critical < pos_medium < pos_low

    def test_empty_message(self):
        md = format_markdown_report(**_default_kwargs())
        assert "주요 이슈 없음" in md


# ── 이슈 카드 ──


class TestIssueCard:
    def test_code_block_language(self):
        issues = [_make_issue(language="javascript")]
        il = _make_issue_list(issues)
        md = format_markdown_report(**_default_kwargs(issue_list=il))
        assert "```javascript" in md

    def test_proc_uses_c_fence(self):
        issues = [_make_issue(language="proc")]
        il = _make_issue_list(issues)
        md = format_markdown_report(**_default_kwargs(issue_list=il))
        assert "```c" in md

    def test_empty_fix_omitted(self):
        issues = [_make_issue(before="", after="")]
        il = _make_issue_list(issues)
        md = format_markdown_report(**_default_kwargs(issue_list=il))
        assert "수정 전" not in md
        assert "수정 후" not in md

    def test_location_displayed(self):
        issues = [_make_issue(line_start=42)]
        il = _make_issue_list(issues)
        md = format_markdown_report(**_default_kwargs(issue_list=il))
        assert "/app/test.c:42" in md

    def test_category_korean(self):
        issues = [_make_issue(category="memory_safety")]
        il = _make_issue_list(issues)
        md = format_markdown_report(**_default_kwargs(issue_list=il))
        assert "메모리 안전성" in md

    def test_fix_description(self):
        issues = [_make_issue(fix_desc="이렇게 수정하세요")]
        il = _make_issue_list(issues)
        md = format_markdown_report(**_default_kwargs(issue_list=il))
        assert "이렇게 수정하세요" in md


# ── 체크리스트 섹션 ──


class TestChecklistSection:
    def test_renders_items(self):
        items = [{
            "id": "CHECK-1",
            "category": "security",
            "severity": "critical",
            "description": "보안 이슈 확인",
            "related_issues": ["C-001"],
            "verification_command": "grep -n 'strcpy'",
            "expected_result": "0건",
        }]
        cl = _make_checklist(items)
        md = format_markdown_report(**_default_kwargs(checklist=cl))
        assert "CHECK-1" in md
        assert "보안 이슈 확인" in md
        assert "C-001" in md

    def test_empty_message(self):
        md = format_markdown_report(**_default_kwargs())
        # checklist 기본값은 빈 items
        assert "검증 체크리스트" in md


# ── 배포 체크리스트 섹션 ──


class TestDeploymentChecklistSection:
    def test_renders_sections(self):
        sections = [{
            "section_id": "batch",
            "title": "Batch 배포 (.pc)",
            "files": ["/app/test.pc"],
            "items": [
                {"id": "BAT-01", "item": "SVN 커밋 확인", "checked": False},
            ],
        }]
        dc = _make_deployment_checklist(sections)
        md = format_markdown_report(
            **_default_kwargs(deployment_checklist=dc)
        )
        assert "Batch 배포 (.pc)" in md
        assert "BAT-01" in md

    def test_empty_message(self):
        md = format_markdown_report(**_default_kwargs())
        assert "배포 체크리스트" in md


# ── 메타데이터 ──


class TestMetadata:
    def test_session_id(self):
        md = format_markdown_report(**_default_kwargs())
        assert "`abc123`" in md

    def test_json_filenames(self):
        md = format_markdown_report(**_default_kwargs())
        assert "test_issue-list.json" in md


# ── 전체 구조 ──


class TestStructureOrdering:
    def test_sections_in_order(self):
        issues = [_make_issue(severity="critical")]
        il = _make_issue_list(issues)
        md = format_markdown_report(**_default_kwargs(issue_list=il))
        sections = [
            "# Mider 분석 리포트",
            "## 분석 요약",
            "## 핵심 이슈",
            "## 전체 이슈 목록",
            "## 검증 체크리스트",
            "## 배포 체크리스트",
            "## 메타데이터",
        ]
        positions = [md.index(s) for s in sections]
        assert positions == sorted(positions)

    def test_korean_not_escaped(self):
        summary = _make_summary(risk_description="한국어 테스트")
        md = format_markdown_report(**_default_kwargs(summary=summary))
        assert "한국어 테스트" in md
        assert "\\u" not in md
