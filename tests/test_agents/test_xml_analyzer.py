"""XMLAnalyzerAgent 단위 테스트."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from mider.agents.xml_analyzer import XMLAnalyzerAgent
from mider.models.analysis_result import AnalysisResult
from mider.tools.base_tool import ToolResult


SAMPLE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:w2="http://www.inswave.com/websquare"
      xmlns:ev="http://www.w3.org/2001/xml-events">
<head>
    <w2:dataList id="dlt_search">
        <w2:column id="svc_mgmt_num" dataType="text"/>
    </w2:dataList>
</head>
<body>
    <w2:button id="btn_search" ev:onclick="scwin.btn_search_onclick()"/>
    <w2:button id="btn_reset" ev:onclick="scwin.btn_reset_onclick()"/>
</body>
</html>
"""

SAMPLE_JS = """\
scwin.btn_search_onclick = function() {
    // search logic
};

scwin.btn_reset_onclick = function() {
    // reset logic
};
"""

SAMPLE_JS_MISSING_HANDLER = """\
scwin.btn_search_onclick = function() {
    // search logic
};
// btn_reset_onclick is missing
"""

DUPLICATE_ID_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body>
    <input id="txt_name"/>
    <input id="txt_name"/>
</body>
</html>
"""


def _make_issue(
    issue_id: str = "XML-001",
    category: str = "data_integrity",
    severity: str = "high",
    title: str = "중복 ID",
    file: str = "/app/screen.xml",
) -> dict:
    return {
        "issue_id": issue_id,
        "category": category,
        "severity": severity,
        "title": title,
        "description": "설명",
        "location": {"file": file, "line_start": 0, "line_end": 0},
        "fix": {"before": "before", "after": "after", "description": "fix"},
        "source": "static_analysis",
    }


def _make_llm_response(issues: list[dict] | None = None) -> str:
    return json.dumps({"issues": issues or []})


@pytest.fixture
def xml_file(tmp_path):
    f = tmp_path / "screen.xml"
    f.write_text(SAMPLE_XML, encoding="utf-8")
    return str(f)


@pytest.fixture
def xml_file_with_js(tmp_path):
    """XML + 대응 JS 파일."""
    xml_f = tmp_path / "screen.xml"
    xml_f.write_text(SAMPLE_XML, encoding="utf-8")
    js_f = tmp_path / "screen.js"
    js_f.write_text(SAMPLE_JS, encoding="utf-8")
    return str(xml_f)


@pytest.fixture
def xml_file_missing_handler(tmp_path):
    """XML + JS에 핸들러 누락."""
    xml_f = tmp_path / "screen.xml"
    xml_f.write_text(SAMPLE_XML, encoding="utf-8")
    js_f = tmp_path / "screen.js"
    js_f.write_text(SAMPLE_JS_MISSING_HANDLER, encoding="utf-8")
    return str(xml_f)


@pytest.fixture
def xml_file_duplicate_ids(tmp_path):
    f = tmp_path / "dup.xml"
    f.write_text(DUPLICATE_ID_XML, encoding="utf-8")
    return str(f)


@pytest.fixture
def agent():
    agent = XMLAnalyzerAgent(model="gpt-4o-mini")
    agent._llm_client = AsyncMock()
    return agent


class TestBasicBehavior:
    """기본 동작 테스트."""

    @pytest.mark.asyncio
    async def test_run_returns_analysis_result(self, agent, xml_file):
        """run()이 AnalysisResult 형식을 반환한다."""
        agent._llm_client.chat.return_value = _make_llm_response()
        result = await agent.run(task_id="task_1", file=xml_file, language="xml")
        assert result["task_id"] == "task_1"
        assert result["file"] == xml_file
        assert result["language"] == "xml"
        assert result["agent"] == "XMLAnalyzerAgent"
        assert isinstance(result["issues"], list)
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_schema_validation(self, agent, xml_file):
        """반환값이 AnalysisResult 스키마를 만족한다."""
        agent._llm_client.chat.return_value = _make_llm_response()
        result = await agent.run(task_id="task_1", file=xml_file, language="xml")
        validated = AnalysisResult.model_validate(result)
        assert validated.task_id == "task_1"

    @pytest.mark.asyncio
    async def test_run_with_issues(self, agent, xml_file):
        """LLM이 이슈를 반환하면 포함된다."""
        issue = _make_issue(file=xml_file)
        agent._llm_client.chat.return_value = _make_llm_response([issue])
        result = await agent.run(task_id="task_1", file=xml_file, language="xml")
        assert len(result["issues"]) == 1
        assert result["issues"][0]["issue_id"] == "XML-001"

    @pytest.mark.asyncio
    async def test_file_not_found(self, agent):
        """존재하지 않는 파일 → error."""
        agent._llm_client.chat.return_value = _make_llm_response()
        result = await agent.run(
            task_id="task_1", file="/nonexistent.xml", language="xml",
        )
        assert result["error"] is not None
        assert result["issues"] == []


class TestErrorFocusedPath:
    """Error-Focused 경로 테스트."""

    @pytest.mark.asyncio
    async def test_duplicate_ids_trigger_error_focused(self, agent, xml_file_duplicate_ids):
        """중복 ID가 있으면 Error-Focused 프롬프트 사용."""
        agent._llm_client.chat.return_value = _make_llm_response()
        await agent.run(
            task_id="task_1", file=xml_file_duplicate_ids, language="xml",
        )
        agent._llm_client.chat.assert_called_once()
        call_args = agent._llm_client.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        prompt = messages[1]["content"]
        assert "txt_name" in prompt

    @pytest.mark.asyncio
    async def test_missing_handler_trigger_error_focused(self, agent, xml_file_missing_handler):
        """JS 핸들러 누락이 있으면 Error-Focused 프롬프트 사용."""
        agent._llm_client.chat.return_value = _make_llm_response()
        await agent.run(
            task_id="task_1", file=xml_file_missing_handler, language="xml",
        )
        agent._llm_client.chat.assert_called_once()
        call_args = agent._llm_client.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        prompt = messages[1]["content"]
        assert "btn_reset_onclick" in prompt


class TestHeuristicPath:
    """Heuristic 경로 테스트."""

    @pytest.mark.asyncio
    async def test_no_errors_trigger_heuristic(self, agent, xml_file_with_js):
        """파서 오류/중복 ID/핸들러 누락 없으면 Heuristic."""
        agent._llm_client.chat.return_value = _make_llm_response()
        await agent.run(
            task_id="task_1", file=xml_file_with_js, language="xml",
        )
        agent._llm_client.chat.assert_called_once()


class TestJSCrossValidation:
    """JS 교차 검증 테스트."""

    @pytest.mark.asyncio
    async def test_js_file_matched(self, agent, xml_file_with_js):
        """대응 JS 파일이 자동 매칭된다."""
        agent._llm_client.chat.return_value = _make_llm_response()
        result = await agent.run(
            task_id="task_1", file=xml_file_with_js, language="xml",
        )
        # 오류 없이 완료 (JS에 모든 핸들러 있음)
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_missing_handlers_detected(self, agent, xml_file_missing_handler):
        """JS에 핸들러가 없으면 missing_handlers로 탐지."""
        agent._llm_client.chat.return_value = _make_llm_response()
        result = await agent.run(
            task_id="task_1", file=xml_file_missing_handler, language="xml",
        )
        assert result["error"] is None  # 에러가 아니라 이슈로 보고

    @pytest.mark.asyncio
    async def test_no_js_file(self, agent, xml_file):
        """대응 JS 파일 없어도 정상 동작."""
        agent._llm_client.chat.return_value = _make_llm_response()
        result = await agent.run(
            task_id="task_1", file=xml_file, language="xml",
        )
        assert result["error"] is None


class TestLLMFailure:
    """LLM 실패 시 동작 테스트."""

    @pytest.mark.asyncio
    async def test_llm_failure_returns_error(self, agent, xml_file):
        """LLM 실패 → error 필드."""
        agent._llm_client.chat.side_effect = Exception("API error")
        result = await agent.run(
            task_id="task_1", file=xml_file, language="xml",
        )
        assert result["error"] is not None
        assert result["issues"] == []

    @pytest.mark.asyncio
    async def test_llm_invalid_json(self, agent, xml_file):
        """LLM이 잘못된 JSON 반환."""
        agent._llm_client.chat.return_value = "not json"
        result = await agent.run(
            task_id="task_1", file=xml_file, language="xml",
        )
        assert result["error"] is not None


class TestAgentInit:
    """Agent 초기화 테스트."""

    def test_default_model(self):
        agent = XMLAnalyzerAgent()
        assert agent.model == "gpt-5-mini"
        assert agent.fallback_model == "gpt-5"

    def test_custom_model(self):
        agent = XMLAnalyzerAgent(model="custom-model", fallback_model="custom-fallback")
        assert agent.model == "custom-model"
        assert agent.fallback_model == "custom-fallback"
