"""T14.6: CLI Entry Point 단위 테스트."""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mider.main import (
    EXIT_CRITICAL_FOUND,
    EXIT_FILE_ERROR,
    EXIT_LLM_ERROR,
    EXIT_OK,
    build_parser,
    determine_exit_code,
    print_file_list,
    print_issues,
    print_summary,
    resolve_model,
    run_analysis,
    validate_api_key,
    write_output_files,
)


# ──────────────────────────────────────────────
# build_parser
# ──────────────────────────────────────────────


class TestBuildParser:
    """argparse 파서 테스트."""

    def test_required_files(self):
        """--files는 필수 인자."""
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_files_single(self):
        """단일 파일 지정."""
        parser = build_parser()
        args = parser.parse_args(["--files", "test.js"])
        assert args.files == ["test.js"]

    def test_files_multiple(self):
        """다중 파일 지정."""
        parser = build_parser()
        args = parser.parse_args(["-f", "a.c", "b.pc", "c.sql"])
        assert args.files == ["a.c", "b.pc", "c.sql"]

    def test_output_default(self):
        """--output 기본값은 ./output."""
        parser = build_parser()
        args = parser.parse_args(["--files", "test.js"])
        assert args.output == "./output"

    def test_output_custom(self):
        """--output 커스텀 경로."""
        parser = build_parser()
        args = parser.parse_args(["-f", "test.js", "-o", "/tmp/reports"])
        assert args.output == "/tmp/reports"

    def test_model_option(self):
        """--model 옵션."""
        parser = build_parser()
        args = parser.parse_args(["-f", "test.js", "-m", "gpt-4o-mini"])
        assert args.model == "gpt-4o-mini"

    def test_model_default_none(self):
        """--model 미지정 시 None."""
        parser = build_parser()
        args = parser.parse_args(["-f", "test.js"])
        assert args.model is None

    def test_verbose_flag(self):
        """--verbose 플래그."""
        parser = build_parser()
        args = parser.parse_args(["-f", "test.js", "-v"])
        assert args.verbose is True

    def test_verbose_default_false(self):
        """--verbose 미지정 시 False."""
        parser = build_parser()
        args = parser.parse_args(["-f", "test.js"])
        assert args.verbose is False

    def test_explain_plan_option(self):
        """--explain-plan 옵션."""
        parser = build_parser()
        args = parser.parse_args(["-f", "test.sql", "-e", "/tmp/plan.txt"])
        assert args.explain_plan == "/tmp/plan.txt"

    def test_explain_plan_default_none(self):
        """--explain-plan 미지정 시 None."""
        parser = build_parser()
        args = parser.parse_args(["-f", "test.sql"])
        assert args.explain_plan is None

    def test_version(self):
        """--version 출력."""
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--version"])
        assert exc_info.value.code == 0


# ──────────────────────────────────────────────
# resolve_model
# ──────────────────────────────────────────────


class TestResolveModel:
    """모델 결정 로직 테스트."""

    def test_cli_arg_priority(self):
        """CLI 인자가 최우선."""
        result = resolve_model("gpt-4o-mini")
        assert result == "gpt-4o-mini"

    def test_env_var_fallback(self, monkeypatch):
        """환경변수 폴백."""
        monkeypatch.setenv("MIDER_MODEL", "gpt-4-turbo")
        result = resolve_model(None)
        assert result == "gpt-4-turbo"

    def test_default_gpt4o(self, monkeypatch):
        """기본값 gpt-4o."""
        monkeypatch.delenv("MIDER_MODEL", raising=False)
        result = resolve_model(None)
        assert result == "gpt-4o"


# ──────────────────────────────────────────────
# validate_api_key
# ──────────────────────────────────────────────


