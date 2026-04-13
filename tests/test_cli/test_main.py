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
    get_base_dir,
    print_file_list,
    print_issues,
    print_summary,
    prompt_for_explain_plan,
    prompt_for_files,
    resolve_input_files,
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

    def test_no_args_ok(self):
        """인자 없이 실행 가능 (인터랙티브 모드)."""
        parser = build_parser()
        args = parser.parse_args([])
        assert args.files is None

    def test_files_single(self):
        """단일 파일 지정."""
        parser = build_parser()
        args = parser.parse_args(["-f", "test.js"])
        assert args.files == ["test.js"]

    def test_files_multiple(self):
        """다중 파일 지정."""
        parser = build_parser()
        args = parser.parse_args(["-f", "a.c", "b.pc", "c.sql"])
        assert args.files == ["a.c", "b.pc", "c.sql"]

    def test_output_default(self):
        """--output 기본값은 ./output."""
        parser = build_parser()
        args = parser.parse_args([])
        assert args.output == "./output"

    def test_output_custom(self):
        """--output 커스텀 경로."""
        parser = build_parser()
        args = parser.parse_args(["-o", "/tmp/reports"])
        assert args.output == "/tmp/reports"

    def test_model_option(self):
        """--model 옵션."""
        parser = build_parser()
        args = parser.parse_args(["-m", "gpt-4o-mini"])
        assert args.model == "gpt-4o-mini"

    def test_model_default_none(self):
        """--model 미지정 시 None."""
        parser = build_parser()
        args = parser.parse_args([])
        assert args.model is None

    def test_verbose_flag(self):
        """--verbose 플래그."""
        parser = build_parser()
        args = parser.parse_args(["-v"])
        assert args.verbose is True

    def test_verbose_default_false(self):
        """--verbose 미지정 시 False."""
        parser = build_parser()
        args = parser.parse_args([])
        assert args.verbose is False

    def test_explain_plan_option(self):
        """--explain-plan 옵션."""
        parser = build_parser()
        args = parser.parse_args(["-e", "/tmp/plan.txt"])
        assert args.explain_plan == "/tmp/plan.txt"

    def test_explain_plan_default_none(self):
        """--explain-plan 미지정 시 None."""
        parser = build_parser()
        args = parser.parse_args([])
        assert args.explain_plan is None

    def test_version(self):
        """--version 출력."""
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--version"])
        assert exc_info.value.code == 0


class TestPromptForFiles:
    """인터랙티브 파일 입력 테스트."""

    def test_single_file(self, monkeypatch):
        """단건 파일 입력."""
        monkeypatch.setattr("builtins.input", lambda _: "test.c")
        console = MagicMock()
        result = prompt_for_files(console)
        assert result == ["test.c"]

    def test_multiple_files(self, monkeypatch):
        """다건 파일 입력 (쉼표 구분)."""
        monkeypatch.setattr("builtins.input", lambda _: "a.c, b.pc, c.sql")
        console = MagicMock()
        result = prompt_for_files(console)
        assert result == ["a.c", "b.pc", "c.sql"]

    def test_empty_input_exits(self, monkeypatch):
        """빈 입력 시 종료."""
        monkeypatch.setattr("builtins.input", lambda _: "")
        console = MagicMock()
        with pytest.raises(SystemExit):
            prompt_for_files(console)

    def test_eof_exits(self, monkeypatch):
        """EOF 시 종료."""
        monkeypatch.setattr("builtins.input", MagicMock(side_effect=EOFError))
        console = MagicMock()
        with pytest.raises(SystemExit):
            prompt_for_files(console)


# ──────────────────────────────────────────────
# prompt_for_explain_plan
# ──────────────────────────────────────────────


