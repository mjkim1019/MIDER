"""XMLAnalyzerAgent 단위 테스트."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mider.agents.xml_analyzer import XMLAnalyzerAgent
from mider.models.analysis_result import AnalysisResult
from mider.tools.base_tool import ToolResult
from mider.tools.static_analysis.xml_parser import ScriptBlock, js_line_to_xml_line


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

SAMPLE_XML_WITH_INLINE_JS = """\
<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:w2="http://www.inswave.com/websquare"
      xmlns:ev="http://www.w3.org/2001/xml-events">
<head>
    <w2:dataList id="dlt_search">
        <w2:column id="col1" dataType="text"/>
    </w2:dataList>
</head>
<body>
    <w2:button id="btn_search" ev:onclick="scwin.btn_search_onclick()"/>
</body>
<script type="text/javascript">
<![CDATA[
scwin.btn_search_onclick = function() {
    var result = ngmf.getData("dlt_search");
    for (var i = 0; i < result.length; i++) {
        console.log(result[i]);
    }
};
]]>
</script>
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
def xml_file_with_inline_js(tmp_path):
    """인라인 JS가 포함된 XML."""
    f = tmp_path / "screen_inline.xml"
    f.write_text(SAMPLE_XML_WITH_INLINE_JS, encoding="utf-8")
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
    # JS Analyzer도 mock — 인라인 JS 위임 시 LLM 호출 방지
    agent._js_analyzer._llm_client = AsyncMock()
    agent._js_analyzer._llm_client.chat.return_value = _make_llm_response()
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
        assert len(result["issues"]) >= 1

    @pytest.mark.asyncio
    async def test_file_not_found(self, agent):
        """존재하지 않는 파일 → error."""
        agent._llm_client.chat.return_value = _make_llm_response()
        result = await agent.run(
            task_id="task_1", file="/nonexistent.xml", language="xml",
        )
        assert result["error"] is not None
        assert result["issues"] == []


class TestXMLStructureAnalysis:
    """XML 구조 분석 테스트."""

    @pytest.mark.asyncio
    async def test_duplicate_ids_in_prompt(self, agent, xml_file_duplicate_ids):
        """중복 ID가 프롬프트에 포함된다."""
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
    async def test_missing_handler_in_prompt(self, agent, xml_file_missing_handler):
        """JS 핸들러 누락이 프롬프트에 포함된다."""
        agent._llm_client.chat.return_value = _make_llm_response()
        await agent.run(
            task_id="task_1", file=xml_file_missing_handler, language="xml",
        )
        agent._llm_client.chat.assert_called_once()
        call_args = agent._llm_client.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        prompt = messages[1]["content"]
        assert "btn_reset_onclick" in prompt

    @pytest.mark.asyncio
    async def test_datalist_summary_in_prompt(self, agent, xml_file):
        """dataList가 요약 형태로 프롬프트에 포함된다."""
        agent._llm_client.chat.return_value = _make_llm_response()
        await agent.run(task_id="task_1", file=xml_file, language="xml")
        call_args = agent._llm_client.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        prompt = messages[1]["content"]
        assert "dataList 요약" in prompt
        assert "dlt_search" in prompt


class TestInlineJSAnalysis:
    """인라인 JS 분석 테스트."""

    @pytest.mark.asyncio
    async def test_inline_js_delegated_to_js_analyzer(self, agent, xml_file_with_inline_js):
        """인라인 JS가 있으면 JSAnalyzer에 위임된다."""
        agent._llm_client.chat.return_value = _make_llm_response()
        agent._js_analyzer._llm_client.chat.return_value = _make_llm_response()

        result = await agent.run(
            task_id="task_1", file=xml_file_with_inline_js, language="xml",
        )
        assert result["error"] is None
        # JS Analyzer의 LLM도 호출됨
        agent._js_analyzer._llm_client.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_inline_js_skips_js_analyzer(self, agent, xml_file):
        """인라인 JS가 없으면 JSAnalyzer를 호출하지 않는다."""
        agent._llm_client.chat.return_value = _make_llm_response()

        await agent.run(task_id="task_1", file=xml_file, language="xml")
        # JS Analyzer의 LLM은 호출되지 않음
        agent._js_analyzer._llm_client.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_js_issues_line_remapped(self, agent, xml_file_with_inline_js):
        """JS 이슈의 라인 번호가 XML 라인으로 변환된다."""
        agent._llm_client.chat.return_value = _make_llm_response()
        # JS Analyzer가 L3에서 이슈를 발견했다고 mock
        js_issue = {
            "issue_id": "JS-001",
            "category": "code_quality",
            "severity": "medium",
            "title": "변수 재선언",
            "description": "i 재선언",
            "location": {"file": "/tmp/test.js", "line_start": 3, "line_end": 3},
            "fix": {"before": "var i", "after": "let i", "description": "let 사용"},
            "source": "llm",
        }
        agent._js_analyzer._llm_client.chat.return_value = _make_llm_response([js_issue])

        result = await agent.run(
            task_id="task_1", file=xml_file_with_inline_js, language="xml",
        )

        # JS 이슈가 결과에 포함됨
        js_issues = [i for i in result["issues"] if i.get("source") == "llm"]
        assert len(js_issues) >= 1
        # 라인 번호가 XML 기준으로 변환됨 (원본 L3 → XML offset + 3)
        loc = js_issues[0]["location"]
        assert loc["line_start"] > 3  # XML 오프셋 적용됨
        assert loc["file"] == xml_file_with_inline_js  # 파일 경로 원본으로 복원


