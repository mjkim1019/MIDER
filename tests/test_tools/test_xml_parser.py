"""XMLParser 단위 테스트."""

import pytest

from mider.tools.base_tool import ToolExecutionError
from mider.tools.static_analysis.xml_parser import (
    XMLParser,
    _extract_handler_functions,
)


@pytest.fixture
def parser():
    return XMLParser()


# --- 샘플 XML ---

SAMPLE_WEBSQUARE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:w2="http://www.inswave.com/websquare"
      xmlns:ev="http://www.w3.org/2001/xml-events">
<head>
    <w2:dataList id="dlt_search">
        <w2:column id="svc_mgmt_num" dataType="text"/>
        <w2:column id="start_date" dataType="text"/>
    </w2:dataList>
    <w2:dataList id="dlt_result">
        <w2:column id="order_id" dataType="number"/>
        <w2:column id="amount" dataType="number"/>
    </w2:dataList>
</head>
<body>
    <w2:trigger id="tgr_onload" ev:onclick="scwin.fn_init()"/>
    <w2:input id="txt_search" ev:onchange="scwin.fn_validate()"/>
    <w2:button id="btn_search" ev:onclick="scwin.btn_search_onclick()"/>
    <w2:button id="btn_reset" ev:onclick="scwin.btn_reset_onclick()"/>
    <w2:grid id="grd_list"/>
</body>
</html>
"""

DUPLICATE_ID_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body>
    <input id="txt_name"/>
    <input id="txt_name"/>
    <button id="btn_ok"/>
    <button id="btn_ok"/>
    <button id="btn_ok"/>
</body>
</html>
"""

# 서로 다른 dataList에 동명 column id가 있는 XML (false positive 재현)
CROSS_DATALIST_SAME_COLUMN_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:w2="http://www.inswave.com/websquare">
<head>
    <w2:dataList id="DS_REQR_INFO">
        <w2:columnInfo>
            <w2:column id="req_sale_org_id" name="신청영업조직ID" dataType="text"/>
            <w2:column id="reqr_nm" name="신청인명" dataType="text"/>
        </w2:columnInfo>
        <w2:data use="true"/>
    </w2:dataList>
    <w2:dataList id="DS_FAX_INFO">
        <w2:columnInfo>
            <w2:column id="req_sale_org_id" name="판매점아이디" dataType="text"/>
            <w2:column id="fax_num" name="팩스번호" dataType="text"/>
        </w2:columnInfo>
        <w2:data use="true"/>
    </w2:dataList>
</head>
<body>
    <input id="txt_search"/>
</body>
</html>
"""

INVALID_XML = """\
<?xml version="1.0"?>
<root>
    <unclosed>
</root>
"""

NO_EVENTS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body>
    <div id="container">
        <span id="label1">Hello</span>
    </div>
</body>
</html>
"""