class TestPromptForExplainPlan:
    """인터랙티브 Explain Plan 프롬프트 테스트."""

    def test_sql_file_triggers_prompt(self, monkeypatch, tmp_path):
        """SQL 파일 포함 시 프롬프트가 호출되고 파일 경로 반환."""
        explain_file = tmp_path / "explain.txt"
        explain_file.write_text("EXPLAIN PLAN OUTPUT")
        monkeypatch.setattr("builtins.input", lambda _: str(explain_file))

        result = prompt_for_explain_plan(
            ["/app/orders.sql"], tmp_path,
        )
        assert result == str(explain_file.resolve())

    def test_no_sql_file_skips_prompt(self, tmp_path):
        """SQL 파일 미포함 → 프롬프트 미호출, None 반환."""
        result = prompt_for_explain_plan(
            ["/app/calc.c", "/app/main.js"], tmp_path,
        )
        assert result is None

    def test_empty_input_returns_none(self, monkeypatch, tmp_path):
        """Enter(빈 입력) → None 반환."""
        monkeypatch.setattr("builtins.input", lambda _: "")

        result = prompt_for_explain_plan(
            ["/app/orders.sql"], tmp_path,
        )
        assert result is None

    def test_eof_returns_none(self, monkeypatch, tmp_path):
        """EOF → None 반환 (크래시하지 않음)."""
        monkeypatch.setattr(
            "builtins.input", MagicMock(side_effect=EOFError),
        )

        result = prompt_for_explain_plan(
            ["/app/orders.sql"], tmp_path,
        )
        assert result is None

    def test_nonexistent_file_returns_none(self, monkeypatch, tmp_path):
        """존재하지 않는 파일 → None 반환 + 경고."""
        monkeypatch.setattr("builtins.input", lambda _: "nonexistent.txt")

        result = prompt_for_explain_plan(
            ["/app/orders.sql"], tmp_path,
        )
        assert result is None

    def test_mixed_files_with_sql(self, monkeypatch, tmp_path):
        """C + SQL 혼합 입력 → SQL 감지하여 프롬프트 호출."""
        explain_file = tmp_path / "plan.txt"
        explain_file.write_text("plan data")
        monkeypatch.setattr("builtins.input", lambda _: str(explain_file))

        result = prompt_for_explain_plan(
            ["/app/calc.c", "/app/orders.sql"], tmp_path,
        )
        assert result == str(explain_file.resolve())


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
        """기본값은 settings.yaml의 orchestrator 모델."""
        monkeypatch.delenv("MIDER_MODEL", raising=False)
        result = resolve_model(None)
        assert result == "gpt-5"


# ──────────────────────────────────────────────
# validate_api_key
# ──────────────────────────────────────────────


