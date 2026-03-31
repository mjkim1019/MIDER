"""T15 Integration Test — 전체 파이프라인 E2E 검증.

Phase 0(분류) → Phase 1(컨텍스트) → Phase 2(분석) → Phase 3(리포트)
전체 흐름을 LLM mock으로 검증한다.

- 5개 언어별 샘플 파일 (JS, C, ProC, SQL, XML)
- 출력 파일 4개 JSON 검증
- Exit code 검증
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mider.agents.orchestrator import OrchestratorAgent
from mider.main import determine_exit_code, write_output_files

# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_llm_response(issues: list[dict] | None = None) -> str:
    """LLM 응답 JSON 문자열을 생성한다."""
    return json.dumps({"issues": issues or []})


def _make_issue(
    issue_id: str = "X-001",
    category: str = "data_integrity",
    severity: str = "high",
    title: str = "테스트 이슈",
    file: str = "/tmp/test",
    line_start: int = 10,
) -> dict:
    return {
        "issue_id": issue_id,
        "category": category,
        "severity": severity,
        "title": title,
        "description": "테스트 설명",
        "location": {
            "file": file,
            "line_start": line_start,
            "line_end": line_start + 2,
        },
        "fix": {
            "before": "before",
            "after": "after",
            "description": "수정 설명",
        },
        "source": "llm",
    }


def _make_critical_issue(file: str = "/tmp/test") -> dict:
    return _make_issue(
        issue_id="X-001",
        severity="critical",
        title="치명적 이슈",
        file=file,
    )


def _make_classifier_response(file: str, language: str) -> str:
    """Phase 0 TaskClassifier 응답."""
    return json.dumps({
        "sub_tasks": [
            {
                "task_id": "task_1",
                "file": file,
                "language": language,
                "priority": 1,
                "estimated_time_seconds": 10,
            }
        ],
        "total_estimated_seconds": 10,
    })


def _make_context_response() -> str:
    """Phase 1 ContextCollector 응답."""
    return json.dumps({
        "file_contexts": {},
        "corrections": [],
    })


def _make_reporter_response(issues: list[dict]) -> str:
    """Phase 3 Reporter 응답."""
    by_severity: dict[str, int] = {}
    for issue in issues:
        sev = issue.get("severity", "low")
        by_severity[sev] = by_severity.get(sev, 0) + 1

    return json.dumps({
        "risk_description": "테스트 리스크",
        "deployment_risk": "high" if by_severity.get("critical", 0) > 0 else "low",
        "deployment_allowed": by_severity.get("critical", 0) == 0,
        "recommended_actions": ["코드 리뷰"],
    })


# ──────────────────────────────────────────────
# T15.1: 샘플 파일 존재 검증
# ──────────────────────────────────────────────


class TestSampleFilesExist:
    """fixtures에 5개 언어 파일이 존재하는지 검증."""

    def test_js_file_exists(self):
        js_files = list(FIXTURES_DIR.glob("**/*.js"))
        assert len(js_files) >= 1, "JS 샘플 파일 없음"

    def test_c_file_exists(self):
        c_files = list(FIXTURES_DIR.glob("error/*.c"))
        assert len(c_files) >= 1, "C 샘플 파일 없음"

    def test_proc_file_exists(self):
        pc_files = list(FIXTURES_DIR.glob("error/*.pc"))
        assert len(pc_files) >= 1, "ProC 샘플 파일 없음"

    def test_sql_file_exists(self):
        sql_files = list(FIXTURES_DIR.glob("**/*.sql"))
        assert len(sql_files) >= 1, "SQL 샘플 파일 없음"

    def test_xml_file_exists(self):
        xml_files = list(FIXTURES_DIR.glob("**/*.xml"))
        assert len(xml_files) >= 1, "XML 샘플 파일 없음"


# ──────────────────────────────────────────────
# T15.2: E2E 파이프라인 테스트
# ──────────────────────────────────────────────


class TestE2EPipeline:
    """Phase 0→1→2→3 전체 파이프라인이 정상 동작하는지 검증."""

    @pytest.fixture
    def small_pc_file(self, tmp_path):
        """작은 ProC 샘플 파일."""
        content = (
            '#include <stdio.h>\n'
            'EXEC SQL INCLUDE SQLCA;\n'
            'void main() {\n'
            '  EXEC SQL SELECT 1 FROM DUAL;\n'
            '  if (sqlca.sqlcode != 0) return;\n'
            '  EXEC SQL COMMIT;\n'
            '  return;\n'
            '}\n'
        )
        f = tmp_path / "test.pc"
        f.write_text(content)
        return str(f)

    @pytest.mark.asyncio
    async def test_single_file_pipeline(self, small_pc_file, tmp_path):
        """단일 파일 E2E: Phase 0→1→2→3 정상 완료."""
        orch = OrchestratorAgent(model="gpt-5")
        mock_llm = AsyncMock()

        # Phase 0: classifier → 1개 task
        # Phase 1: context → 빈 컨텍스트 (단일 파일 skip)
        # Phase 2: proc analyzer → issues
        # Phase 3: reporter → summary
        issue = _make_issue(file=small_pc_file)
        mock_llm.chat.side_effect = [
            # Phase 0 (단일 파일이면 LLM skip)
            # Phase 1 (단일 파일이면 LLM skip)
            # Phase 2: ProC analyzer (단일 호출)
            _make_llm_response([issue]),
            # Phase 3: reporter
            _make_reporter_response([issue]),
        ]

        # 모든 sub-agent에 동일 mock LLM 주입
        orch._task_classifier = None  # lazy init
        orch._context_collector = None
        orch._reporter = None

        # orchestrator 내부에서 생성되는 analyzer에도 mock 필요
        # → _run_phase2에서 Analyzer를 생성하므로 LLM client를 전역 mock
        from unittest.mock import patch
        with patch("mider.agents.base_agent.LLMClient") as mock_client_cls:
            mock_client_cls.return_value = mock_llm
            result = await orch.run(files=[small_pc_file])

        assert "issue_list" in result
        assert "checklist" in result
        assert "summary" in result
        assert "deployment_checklist" in result
        assert "session_id" in result
        assert result.get("errors") is None or len(result.get("errors", [])) == 0

    @pytest.mark.asyncio
    async def test_nonexistent_file_returns_error(self):
        """존재하지 않는 파일 → errors 포함."""
        orch = OrchestratorAgent(model="gpt-5")
        result = await orch.run(files=["/nonexistent/file.pc"])

        assert len(result.get("errors", [])) > 0

    @pytest.mark.asyncio
    async def test_unsupported_extension_filtered(self, tmp_path):
        """지원하지 않는 확장자 → 필터링."""
        f = tmp_path / "readme.md"
        f.write_text("# Hello")
        orch = OrchestratorAgent(model="gpt-5")
        result = await orch.run(files=[str(f)])

        # .md는 지원하지 않으므로 valid_files = 0 → errors
        assert len(result.get("errors", [])) > 0


# ──────────────────────────────────────────────
# T15.3: Exit code 검증
# ──────────────────────────────────────────────


class TestExitCode:
    """determine_exit_code 함수 검증."""

    def test_no_issues_returns_0(self):
        result = {
            "summary": {
                "issue_summary": {
                    "by_severity": {"low": 2, "medium": 1},
                },
            },
        }
        assert determine_exit_code(result) == 0

    def test_critical_returns_1(self):
        result = {
            "summary": {
                "issue_summary": {
                    "by_severity": {"critical": 1, "high": 2},
                },
            },
        }
        assert determine_exit_code(result) == 1

    def test_empty_result_returns_0(self):
        assert determine_exit_code({}) == 0

    def test_high_only_returns_0(self):
        result = {
            "summary": {
                "issue_summary": {
                    "by_severity": {"high": 5},
                },
            },
        }
        assert determine_exit_code(result) == 0


# ──────────────────────────────────────────────
# T15.4: 출력 파일 검증 (4개 JSON)
# ──────────────────────────────────────────────


class TestOutputFiles:
    """write_output_files가 4개 JSON을 정상 생성하는지 검증."""

    def test_writes_4_json_files(self, tmp_path):
        output_dir = str(tmp_path / "output")
        result = {
            "issue_list": {"issues": [], "total_issues": 0},
            "checklist": {"items": []},
            "summary": {"issue_summary": {}},
            "deployment_checklist": {"sections": []},
        }

        write_output_files(output_dir, result, ["/tmp/test.pc"])

        output_path = Path(output_dir)
        json_files = list(output_path.glob("*.json"))
        assert len(json_files) == 4, f"JSON 파일 수: {len(json_files)}, 기대: 4"

    def test_json_files_are_valid(self, tmp_path):
        output_dir = str(tmp_path / "output")
        result = {
            "issue_list": {"issues": [_make_issue()], "total_issues": 1},
            "checklist": {"items": ["SQLCA 검사"]},
            "summary": {"risk": "high"},
            "deployment_checklist": {"sections": ["DB"]},
        }

        write_output_files(output_dir, result, ["/tmp/test.pc"])

        output_path = Path(output_dir)
        for json_file in output_path.glob("*.json"):
            content = json_file.read_text(encoding="utf-8")
            parsed = json.loads(content)
            assert isinstance(parsed, dict), f"{json_file.name}이 유효한 JSON이 아님"

    def test_issue_list_contains_issues(self, tmp_path):
        output_dir = str(tmp_path / "output")
        issue = _make_issue()
        result = {
            "issue_list": {"issues": [issue], "total_issues": 1},
            "checklist": {},
            "summary": {},
            "deployment_checklist": {},
        }

        write_output_files(output_dir, result, ["/tmp/test.pc"])

        output_path = Path(output_dir)
        issue_files = list(output_path.glob("*issue-list.json"))
        assert len(issue_files) == 1
        content = json.loads(issue_files[0].read_text())
        assert content["total_issues"] == 1
        assert len(content["issues"]) == 1

    def test_output_filenames_have_prefix(self, tmp_path):
        output_dir = str(tmp_path / "output")
        result = {
            "issue_list": {},
            "checklist": {},
            "summary": {},
            "deployment_checklist": {},
        }

        write_output_files(output_dir, result, ["/tmp/test.pc"])

        output_path = Path(output_dir)
        filenames = [f.name for f in output_path.glob("*.json")]
        # 파일명에 prefix가 포함되어야 함
        assert any("issue-list" in f for f in filenames)
        assert any("checklist" in f for f in filenames)
        assert any("summary" in f for f in filenames)
        assert any("deployment-checklist" in f for f in filenames)