class TestValidateApiKey:
    """API 키 검증 테스트."""

    def test_valid_key(self, monkeypatch):
        """API 키가 설정된 경우 정상 반환."""
        monkeypatch.setenv("MIDER_API_KEY", "sk-test-key")
        result = validate_api_key()
        assert result == "sk-test-key"

    def test_missing_key_exits(self, monkeypatch):
        """API 키 미설정 시 exit code 3."""
        monkeypatch.delenv("MIDER_API_KEY", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            validate_api_key()
        assert exc_info.value.code == EXIT_LLM_ERROR

    def test_empty_key_exits(self, monkeypatch):
        """빈 API 키 시 exit code 3."""
        monkeypatch.setenv("MIDER_API_KEY", "")
        with pytest.raises(SystemExit) as exc_info:
            validate_api_key()
        assert exc_info.value.code == EXIT_LLM_ERROR


# ──────────────────────────────────────────────
# determine_exit_code
# ──────────────────────────────────────────────


class TestDetermineExitCode:
    """종료 코드 결정 테스트."""

    def test_no_critical_returns_0(self):
        """Critical 없으면 0."""
        result = {
            "summary": {
                "issue_summary": {
                    "by_severity": {"critical": 0, "high": 2, "medium": 1, "low": 0},
                },
            },
        }
        assert determine_exit_code(result) == EXIT_OK

    def test_critical_found_returns_1(self):
        """Critical 있으면 1."""
        result = {
            "summary": {
                "issue_summary": {
                    "by_severity": {"critical": 2, "high": 1, "medium": 0, "low": 0},
                },
            },
        }
        assert determine_exit_code(result) == EXIT_CRITICAL_FOUND

    def test_empty_result_returns_0(self):
        """빈 결과는 0."""
        assert determine_exit_code({}) == EXIT_OK


# ──────────────────────────────────────────────
# write_output_files
# ──────────────────────────────────────────────


class TestWriteOutputFiles:
    """JSON 출력 테스트."""

    def test_creates_output_dir(self, tmp_path: Path):
        """출력 디렉토리를 자동 생성한다."""
        output_dir = str(tmp_path / "reports" / "sub")
        result = {
            "issue_list": {"total_issues": 0, "issues": []},
            "checklist": {"total_checks": 0, "items": []},
            "summary": {"issue_summary": {}},
        }
        write_output_files(output_dir, result)
        assert (tmp_path / "reports" / "sub").exists()

    def test_writes_three_files(self, tmp_path: Path):
        """3개 JSON 파일을 생성한다."""
        output_dir = str(tmp_path)
        result = {
            "issue_list": {"total_issues": 1},
            "checklist": {"total_checks": 0},
            "summary": {"issue_summary": {}},
        }
        write_output_files(output_dir, result)

        assert (tmp_path / "issue-list.json").exists()
        assert (tmp_path / "checklist.json").exists()
        assert (tmp_path / "summary.json").exists()

    def test_json_content_valid(self, tmp_path: Path):
        """출력 JSON이 유효하다."""
        output_dir = str(tmp_path)
        result = {
            "issue_list": {"total_issues": 3, "issues": [{"id": "1"}]},
            "checklist": {"total_checks": 1},
            "summary": {"risk": "LOW"},
        }
        write_output_files(output_dir, result)

        content = json.loads(
            (tmp_path / "issue-list.json").read_text(encoding="utf-8")
        )
        assert content["total_issues"] == 3
        assert len(content["issues"]) == 1

    def test_korean_text_not_escaped(self, tmp_path: Path):
        """한국어 텍스트가 escape되지 않는다."""
        output_dir = str(tmp_path)
        result = {
            "issue_list": {"title": "한국어 테스트"},
            "checklist": {},
            "summary": {},
        }
        write_output_files(output_dir, result)

        raw = (tmp_path / "issue-list.json").read_text(encoding="utf-8")
        assert "한국어 테스트" in raw
        assert "\\u" not in raw


# ──────────────────────────────────────────────
# print_file_list
# ──────────────────────────────────────────────


class TestPrintFileList:
    """파일 목록 출력 테스트."""

    def test_prints_file_count(self):
        """파일 개수를 출력한다."""
        console = MagicMock()
        print_file_list(console, ["test.js", "calc.c"])
        # console.print가 파일 수를 포함하여 호출됐는지
        calls = [str(c) for c in console.print.call_args_list]
        assert any("2" in c for c in calls)

    def test_prints_language_label(self):
        """언어 레이블을 출력한다."""
        console = MagicMock()
        print_file_list(console, ["app.js"])
        calls = [str(c) for c in console.print.call_args_list]
        assert any("JavaScript" in c for c in calls)


# ──────────────────────────────────────────────
# print_issues
# ──────────────────────────────────────────────


class TestPrintIssues:
    """이슈 출력 테스트."""

    def test_no_issues_no_output(self):
        """이슈 없으면 출력 없음."""
        console = MagicMock()
        print_issues(console, {"issues": []})
        # Panel 출력 안 함
        assert not any(
            "Panel" in str(c) for c in console.print.call_args_list
        )

    def test_critical_issue_displayed(self):
        """Critical 이슈가 출력된다."""
        console = MagicMock()
        issue_list = {
            "issues": [
                {
                    "severity": "critical",
                    "issue_id": "C-001",
                    "title": "버퍼 오버플로우",
                    "file": "test.c",
                    "location": {"start_line": 10},
                    "fix": {"before": "strcpy(a, b);", "after": "strncpy(a, b, n);"},
                    "description": "위험",
                },
            ],
        }
        print_issues(console, issue_list)
        assert console.print.called

    def test_low_issues_not_displayed(self):
        """Low 이슈는 Before/After 미표시."""
        console = MagicMock()
        issue_list = {
            "issues": [
                {
                    "severity": "low",
                    "issue_id": "L-001",
                    "title": "코드 스타일",
                    "file": "test.js",
                    "location": {"start_line": 1},
                    "fix": {"before": "var x", "after": "let x"},
                    "description": "스타일",
                },
            ],
        }
        print_issues(console, issue_list)
        # Panel은 critical/high에만 사용
        calls_str = str(console.print.call_args_list)
        assert "Panel" not in calls_str


# ──────────────────────────────────────────────
# print_summary
# ──────────────────────────────────────────────


class TestPrintSummary:
    """요약 출력 테스트."""

    def test_deployment_allowed(self):
        """배포 가능 판정 출력."""
        console = MagicMock()
        summary = {
            "issue_summary": {
                "by_severity": {"critical": 0, "high": 1, "medium": 0, "low": 0},
            },
            "risk_assessment": {
                "deployment_risk": "LOW",
                "deployment_allowed": True,
                "blocking_issues": [],
            },
        }
        print_summary(console, summary, "./output")
        calls_str = str(console.print.call_args_list)
        assert "가능" in calls_str

    def test_deployment_blocked(self):
        """배포 불가 판정 출력."""
        console = MagicMock()
        summary = {
            "issue_summary": {
                "by_severity": {"critical": 2, "high": 0, "medium": 0, "low": 0},
            },
            "risk_assessment": {
                "deployment_risk": "CRITICAL",
                "deployment_allowed": False,
                "blocking_issues": ["C-001", "C-002"],
            },
        }
        print_summary(console, summary, "./output")
        calls_str = str(console.print.call_args_list)
        assert "불가" in calls_str


# ──────────────────────────────────────────────
# run_analysis
# ──────────────────────────────────────────────


class TestRunAnalysis:
    """분석 실행 통합 테스트."""

    @pytest.mark.asyncio
    async def test_returns_exit_ok_no_critical(self, tmp_path: Path):
        """Critical 없으면 EXIT_OK 반환."""
        mock_result = {
            "session_id": "test123",
            "execution_plan": {"sub_tasks": [{"task_id": "1"}]},
            "issue_list": {
                "total_issues": 1,
                "issues": [
                    {
                        "severity": "medium",
                        "issue_id": "M-001",
                        "title": "test",
                        "file": "t.js",
                        "location": {"start_line": 1},
                        "fix": {"before": "", "after": ""},
                        "description": "",
                    },
                ],
            },
            "checklist": {"total_checks": 0, "items": []},
            "summary": {
                "issue_summary": {
                    "by_severity": {"critical": 0, "high": 0, "medium": 1, "low": 0},
                },
                "risk_assessment": {
                    "deployment_risk": "LOW",
                    "deployment_allowed": True,
                    "blocking_issues": [],
                },
            },
            "errors": [],
        }

        with patch("mider.main.OrchestratorAgent") as MockOrch:
            instance = MockOrch.return_value
            instance.run = AsyncMock(return_value=mock_result)

            console = MagicMock()
            exit_code = await run_analysis(
                files=["test.js"],
                output_dir=str(tmp_path),
                model="gpt-4o",
                console=console,
            )

        assert exit_code == EXIT_OK
        assert (tmp_path / "issue-list.json").exists()

    @pytest.mark.asyncio
    async def test_returns_exit_critical(self, tmp_path: Path):
        """Critical 있으면 EXIT_CRITICAL_FOUND 반환."""
        mock_result = {
            "session_id": "test123",
            "execution_plan": {"sub_tasks": [{"task_id": "1"}]},
            "issue_list": {
                "total_issues": 1,
                "issues": [
                    {
                        "severity": "critical",
                        "issue_id": "C-001",
                        "title": "critical issue",
                        "file": "t.c",
                        "location": {"start_line": 1},
                        "fix": {"before": "", "after": ""},
                        "description": "",
                    },
                ],
            },
            "checklist": {"total_checks": 0, "items": []},
            "summary": {
                "issue_summary": {
                    "by_severity": {"critical": 1, "high": 0, "medium": 0, "low": 0},
                },
                "risk_assessment": {
                    "deployment_risk": "CRITICAL",
                    "deployment_allowed": False,
                    "blocking_issues": ["C-001"],
                },
            },
            "errors": [],
        }

        with patch("mider.main.OrchestratorAgent") as MockOrch:
            instance = MockOrch.return_value
            instance.run = AsyncMock(return_value=mock_result)

            console = MagicMock()
            exit_code = await run_analysis(
                files=["test.c"],
                output_dir=str(tmp_path),
                model="gpt-4o",
                console=console,
            )

        assert exit_code == EXIT_CRITICAL_FOUND

    @pytest.mark.asyncio
    async def test_file_error_returns_exit_2(self, tmp_path: Path):
        """파일 검증 에러만 있으면 EXIT_FILE_ERROR."""
        mock_result = {
            "session_id": "test123",
            "execution_plan": {"sub_tasks": []},
            "issue_list": {"total_issues": 0, "issues": []},
            "checklist": {"total_checks": 0, "items": []},
            "summary": {
                "issue_summary": {
                    "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
                },
                "risk_assessment": {
                    "deployment_risk": "LOW",
                    "deployment_allowed": True,
                    "blocking_issues": [],
                },
            },
            "errors": ["파일 없음: /nonexistent.js"],
        }

        with patch("mider.main.OrchestratorAgent") as MockOrch:
            instance = MockOrch.return_value
            instance.run = AsyncMock(return_value=mock_result)

            console = MagicMock()
            exit_code = await run_analysis(
                files=["/nonexistent.js"],
                output_dir=str(tmp_path),
                model="gpt-4o",
                console=console,
            )

        assert exit_code == EXIT_FILE_ERROR


# ──────────────────────────────────────────────
# main (통합)
# ──────────────────────────────────────────────


class TestMain:
    """main() 함수 통합 테스트."""

    def test_main_exits_without_api_key(self, monkeypatch):
        """API 키 없으면 exit 3."""
        monkeypatch.delenv("MIDER_API_KEY", raising=False)
        monkeypatch.setattr(
            "sys.argv", ["mider", "--files", "test.js"],
        )
        with pytest.raises(SystemExit) as exc_info:
            from mider.main import main
            main()
        assert exc_info.value.code == EXIT_LLM_ERROR

    def test_main_no_files_exits(self, monkeypatch):
        """--files 없으면 exit 2."""
        monkeypatch.setattr("sys.argv", ["mider"])
        with pytest.raises(SystemExit) as exc_info:
            from mider.main import main
            main()
        assert exc_info.value.code == 2  # argparse exit code
