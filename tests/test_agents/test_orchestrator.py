"""OrchestratorAgent 단위 테스트."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mider.agents.orchestrator import OrchestratorAgent, _LANGUAGE_AGENT_MAP


# ──────────────────────────────────────────────
# 테스트용 헬퍼
# ──────────────────────────────────────────────

def _make_execution_plan(
    files: list[dict] | None = None,
) -> dict:
    """테스트용 ExecutionPlan dict 생성."""
    if files is None:
        files = [
            {
                "task_id": "task_1",
                "file": "/app/test.js",
                "language": "javascript",
                "priority": 1,
                "metadata": {
                    "file_size": 500,
                    "line_count": 30,
                    "last_modified": "2026-03-01T00:00:00",
                },
            },
        ]
    return {
        "sub_tasks": files,
        "dependencies": {
            "edges": [],
            "has_circular": False,
            "warnings": [],
        },
        "total_files": len(files),
        "estimated_time_seconds": 60,
    }


def _make_file_context(files: list[str] | None = None) -> dict:
    """테스트용 FileContext dict 생성."""
    if files is None:
        files = ["/app/test.js"]
    return {
        "file_contexts": [
            {
                "file": f,
                "language": "javascript",
                "imports": [],
                "calls": [],
                "patterns": [],
            }
            for f in files
        ],
        "dependencies": {"edges": [], "has_circular": False, "warnings": []},
        "common_patterns": {},
    }


def _make_analysis_result(
    task_id: str = "task_1",
    file: str = "/app/test.js",
    language: str = "javascript",
    issues: list | None = None,
) -> dict:
    """테스트용 AnalysisResult dict 생성."""
    return {
        "task_id": task_id,
        "file": file,
        "language": language,
        "agent": "TestAgent",
        "issues": issues or [],
        "analysis_time_seconds": 1.0,
        "llm_tokens_used": 100,
        "error": None,
    }


def _make_issue(
    issue_id: str = "JS-001",
    severity: str = "critical",
) -> dict:
    """테스트용 이슈 dict 생성."""
    return {
        "issue_id": issue_id,
        "category": "security",
        "severity": severity,
        "title": "XSS 취약점",
        "description": "innerHTML 사용",
        "location": {"file": "/app/test.js", "line_start": 10, "line_end": 10},
        "fix": {
            "before": "innerHTML = input;",
            "after": "textContent = input;",
            "description": "textContent 사용",
        },
        "source": "llm",
    }


def _make_report_result() -> dict:
    """테스트용 ReporterAgent 결과 생성."""
    return {
        "issue_list": {
            "generated_at": "2026-03-04T00:00:00Z",
            "session_id": "test-session",
            "total_issues": 0,
            "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
            "issues": [],
        },
        "checklist": {
            "generated_at": "2026-03-04T00:00:00Z",
            "session_id": "test-session",
            "total_checks": 0,
            "items": [],
        },
        "summary": {
            "analysis_metadata": {
                "session_id": "test-session",
                "analyzed_at": "2026-03-04T00:00:00Z",
                "total_files": 1,
                "total_lines": 30,
                "analysis_duration_seconds": 5.0,
                "total_llm_tokens": 100,
            },
            "issue_summary": {
                "total": 0,
                "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
                "by_category": {},
                "by_language": {},
                "by_file": {},
            },
            "risk_assessment": {
                "deployment_risk": "LOW",
                "deployment_allowed": True,
                "blocking_issues": [],
                "risk_description": "심각한 문제 없음",
            },
        },
    }


# ──────────────────────────────────────────────
# Fixture
# ──────────────────────────────────────────────

@pytest.fixture
def agent():
    """OrchestratorAgent (LLM 미사용)."""
    return OrchestratorAgent(model="gpt-4o", fallback_model="gpt-4-turbo")


@pytest.fixture
def js_file(tmp_path: Path) -> str:
    """테스트용 JS 파일 생성."""
    f = tmp_path / "test.js"
    f.write_text("function hello() { return 'hi'; }\n")
    return str(f)


@pytest.fixture
def c_file(tmp_path: Path) -> str:
    """테스트용 C 파일 생성."""
    f = tmp_path / "test.c"
    f.write_text('#include <stdio.h>\nvoid main() { printf("hello"); }\n')
    return str(f)


@pytest.fixture
def sql_file(tmp_path: Path) -> str:
    """테스트용 SQL 파일 생성."""
    f = tmp_path / "test.sql"
    f.write_text("SELECT * FROM users;\n")
    return str(f)


# ──────────────────────────────────────────────
# TestValidateAndExpandFiles
# ──────────────────────────────────────────────

class TestValidateAndExpandFiles:
    """파일 검증 및 glob 확장 테스트."""

    def test_valid_single_file(self, agent, js_file):
        """유효한 단일 파일이 통과한다."""
        valid, errors = agent._validate_and_expand_files([js_file])
        assert len(valid) == 1
        assert len(errors) == 0

    def test_nonexistent_file(self, agent):
        """존재하지 않는 파일은 에러로 분류된다."""
        valid, errors = agent._validate_and_expand_files(["/nonexistent/file.js"])
        assert len(valid) == 0
        assert len(errors) == 1
        assert "파일 없음" in errors[0]

    def test_unsupported_extension(self, agent, tmp_path):
        """지원하지 않는 확장자는 에러로 분류된다."""
        f = tmp_path / "test.py"
        f.write_text("print('hello')")
        valid, errors = agent._validate_and_expand_files([str(f)])
        assert len(valid) == 0
        assert "지원하지 않는 확장자" in errors[0]

    def test_multiple_files(self, agent, js_file, c_file, sql_file):
        """여러 파일이 모두 검증된다."""
        valid, errors = agent._validate_and_expand_files(
            [js_file, c_file, sql_file]
        )
        assert len(valid) == 3
        assert len(errors) == 0

    def test_duplicate_files_deduplicated(self, agent, js_file):
        """중복 파일이 제거된다."""
        valid, errors = agent._validate_and_expand_files([js_file, js_file])
        assert len(valid) == 1

    def test_glob_pattern_expansion(self, agent, tmp_path):
        """glob 패턴이 확장된다."""
        for name in ["a.js", "b.js", "c.js"]:
            (tmp_path / name).write_text(f"// {name}")
        pattern = str(tmp_path / "*.js")
        valid, errors = agent._validate_and_expand_files([pattern])
        assert len(valid) == 3

    def test_glob_no_match(self, agent, tmp_path):
        """매칭 없는 glob 패턴은 에러로 분류된다."""
        pattern = str(tmp_path / "*.nonexistent")
        valid, errors = agent._validate_and_expand_files([pattern])
        assert len(valid) == 0
        assert any("패턴과 매칭되는 파일 없음" in e for e in errors)

    def test_directory_rejected(self, agent, tmp_path):
        """디렉토리는 파일이 아니므로 거부된다."""
        d = tmp_path / "subdir"
        d.mkdir()
        # 디렉토리는 is_file() False 이므로
        valid, errors = agent._validate_and_expand_files([str(d)])
        assert len(valid) == 0
        assert "파일이 아님" in errors[0]

    def test_empty_input(self, agent):
        """빈 입력에 대해 빈 결과를 반환한다."""
        valid, errors = agent._validate_and_expand_files([])
        assert valid == []
        assert errors == []


# ──────────────────────────────────────────────
# TestValidateFiles
# ──────────────────────────────────────────────

class TestValidateFiles:
    """_validate_files 메서드 테스트."""

    def test_supported_extensions(self, agent, tmp_path):
        """지원하는 모든 확장자가 통과한다."""
        extensions = [".js", ".c", ".h", ".pc", ".sql"]
        paths = []
        for ext in extensions:
            f = tmp_path / f"test{ext}"
            f.write_text("// test")
            paths.append(str(f))

        valid, errors = agent._validate_files(paths)
        assert len(valid) == len(extensions)
        assert len(errors) == 0


# ──────────────────────────────────────────────
# TestBuildContextMap
# ──────────────────────────────────────────────

class TestBuildContextMap:
    """_build_context_map 메서드 테스트."""

    def test_builds_mapping(self, agent):
        """파일 경로 → SingleFileContext 매핑을 생성한다."""
        fc = _make_file_context(["/app/a.js", "/app/b.js"])
        ctx_map = agent._build_context_map(fc)
        assert "/app/a.js" in ctx_map
        assert "/app/b.js" in ctx_map
        assert ctx_map["/app/a.js"]["language"] == "javascript"

    def test_empty_context(self, agent):
        """빈 FileContext에 대해 빈 매핑을 반환한다."""
        ctx_map = agent._build_context_map({"file_contexts": []})
        assert ctx_map == {}


# ──────────────────────────────────────────────
# TestProgressCallback
# ──────────────────────────────────────────────

class TestProgressCallback:
    """Progress 콜백 테스트."""

    def test_callback_called(self):
        """콜백이 올바르게 호출된다."""
        callback = MagicMock()
        agent = OrchestratorAgent(progress_callback=callback)
        agent._report_progress(0, "테스트", 1, 3, "진행 중")

        callback.assert_called_once_with(
            phase=0, phase_name="테스트", current=1, total=3, message="진행 중",
        )

    def test_no_callback(self, agent):
        """콜백 없으면 에러 없이 무시된다."""
        agent._report_progress(0, "테스트", 1, 3, "진행 중")

    def test_callback_error_ignored(self):
        """콜백 에러가 무시된다."""
        callback = MagicMock(side_effect=RuntimeError("콜백 에러"))
        agent = OrchestratorAgent(progress_callback=callback)
        # 에러 없이 통과해야 함
        agent._report_progress(0, "테스트", 1, 3, "진행 중")


# ──────────────────────────────────────────────
# TestEmptyResult
# ──────────────────────────────────────────────

class TestEmptyResult:
    """빈 결과 생성 테스트."""

    def test_empty_result_structure(self, agent):
        """빈 결과의 구조가 올바르다."""
        result = agent._empty_result(["파일 없음: /nonexistent.js"])

        assert result["session_id"] == agent.session_id
        assert result["execution_plan"]["total_files"] == 0
        assert result["issue_list"]["total_issues"] == 0
        assert result["checklist"]["total_checks"] == 0
        assert result["summary"]["issue_summary"]["total"] == 0
        assert result["errors"] == ["파일 없음: /nonexistent.js"]

    def test_empty_result_risk_low(self, agent):
        """빈 결과의 위험도가 LOW이다."""
        result = agent._empty_result([])
        risk = result["summary"]["risk_assessment"]
        assert risk["deployment_risk"] == "LOW"
        assert risk["deployment_allowed"] is True


# ──────────────────────────────────────────────
# TestRunPipeline (통합 - mock Sub-Agents)
# ──────────────────────────────────────────────

class TestRunPipeline:
    """run() 전체 파이프라인 테스트 (Sub-Agent mock)."""

    @pytest.mark.asyncio
    async def test_no_valid_files_returns_empty(self, agent):
        """유효한 파일이 없으면 빈 결과를 반환한다."""
        result = await agent.run(files=["/nonexistent/file.js"])

        assert result["session_id"] == agent.session_id
        assert result["execution_plan"]["total_files"] == 0
        assert "issue_list" in result
        assert "checklist" in result
        assert "summary" in result
        assert "errors" in result

    @pytest.mark.asyncio
    async def test_full_pipeline_with_mocks(self, agent, js_file):
        """전체 파이프라인이 mock으로 정상 동작한다."""
        plan = _make_execution_plan([
            {
                "task_id": "task_1",
                "file": js_file,
                "language": "javascript",
                "priority": 1,
                "metadata": {
                    "file_size": 100,
                    "line_count": 5,
                    "last_modified": "2026-03-01T00:00:00",
                },
            },
        ])
        fc = _make_file_context([js_file])
        ar = _make_analysis_result(file=js_file)
        report = _make_report_result()

        # Mock sub-agents
        mock_classifier = AsyncMock()
        mock_classifier.run.return_value = plan
        agent._task_classifier = mock_classifier

        mock_collector = AsyncMock()
        mock_collector.run.return_value = fc
        agent._context_collector = mock_collector

        mock_reporter = AsyncMock()
        mock_reporter.run.return_value = report
        agent._reporter = mock_reporter

        # Mock analyzer
        mock_analyzer_cls = MagicMock()
        mock_analyzer_instance = AsyncMock()
        mock_analyzer_instance.run.return_value = ar
        mock_analyzer_cls.return_value = mock_analyzer_instance

        with patch.dict(_LANGUAGE_AGENT_MAP, {"javascript": mock_analyzer_cls}):
            result = await agent.run(files=[js_file])

        assert "session_id" in result
        assert "issue_list" in result
        assert "checklist" in result
        assert "summary" in result
        assert "execution_plan" in result
        assert "errors" in result

        mock_classifier.run.assert_called_once()
        mock_collector.run.assert_called_once()
        mock_reporter.run.assert_called_once()
        mock_analyzer_instance.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_multiple_files_different_languages(
        self, agent, js_file, c_file, sql_file,
    ):
        """다양한 언어의 파일이 각각 적절한 Analyzer로 라우팅된다."""
        plan = _make_execution_plan([
            {
                "task_id": "task_1",
                "file": js_file,
                "language": "javascript",
                "priority": 1,
                "metadata": {
                    "file_size": 100, "line_count": 5,
                    "last_modified": "2026-03-01T00:00:00",
                },
            },
            {
                "task_id": "task_2",
                "file": c_file,
                "language": "c",
                "priority": 2,
                "metadata": {
                    "file_size": 200, "line_count": 10,
                    "last_modified": "2026-03-01T00:00:00",
                },
            },
            {
                "task_id": "task_3",
                "file": sql_file,
                "language": "sql",
                "priority": 3,
                "metadata": {
                    "file_size": 50, "line_count": 3,
                    "last_modified": "2026-03-01T00:00:00",
                },
            },
        ])
        fc = _make_file_context([js_file, c_file, sql_file])
        report = _make_report_result()

        mock_classifier = AsyncMock()
        mock_classifier.run.return_value = plan
        agent._task_classifier = mock_classifier

        mock_collector = AsyncMock()
        mock_collector.run.return_value = fc
        agent._context_collector = mock_collector

        mock_reporter = AsyncMock()
        mock_reporter.run.return_value = report
        agent._reporter = mock_reporter

        # Analyzer mocks
        analyzers_called: list[str] = []

        def make_mock_analyzer(lang: str):
            cls = MagicMock()
            inst = AsyncMock()
            inst.run.return_value = _make_analysis_result(language=lang)
            inst.run.side_effect = lambda **kw: (
                analyzers_called.append(kw["language"]),
                _make_analysis_result(language=kw["language"]),
            )[-1]
            cls.return_value = inst
            return cls

        mock_map = {
            "javascript": make_mock_analyzer("javascript"),
            "c": make_mock_analyzer("c"),
            "sql": make_mock_analyzer("sql"),
        }

        with patch.dict(_LANGUAGE_AGENT_MAP, mock_map, clear=True):
            result = await agent.run(files=[js_file, c_file, sql_file])

        assert set(analyzers_called) == {"javascript", "c", "sql"}

    @pytest.mark.asyncio
    async def test_unsupported_language_in_plan(self, agent, js_file):
        """지원하지 않는 언어가 plan에 있으면 에러 결과를 반환한다."""
        plan = _make_execution_plan([
            {
                "task_id": "task_1",
                "file": js_file,
                "language": "ruby",  # 미지원
                "priority": 1,
                "metadata": {
                    "file_size": 100, "line_count": 5,
                    "last_modified": "2026-03-01T00:00:00",
                },
            },
        ])
        fc = _make_file_context([js_file])
        report = _make_report_result()

        mock_classifier = AsyncMock()
        mock_classifier.run.return_value = plan
        agent._task_classifier = mock_classifier

        mock_collector = AsyncMock()
        mock_collector.run.return_value = fc
        agent._context_collector = mock_collector

        mock_reporter = AsyncMock()
        mock_reporter.run.return_value = report
        agent._reporter = mock_reporter

        result = await agent.run(files=[js_file])

        # 리포트는 여전히 생성됨
        assert "issue_list" in result

    @pytest.mark.asyncio
    async def test_progress_callback_called(self, js_file):
        """Progress 콜백이 각 Phase에서 호출된다."""
        callback = MagicMock()
        agent = OrchestratorAgent(progress_callback=callback)

        plan = _make_execution_plan([
            {
                "task_id": "task_1",
                "file": js_file,
                "language": "javascript",
                "priority": 1,
                "metadata": {
                    "file_size": 100, "line_count": 5,
                    "last_modified": "2026-03-01T00:00:00",
                },
            },
        ])
        fc = _make_file_context([js_file])
        report = _make_report_result()

        agent._task_classifier = AsyncMock()
        agent._task_classifier.run.return_value = plan
        agent._context_collector = AsyncMock()
        agent._context_collector.run.return_value = fc
        agent._reporter = AsyncMock()
        agent._reporter.run.return_value = report

        mock_analyzer_cls = MagicMock()
        mock_analyzer_inst = AsyncMock()
        mock_analyzer_inst.run.return_value = _make_analysis_result(file=js_file)
        mock_analyzer_cls.return_value = mock_analyzer_inst

        with patch.dict(_LANGUAGE_AGENT_MAP, {"javascript": mock_analyzer_cls}):
            await agent.run(files=[js_file])

        # 콜백이 여러 번 호출되었는지 확인
        assert callback.call_count >= 6  # 입력검증 + Phase0 + Phase1 + Phase2 + Phase3

        # Phase 번호 추출
        phases_called = {call.kwargs.get("phase") for call in callback.call_args_list}
        assert {0, 1, 2, 3} == phases_called


# ──────────────────────────────────────────────
# TestAnalyzeSingleFile
# ──────────────────────────────────────────────

class TestAnalyzeSingleFile:
    """_analyze_single_file 메서드 테스트."""

    @pytest.mark.asyncio
    async def test_unsupported_language_returns_error(self, agent):
        """미지원 언어는 에러 결과를 반환한다."""
        result = await agent._analyze_single_file(
            task_id="task_1",
            file="/app/test.rb",
            language="ruby",
            file_context=None,
        )

        assert result["error"] == "지원하지 않는 언어: ruby"
        assert result["issues"] == []

    @pytest.mark.asyncio
    async def test_supported_language_dispatches(self, agent):
        """지원 언어는 적절한 Analyzer로 디스패치된다."""
        mock_cls = MagicMock()
        mock_inst = AsyncMock()
        mock_inst.run.return_value = _make_analysis_result()
        mock_cls.return_value = mock_inst

        with patch.dict(_LANGUAGE_AGENT_MAP, {"javascript": mock_cls}):
            result = await agent._analyze_single_file(
                task_id="task_1",
                file="/app/test.js",
                language="javascript",
                file_context=None,
            )

        mock_inst.run.assert_called_once()
        assert result["task_id"] == "task_1"

    @pytest.mark.asyncio
    async def test_analyzer_exception_returns_error(self, agent):
        """Analyzer가 예외를 발생시키면 에러 결과를 반환한다."""
        mock_cls = MagicMock()
        mock_inst = AsyncMock()
        mock_inst.run.side_effect = RuntimeError("LLM 장애")
        mock_cls.return_value = mock_inst

        with patch.dict(_LANGUAGE_AGENT_MAP, {"javascript": mock_cls}):
            result = await agent._analyze_single_file(
                task_id="task_1",
                file="/app/test.js",
                language="javascript",
                file_context=None,
            )

        assert result["error"] == "LLM 장애"
        assert result["issues"] == []

    @pytest.mark.asyncio
    async def test_analyzer_cached_by_language(self, agent):
        """같은 언어의 Analyzer 인스턴스가 재사용된다."""
        mock_cls = MagicMock()
        mock_inst = AsyncMock()
        mock_inst.run.return_value = _make_analysis_result()
        mock_cls.return_value = mock_inst

        with patch.dict(_LANGUAGE_AGENT_MAP, {"javascript": mock_cls}):
            await agent._analyze_single_file(
                task_id="task_1", file="/a.js",
                language="javascript", file_context=None,
            )
            await agent._analyze_single_file(
                task_id="task_2", file="/b.js",
                language="javascript", file_context=None,
            )

        # 생성자는 한 번만 호출
        mock_cls.assert_called_once()


# ──────────────────────────────────────────────
# TestMalformedSubTask
# ──────────────────────────────────────────────

class TestMalformedSubTask:
    """Phase 2에서 malformed sub-task 처리 테스트."""

    @pytest.mark.asyncio
    async def test_missing_key_in_subtask(self, agent, js_file):
        """sub-task에 필수 키가 없으면 에러로 처리된다."""
        plan = _make_execution_plan([
            {"task_id": "task_1"},  # file, language 누락
        ])
        fc = _make_file_context([js_file])
        report = _make_report_result()

        agent._task_classifier = AsyncMock()
        agent._task_classifier.run.return_value = plan
        agent._context_collector = AsyncMock()
        agent._context_collector.run.return_value = fc
        agent._reporter = AsyncMock()
        agent._reporter.run.return_value = report

        result = await agent.run(files=[js_file])

        # 파이프라인이 크래시하지 않고 완료됨
        assert "issue_list" in result


# ──────────────────────────────────────────────
# TestCallAgent
# ──────────────────────────────────────────────

class TestCallAgent:
    """_call_agent 메서드 테스트."""

    @pytest.mark.asyncio
    async def test_calls_agent_run(self, agent):
        """Agent의 run()을 호출하고 결과를 반환한다."""
        mock_agent = AsyncMock()
        mock_agent.run.return_value = {"result": "ok"}

        result = await agent._call_agent(mock_agent, key="value")

        mock_agent.run.assert_called_once_with(key="value")
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_propagates_exception(self, agent):
        """Agent 에러를 그대로 전파한다."""
        mock_agent = AsyncMock()
        mock_agent.run.side_effect = RuntimeError("Agent 실패")

        with pytest.raises(RuntimeError, match="Agent 실패"):
            await agent._call_agent(mock_agent)


# ──────────────────────────────────────────────
# TestSessionId
# ──────────────────────────────────────────────

class TestSessionId:
    """세션 ID 테스트."""

    def test_session_id_generated(self, agent):
        """세션 ID가 자동 생성된다."""
        assert len(agent.session_id) == 12

    def test_unique_session_ids(self):
        """서로 다른 Agent는 서로 다른 세션 ID를 가진다."""
        a1 = OrchestratorAgent()
        a2 = OrchestratorAgent()
        assert a1.session_id != a2.session_id


# ──────────────────────────────────────────────
# TestGlobExpand
# ──────────────────────────────────────────────

class TestGlobExpand:
    """_glob_expand 메서드 테스트."""

    def test_relative_glob(self, agent, tmp_path):
        """상대경로 glob 패턴이 확장된다."""
        (tmp_path / "a.js").write_text("// a")
        (tmp_path / "b.js").write_text("// b")

        result = agent._glob_expand(str(tmp_path / "*.js"))
        assert len(result) == 2

    def test_no_match_returns_empty(self, agent, tmp_path):
        """매칭 없으면 빈 리스트를 반환한다."""
        result = agent._glob_expand(str(tmp_path / "*.nonexistent"))
        assert result == []