class TestXMLParserBasic:
    """기본 파싱 테스트."""

    def test_parse_valid_xml(self, parser, tmp_path):
        """정상 XML 파싱."""
        f = tmp_path / "screen.xml"
        f.write_text(SAMPLE_WEBSQUARE_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        assert result.success is True
        assert result.error is None

    def test_file_not_found(self, parser):
        """존재하지 않는 파일."""
        with pytest.raises(ToolExecutionError, match="파일 없음"):
            parser.execute(file="/nonexistent.xml")

    def test_invalid_xml(self, parser, tmp_path):
        """잘못된 XML → parse_errors 반환."""
        f = tmp_path / "bad.xml"
        f.write_text(INVALID_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        assert result.success is False
        assert len(result.data["parse_errors"]) > 0

    def test_empty_xml(self, parser, tmp_path):
        """빈 요소만 있는 XML."""
        f = tmp_path / "empty.xml"
        f.write_text('<root/>', encoding="utf-8")
        result = parser.execute(file=str(f))
        assert result.success is True
        assert result.data["data_lists"] == []
        assert result.data["events"] == []


class TestDataListExtraction:
    """데이터 리스트 추출 테스트."""

    def test_extract_data_lists(self, parser, tmp_path):
        """w2:dataList 추출."""
        f = tmp_path / "screen.xml"
        f.write_text(SAMPLE_WEBSQUARE_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        data_lists = result.data["data_lists"]
        assert len(data_lists) == 2
        assert data_lists[0]["id"] == "dlt_search"
        assert len(data_lists[0]["columns"]) == 2
        assert data_lists[0]["columns"][0]["id"] == "svc_mgmt_num"

    def test_data_list_column_types(self, parser, tmp_path):
        """컬럼 dataType 추출."""
        f = tmp_path / "screen.xml"
        f.write_text(SAMPLE_WEBSQUARE_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        result_list = result.data["data_lists"][1]
        assert result_list["id"] == "dlt_result"
        assert result_list["columns"][0]["dataType"] == "number"

    def test_no_data_lists(self, parser, tmp_path):
        """dataList 없는 XML."""
        f = tmp_path / "screen.xml"
        f.write_text(NO_EVENTS_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        assert result.data["data_lists"] == []


class TestEventExtraction:
    """이벤트 바인딩 추출 테스트."""

    def test_extract_events(self, parser, tmp_path):
        """ev:onclick 등 이벤트 추출."""
        f = tmp_path / "screen.xml"
        f.write_text(SAMPLE_WEBSQUARE_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        events = result.data["events"]
        assert len(events) == 4  # tgr_onload, txt_search, btn_search, btn_reset

    def test_event_handler_functions(self, parser, tmp_path):
        """이벤트 핸들러에서 함수명 추출."""
        f = tmp_path / "screen.xml"
        f.write_text(SAMPLE_WEBSQUARE_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        events = result.data["events"]
        # btn_search → scwin.btn_search_onclick()
        btn_event = [e for e in events if e["element_id"] == "btn_search"][0]
        assert "btn_search_onclick" in btn_event["handler_functions"]

    def test_event_element_id(self, parser, tmp_path):
        """이벤트의 요소 ID 추출."""
        f = tmp_path / "screen.xml"
        f.write_text(SAMPLE_WEBSQUARE_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        event_ids = {e["element_id"] for e in result.data["events"]}
        assert "tgr_onload" in event_ids
        assert "txt_search" in event_ids

    def test_no_events(self, parser, tmp_path):
        """이벤트 없는 XML."""
        f = tmp_path / "screen.xml"
        f.write_text(NO_EVENTS_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        assert result.data["events"] == []


class TestDuplicateIds:
    """중복 ID 검사 테스트."""

    def test_detect_duplicate_ids(self, parser, tmp_path):
        """중복 ID 탐지."""
        f = tmp_path / "screen.xml"
        f.write_text(DUPLICATE_ID_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        duplicates = result.data["duplicate_ids"]
        assert len(duplicates) == 2

    def test_duplicate_id_count(self, parser, tmp_path):
        """중복 ID 카운트 정확성."""
        f = tmp_path / "screen.xml"
        f.write_text(DUPLICATE_ID_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        dup_map = {d["id"]: d["count"] for d in result.data["duplicate_ids"]}
        assert dup_map["txt_name"] == 2
        assert dup_map["btn_ok"] == 3

    def test_no_duplicates(self, parser, tmp_path):
        """중복 없는 XML."""
        f = tmp_path / "screen.xml"
        f.write_text(SAMPLE_WEBSQUARE_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        assert result.data["duplicate_ids"] == []


class TestHandlerFunctionExtraction:
    """핸들러 함수명 추출 테스트."""

    def test_scwin_pattern(self):
        """scwin.funcName() 패턴."""
        funcs = _extract_handler_functions("scwin.btn_search_onclick()")
        assert funcs == ["btn_search_onclick"]

    def test_multiple_functions(self):
        """복수 함수 호출."""
        funcs = _extract_handler_functions("scwin.fn_init(); scwin.fn_load();")
        assert funcs == ["fn_init", "fn_load"]

    def test_no_scwin_fallback(self):
        """scwin 없이 직접 호출."""
        funcs = _extract_handler_functions("doSomething()")
        assert funcs == ["doSomething"]

    def test_empty_handler(self):
        """빈 핸들러."""
        funcs = _extract_handler_functions("")
        assert funcs == []

    def test_keyword_excluded(self):
        """제어문 키워드 제외."""
        funcs = _extract_handler_functions("if(true)")
        assert funcs == []


class TestComponentIds:
    """컴포넌트 ID 추출 테스트."""

    def test_extract_all_ids(self, parser, tmp_path):
        """UI 컴포넌트 + dataList ID 추출, column ID 제외."""
        f = tmp_path / "screen.xml"
        f.write_text(SAMPLE_WEBSQUARE_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        ids = {item["id"] for item in result.data["component_ids"]}
        # dataList ID는 document-level이므로 포함
        assert "dlt_search" in ids
        # body UI 컴포넌트 포함
        assert "btn_search" in ids
        assert "grd_list" in ids
        # column ID는 데이터 정의 내부이므로 제외
        assert "svc_mgmt_num" not in ids
        assert "start_date" not in ids
        assert "order_id" not in ids
        assert "amount" not in ids

    def test_column_ids_excluded_from_duplicates(self, parser, tmp_path):
        """서로 다른 dataList의 동명 column id는 중복으로 탐지하지 않는다."""
        f = tmp_path / "screen.xml"
        f.write_text(CROSS_DATALIST_SAME_COLUMN_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        # req_sale_org_id가 2개 dataList에 있지만 중복 아님
        assert result.data["duplicate_ids"] == []
        # dataList ID는 component_ids에 포함
        ids = {item["id"] for item in result.data["component_ids"]}
        assert "DS_REQR_INFO" in ids
        assert "DS_FAX_INFO" in ids
        # column ID는 component_ids에서 제외
        assert "req_sale_org_id" not in ids

    def test_body_duplicate_still_detected(self, parser, tmp_path):
        """body UI 컴포넌트 중복은 여전히 탐지한다 (회귀 테스트)."""
        f = tmp_path / "screen.xml"
        f.write_text(DUPLICATE_ID_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        duplicates = result.data["duplicate_ids"]
        dup_ids = {d["id"] for d in duplicates}
        assert "txt_name" in dup_ids
        assert "btn_ok" in dup_ids

    def test_duplicate_ids_have_line_numbers(self, parser, tmp_path):
        """중복 ID에 라인 번호가 포함된다."""
        f = tmp_path / "screen.xml"
        f.write_text(DUPLICATE_ID_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        for dup in result.data["duplicate_ids"]:
            assert "lines" in dup
            assert len(dup["lines"]) == dup["count"]
            assert all(isinstance(n, int) and n > 0 for n in dup["lines"])


class TestSourceLine:
    """lxml sourceline 속성 기반 라인 번호 수집 테스트."""

    def test_events_have_line_numbers(self, parser, tmp_path):
        """events 항목에 line 필드가 포함되고 실제 XML 라인을 가리킨다."""
        f = tmp_path / "screen.xml"
        f.write_text(SAMPLE_WEBSQUARE_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        events = result.data["events"]
        assert events, "이벤트가 추출되어야 한다"
        for event in events:
            assert "line" in event
            assert isinstance(event["line"], int)
            assert event["line"] > 0
        # btn_search는 SAMPLE_WEBSQUARE_XML 본문 기준 L18
        btn = [e for e in events if e["element_id"] == "btn_search"][0]
        assert btn["line"] == 18

    def test_data_lists_have_line_numbers(self, parser, tmp_path):
        """data_lists 항목에 line 필드가 포함된다."""
        f = tmp_path / "screen.xml"
        f.write_text(SAMPLE_WEBSQUARE_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        data_lists = result.data["data_lists"]
        assert data_lists
        for dl in data_lists:
            assert "line" in dl
            assert isinstance(dl["line"], int)
            assert dl["line"] > 0
        # SAMPLE_WEBSQUARE_XML 본문 기준 dlt_search=L6, dlt_result=L10
        by_id = {dl["id"]: dl["line"] for dl in data_lists}
        assert by_id["dlt_search"] == 6
        assert by_id["dlt_result"] == 10

    def test_component_ids_have_line_numbers(self, parser, tmp_path):
        """component_ids 항목에 line 필드가 포함된다."""
        f = tmp_path / "screen.xml"
        f.write_text(SAMPLE_WEBSQUARE_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        for comp in result.data["component_ids"]:
            assert "line" in comp
            assert isinstance(comp["line"], int)
            assert comp["line"] > 0

    def test_xml_parse_error_includes_line(self, parser, tmp_path):
        """XML 파싱 실패 시 parse_errors 메시지에 라인 정보가 포함된다."""
        f = tmp_path / "bad.xml"
        f.write_text(INVALID_XML, encoding="utf-8")
        result = parser.execute(file=str(f))
        assert result.success is False
        assert any("L" in msg for msg in result.data["parse_errors"])


class TestSecurity:
    """XXE/Billion Laughs 등 보안 방어 테스트."""

    def test_doctype_rejected(self, parser, tmp_path):
        """DOCTYPE 선언이 포함된 XML은 파싱을 거부한다."""
        f = tmp_path / "doctype.xml"
        f.write_text(
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE foo SYSTEM "file:///etc/passwd">\n'
            '<root/>\n',
            encoding="utf-8",
        )
        result = parser.execute(file=str(f))
        assert result.success is False
        assert any("DOCTYPE" in msg for msg in result.data["parse_errors"])

    def test_entity_rejected(self, parser, tmp_path):
        """ENTITY 선언이 포함된 XML은 파싱을 거부한다."""
        f = tmp_path / "entity.xml"
        f.write_text(
            '<?xml version="1.0"?>\n'
            '<!ENTITY xxe "evil">\n'
            '<root/>\n',
            encoding="utf-8",
        )
        result = parser.execute(file=str(f))
        assert result.success is False