class TestIssueMerging:
    """이슈 병합 테스트."""

    @pytest.mark.asyncio
    async def test_xml_and_js_issues_merged(self, agent, xml_file_with_inline_js):
        """XML 구조 이슈와 JS 이슈가 병합된다."""
        xml_issue = _make_issue(title="중복 ID")
        agent._llm_client.chat.return_value = _make_llm_response([xml_issue])

        js_issue = {
            "issue_id": "JS-001",
            "category": "code_quality",
            "severity": "medium",
            "title": "변수 재선언",
            "description": "설명",
            "location": {"file": "/tmp/t.js", "line_start": 1, "line_end": 1},
            "fix": {"before": "a", "after": "b", "description": "fix"},
            "source": "llm",
        }
        agent._js_analyzer._llm_client.chat.return_value = _make_llm_response([js_issue])

        result = await agent.run(
            task_id="task_1", file=xml_file_with_inline_js, language="xml",
        )

        assert len(result["issues"]) == 2
        # issue_id가 재번호됨
        assert result["issues"][0]["issue_id"] == "XML-001"
        assert result["issues"][1]["issue_id"] == "XML-002"


class TestJSCrossValidation:
    """JS 교차 검증 테스트."""

    @pytest.mark.asyncio
    async def test_js_file_matched(self, agent, xml_file_with_js):
        """대응 JS 파일이 자동 매칭된다."""
        agent._llm_client.chat.return_value = _make_llm_response()
        result = await agent.run(
            task_id="task_1", file=xml_file_with_js, language="xml",
        )
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_missing_handlers_detected(self, agent, xml_file_missing_handler):
        """JS에 핸들러가 없으면 missing_handlers로 탐지."""
        agent._llm_client.chat.return_value = _make_llm_response()
        result = await agent.run(
            task_id="task_1", file=xml_file_missing_handler, language="xml",
        )
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_no_js_file(self, agent, xml_file):
        """대응 JS 파일 없어도 정상 동작."""
        agent._llm_client.chat.return_value = _make_llm_response()
        result = await agent.run(
            task_id="task_1", file=xml_file, language="xml",
        )
        assert result["error"] is None


class TestLineMapping:
    """라인 번호 매핑 테스트."""

    def test_single_block_mapping(self):
        """단일 블록 오프셋 맵 변환."""
        offset_map = [ScriptBlock(xml_start=2514, js_start=1, length=12215)]
        assert js_line_to_xml_line(1, offset_map) == 2514
        assert js_line_to_xml_line(100, offset_map) == 2613
        assert js_line_to_xml_line(1000, offset_map) == 3513

    def test_multi_block_mapping(self):
        """다중 블록 오프셋 맵 변환."""
        offset_map = [
            ScriptBlock(xml_start=1511, js_start=1, length=91),
            ScriptBlock(xml_start=1609, js_start=92, length=12234),
        ]
        assert js_line_to_xml_line(1, offset_map) == 1511
        assert js_line_to_xml_line(91, offset_map) == 1601
        assert js_line_to_xml_line(92, offset_map) == 1609
        assert js_line_to_xml_line(100, offset_map) == 1617

    def test_fallback_for_unmapped_line(self):
        """맵에 없는 라인은 원본 값 그대로 반환."""
        offset_map = [ScriptBlock(xml_start=100, js_start=1, length=50)]
        assert js_line_to_xml_line(999, offset_map) == 999


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