class TestValidateApiKey:
    """API 키 검증 테스트."""

    def _clear_all_keys(self, monkeypatch):
        """모든 API 키 환경변수 제거."""
        for key in ["API_PROVIDER", "AICA_API_KEY", "AICA_ENDPOINT",
                     "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT",
                     "OPENAI_API_KEY"]:
            monkeypatch.delenv(key, raising=False)

    def test_openai_key(self, monkeypatch):
        """OpenAI 키가 설정된 경우 정상 통과."""
        self._clear_all_keys(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        validate_api_key()

    def test_azure_key(self, monkeypatch):
        """Azure 키가 설정된 경우 정상 통과."""
        self._clear_all_keys(monkeypatch)
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com/")
        validate_api_key()

    def test_aica_key(self, monkeypatch):
        """AICA 키가 설정된 경우 정상 통과."""
        self._clear_all_keys(monkeypatch)
        monkeypatch.setenv("API_PROVIDER", "aica")
        monkeypatch.setenv("AICA_API_KEY", "test-key")
        monkeypatch.setenv("AICA_ENDPOINT", "http://aica.test.com:3000")
        validate_api_key()

    def test_no_key_exits(self, monkeypatch):
        """어떤 키도 없으면 exit code 3."""
        self._clear_all_keys(monkeypatch)
        with pytest.raises(SystemExit) as exc_info:
            validate_api_key()
        assert exc_info.value.code == EXIT_LLM_ERROR

    def test_aica_key_without_endpoint_exits(self, monkeypatch):
        """AICA에서 ENDPOINT 없으면 exit code 3."""
        self._clear_all_keys(monkeypatch)
        monkeypatch.setenv("API_PROVIDER", "aica")
        monkeypatch.setenv("AICA_API_KEY", "test-key")
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
        write_output_files(output_dir, result, ["/app/test.c"])
        assert (tmp_path / "reports" / "sub").exists()

    def test_writes_three_files(self, tmp_path: Path):
        """3개 JSON 파일을 생성한다."""
        output_dir = str(tmp_path)
        result = {
            "issue_list": {"total_issues": 1},
            "checklist": {"total_checks": 0},
            "summary": {"issue_summary": {}},
        }
        write_output_files(output_dir, result, ["/app/test.c"])

        assert list(tmp_path.glob("*issue-list.json"))
        assert list(tmp_path.glob("*checklist.json"))
        assert list(tmp_path.glob("*summary.json"))

    def test_json_content_valid(self, tmp_path: Path):
        """출력 JSON이 유효하다."""
        output_dir = str(tmp_path)
        result = {
            "issue_list": {"total_issues": 3, "issues": [{"id": "1"}]},
            "checklist": {"total_checks": 1},
            "summary": {"risk": "LOW"},
        }
        write_output_files(output_dir, result, ["/app/test.c"])

        issue_files = list(tmp_path.glob("*issue-list.json"))
        assert issue_files
        content = json.loads(issue_files[0].read_text(encoding="utf-8"))
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
        write_output_files(output_dir, result, ["/app/test.c"])

        issue_files = list(tmp_path.glob("*issue-list.json"))
        assert issue_files
        raw = issue_files[0].read_text(encoding="utf-8")
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

    def test_low_issues_displayed_without_before_after(self):
        """Low 이슈도 출력되지만 Before/After 코드는 생략."""
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
        # LOW도 Panel로 표시됨
        calls_str = str(console.print.call_args_list)
        assert "Panel" in calls_str


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
        print_summary(console, summary, "./output", ["/app/test.c"])
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
        print_summary(console, summary, "./output", ["/app/test.c"])
        calls_str = str(console.print.call_args_list)
        assert "위험" in calls_str


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
        assert list(tmp_path.glob("*issue-list.json"))

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
        for key in ["API_PROVIDER", "AICA_API_KEY", "AICA_ENDPOINT",
                     "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT",
                     "OPENAI_API_KEY"]:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr("sys.argv", ["mider", "-f", "test.js"])
        # load_dotenv()가 .env 파일에서 키를 로드하지 않도록 차단
        monkeypatch.setattr("mider.main.load_dotenv", lambda **kwargs: None)
        with pytest.raises(SystemExit) as exc_info:
            from mider.main import main
            main()
        assert exc_info.value.code == EXIT_LLM_ERROR


# ──────────────────────────────────────────────
# get_base_dir
# ──────────────────────────────────────────────


class TestGetBaseDir:
    """get_base_dir() 테스트."""

    def test_dev_environment(self):
        """개발 환경에서는 프로젝트 루트를 반환한다."""
        base = get_base_dir()
        expected = Path(__file__).resolve().parent.parent.parent
        assert base == expected

    def test_frozen_environment(self, monkeypatch, tmp_path):
        """PyInstaller frozen 환경에서는 실행파일 디렉토리를 반환한다."""
        fake_exe = tmp_path / "dist" / "mider.exe"
        fake_exe.parent.mkdir(parents=True, exist_ok=True)
        fake_exe.touch()
        monkeypatch.setattr("sys.frozen", True, raising=False)
        monkeypatch.setattr("sys.executable", str(fake_exe))
        base = get_base_dir()
        assert base == fake_exe.parent


# ──────────────────────────────────────────────
# resolve_input_files
# ──────────────────────────────────────────────


class TestResolveInputFiles:
    """resolve_input_files() 테스트."""

    def test_absolute_path_passthrough(self, tmp_path):
        """절대경로 파일은 그대로 반환한다."""
        f = tmp_path / "test.js"
        f.write_text("// test")
        result = resolve_input_files(tmp_path, [str(f)])
        assert result == [str(f.resolve())]

    def test_input_folder_resolution(self, tmp_path):
        """input 폴더 내 파일명을 절대경로로 변환한다."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        f = input_dir / "app.c"
        f.write_text("int main() {}")
        result = resolve_input_files(tmp_path, ["app.c"])
        assert result == [str(f.resolve())]

    def test_missing_file_error(self, tmp_path):
        """존재하지 않는 파일은 에러 메시지를 출력하고 exit한다."""
        with pytest.raises(SystemExit) as exc_info:
            resolve_input_files(tmp_path, ["no_such_file.c"])
        assert exc_info.value.code == EXIT_FILE_ERROR

    def test_relative_path_existing_file(self, tmp_path, monkeypatch):
        """현재 디렉토리 기준 상대경로 파일이 존재하면 그대로 사용한다."""
        f = tmp_path / "local.sql"
        f.write_text("SELECT 1")
        monkeypatch.chdir(tmp_path)
        result = resolve_input_files(tmp_path, ["local.sql"])
        assert result == [str(f.resolve())]


# ──────────────────────────────────────────────
# get_base_dir
# ──────────────────────────────────────────────


class TestGetBaseDir:
    """get_base_dir() 테스트."""

    def test_dev_environment(self):
        """개발 환경에서는 프로젝트 루트를 반환한다."""
        base = get_base_dir()
        expected = Path(__file__).resolve().parent.parent.parent
        assert base == expected

    def test_frozen_environment(self, monkeypatch, tmp_path):
        """PyInstaller frozen 환경에서는 실행파일 디렉토리를 반환한다."""
        fake_exe = tmp_path / "dist" / "mider.exe"
        fake_exe.parent.mkdir(parents=True, exist_ok=True)
        fake_exe.touch()
        monkeypatch.setattr("sys.frozen", True, raising=False)
        monkeypatch.setattr("sys.executable", str(fake_exe))
        base = get_base_dir()
        assert base == fake_exe.parent


# ──────────────────────────────────────────────
# resolve_input_files (rglob)
# ──────────────────────────────────────────────


class TestResolveInputFilesRglob:
    """resolve_input_files() rglob 테스트."""

    def test_absolute_path_passthrough(self, tmp_path: Path):
        """절대경로 파일은 그대로 반환한다."""
        f = tmp_path / "test.js"
        f.write_text("// test")
        result = resolve_input_files(tmp_path, [str(f)])
        assert result == [str(f.resolve())]

    def test_input_folder_resolution(self, tmp_path: Path):
        """input 폴더 내 파일명을 절대경로로 변환한다."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        f = input_dir / "app.c"
        f.write_text("int main() {}")
        result = resolve_input_files(tmp_path, ["app.c"])
        assert result == [str(f.resolve())]

    def test_rglob_finds_file_in_subdirectory(self, tmp_path: Path):
        """base_dir 하위 서브디렉토리에 있는 파일을 이름만으로 찾는다."""
        sub_dir = tmp_path / "src" / "module"
        sub_dir.mkdir(parents=True)
        f = sub_dir / "deep_file.c"
        f.write_text("int x;")
        result = resolve_input_files(tmp_path, ["deep_file.c"])
        assert result == [str(f.resolve())]

    def test_rglob_multiple_matches_error(self, tmp_path: Path):
        """동일 파일명이 여러 서브디렉토리에 있으면 에러."""
        dir_a = tmp_path / "src" / "a"
        dir_b = tmp_path / "src" / "b"
        dir_a.mkdir(parents=True)
        dir_b.mkdir(parents=True)
        (dir_a / "dup.c").write_text("int a;")
        (dir_b / "dup.c").write_text("int b;")
        with pytest.raises(SystemExit) as exc_info:
            resolve_input_files(tmp_path, ["dup.c"])
        assert exc_info.value.code == EXIT_FILE_ERROR

    def test_missing_file_error(self, tmp_path: Path):
        """어디에도 없는 파일은 에러 메시지를 출력하고 exit한다."""
        with pytest.raises(SystemExit) as exc_info:
            resolve_input_files(tmp_path, ["no_such_file.c"])
        assert exc_info.value.code == EXIT_FILE_ERROR

    def test_input_folder_takes_precedence_over_rglob(self, tmp_path: Path):
        """input/ 폴더에 파일이 있으면 rglob보다 우선한다."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        input_file = input_dir / "priority.c"
        input_file.write_text("int input_ver;")
        other_dir = tmp_path / "src"
        other_dir.mkdir()
        (other_dir / "priority.c").write_text("int other_ver;")
        result = resolve_input_files(tmp_path, ["priority.c"])
        assert result == [str(input_file.resolve())]

    def test_mixed_found_and_not_found(self, tmp_path: Path):
        """일부 파일만 찾을 수 있는 경우: 찾은 파일은 반환."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        f = input_dir / "found.c"
        f.write_text("int ok;")
        result = resolve_input_files(tmp_path, ["found.c", "missing.c"])
        assert result == [str(f.resolve())]
