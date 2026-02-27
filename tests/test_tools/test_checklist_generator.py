"""ChecklistGenerator 단위 테스트."""

from mider.tools.utility.checklist_generator import ChecklistGenerator


def _make_result(
    file: str = "/app/calc.c",
    issues: list | None = None,
) -> dict:
    """테스트용 AnalysisResult dict 생성."""
    if issues is None:
        issues = []
    return {
        "task_id": "task_1",
        "file": file,
        "language": "c",
        "agent": "CAnalyzerAgent",
        "issues": issues,
        "analysis_time_seconds": 1.0,
        "llm_tokens_used": 100,
    }


def _make_issue(
    issue_id: str = "C-001",
    category: str = "memory_safety",
    severity: str = "critical",
    title: str = "strcpy 버퍼 오버플로우",
    description: str = "strcpy 사용 시 버퍼 오버플로우 위험",
) -> dict:
    """테스트용 Issue dict 생성."""
    return {
        "issue_id": issue_id,
        "category": category,
        "severity": severity,
        "title": title,
        "description": description,
        "location": {"file": "/app/calc.c", "line_start": 10, "line_end": 10},
        "fix": {"before": "old", "after": "new", "description": "fix"},
        "source": "hybrid",
    }


class TestChecklistGenerator:
    def setup_method(self):
        self.generator = ChecklistGenerator()

    def test_critical_issue_generates_check(self):
        result = self.generator.execute(
            analysis_results=[
                _make_result(issues=[_make_issue(severity="critical")])
            ]
        )
        assert result.success is True
        assert result.data["total_checks"] == 1
        item = result.data["items"][0]
        assert item["id"] == "CHECK-1"
        assert item["severity"] == "critical"
        assert item["category"] == "memory_safety"
        assert "C-001" in item["related_issues"]

    def test_high_issue_generates_check(self):
        result = self.generator.execute(
            analysis_results=[
                _make_result(issues=[_make_issue(severity="high")])
            ]
        )
        assert result.data["total_checks"] == 1
        assert result.data["items"][0]["severity"] == "high"

    def test_medium_low_ignored(self):
        result = self.generator.execute(
            analysis_results=[
                _make_result(issues=[
                    _make_issue(issue_id="C-001", severity="medium"),
                    _make_issue(issue_id="C-002", severity="low"),
                ])
            ]
        )
        assert result.data["total_checks"] == 0
        assert result.data["items"] == []

    def test_empty_results(self):
        result = self.generator.execute(analysis_results=[])
        assert result.success is True
        assert result.data["total_checks"] == 0

    def test_grouping_same_category_file(self):
        result = self.generator.execute(
            analysis_results=[
                _make_result(issues=[
                    _make_issue(issue_id="C-001", severity="critical"),
                    _make_issue(issue_id="C-002", severity="high"),
                ])
            ]
        )
        # Same category + file = 1 check item with 2 related issues
        assert result.data["total_checks"] == 1
        item = result.data["items"][0]
        assert len(item["related_issues"]) == 2
        assert item["severity"] == "critical"  # highest in group

    def test_different_categories_separate(self):
        result = self.generator.execute(
            analysis_results=[
                _make_result(issues=[
                    _make_issue(
                        issue_id="C-001",
                        category="memory_safety",
                        severity="critical",
                    ),
                    _make_issue(
                        issue_id="C-002",
                        category="security",
                        severity="high",
                        title="XSS 취약점",
                        description="innerHTML 사용",
                    ),
                ])
            ]
        )
        assert result.data["total_checks"] == 2

    def test_verification_command_generated(self):
        result = self.generator.execute(
            analysis_results=[
                _make_result(issues=[_make_issue()])
            ]
        )
        item = result.data["items"][0]
        assert "verification_command" in item
        assert "grep" in item["verification_command"]
        assert "/app/calc.c" in item["verification_command"]

    def test_critical_sorted_first(self):
        result = self.generator.execute(
            analysis_results=[
                _make_result(
                    file="/app/a.c",
                    issues=[
                        _make_issue(
                            issue_id="C-001",
                            severity="high",
                            category="security",
                            title="보안 이슈",
                        ),
                    ],
                ),
                _make_result(
                    file="/app/b.c",
                    issues=[
                        _make_issue(
                            issue_id="C-002",
                            severity="critical",
                            category="memory_safety",
                        ),
                    ],
                ),
            ]
        )
        items = result.data["items"]
        assert items[0]["severity"] == "critical"
        assert items[1]["severity"] == "high"

    def test_pattern_inference_strcpy(self):
        result = self.generator.execute(
            analysis_results=[
                _make_result(issues=[
                    _make_issue(title="strcpy 버퍼 오버플로우 위험")
                ])
            ]
        )
        cmd = result.data["items"][0]["verification_command"]
        assert "strcpy" in cmd

    def test_pattern_inference_sqlca(self):
        result = self.generator.execute(
            analysis_results=[
                _make_result(
                    file="/app/batch.pc",
                    issues=[
                        _make_issue(
                            issue_id="PC-001",
                            category="data_integrity",
                            severity="critical",
                            title="SQLCA 체크 누락",
                            description="EXEC SQL 후 SQLCA 체크가 없습니다",
                        ),
                    ],
                )
            ]
        )
        item = result.data["items"][0]
        assert "EXEC SQL" in item["verification_command"]

    def test_multiple_files(self):
        result = self.generator.execute(
            analysis_results=[
                _make_result(
                    file="/app/a.c",
                    issues=[_make_issue(issue_id="C-001", severity="critical")],
                ),
                _make_result(
                    file="/app/b.c",
                    issues=[_make_issue(issue_id="C-002", severity="critical")],
                ),
            ]
        )
        assert result.data["total_checks"] == 2
