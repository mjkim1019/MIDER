"""ReasoningLogger 단위 테스트."""

from io import StringIO

from rich.console import Console

from mider.config.reasoning_logger import ReasoningLogger


def _make_logger(verbose: bool = True) -> tuple[ReasoningLogger, StringIO]:
    """테스트용 ReasoningLogger + 출력 캡처 버퍼."""
    buf = StringIO()
    console = Console(file=buf, no_color=True, width=120)
    return ReasoningLogger(console=console, verbose=verbose), buf


class TestVerboseMode:
    """verbose 모드 동작 테스트."""

    def test_verbose_true_outputs(self):
        """verbose=True이면 출력한다."""
        rl, buf = _make_logger(verbose=True)
        rl.scan("test message")
        output = buf.getvalue()
        assert "●" in output
        assert "test message" in output

    def test_verbose_false_no_output(self):
        """verbose=False이면 아무것도 출력하지 않는다."""
        rl, buf = _make_logger(verbose=False)
        rl.scan("should not appear")
        rl.detect("should not appear")
        rl.decision("should not appear")
        rl.prompt("should not appear")
        rl.process("should not appear")
        rl.llm_request("should not appear")
        rl.llm_response("should not appear")
        rl.result("should not appear")
        rl.phase_header(0, "TestAgent")
        assert buf.getvalue() == ""

    def test_enabled_property(self):
        """enabled 속성이 verbose와 일치한다."""
        rl_on, _ = _make_logger(verbose=True)
        rl_off, _ = _make_logger(verbose=False)
        assert rl_on.enabled is True
        assert rl_off.enabled is False


class TestPhaseHeader:
    """phase_header 출력 테스트."""

    def test_phase_header_format(self):
        """헤더에 Phase 번호와 Agent 이름이 포함된다."""
        rl, buf = _make_logger()
        rl.phase_header(2, "XMLAnalyzerAgent")
        output = buf.getvalue()
        assert "[Phase 2]" in output
        assert "XMLAnalyzerAgent" in output
        assert "──" in output


class TestLogMethods:
    """각 로그 메서드 출력 검증."""

    def test_scan_cyan_dot(self):
        """scan은 ● 마커를 포함한다."""
        rl, buf = _make_logger()
        rl.scan("Parse: 2 dataList")
        output = buf.getvalue()
        assert "●" in output
        assert "Parse: 2 dataList" in output

    def test_detect_message(self):
        """detect는 메시지를 출력한다."""
        rl, buf = _make_logger()
        rl.detect("btn_save 중복")
        assert "btn_save 중복" in buf.getvalue()

    def test_decision_with_reason(self):
        """decision은 근거(∵) 라인을 포함한다."""
        rl, buf = _make_logger()
        rl.decision("Error-Focused path", reason="duplicate_ids=1건")
        output = buf.getvalue()
        assert "Error-Focused path" in output
        assert "∵" in output
        assert "duplicate_ids=1건" in output

    def test_decision_without_reason(self):
        """reason 없이도 동작한다."""
        rl, buf = _make_logger()
        rl.decision("LLM skip")
        output = buf.getvalue()
        assert "LLM skip" in output
        assert "∵" not in output

    def test_prompt_message(self):
        """prompt는 메시지를 출력한다."""
        rl, buf = _make_logger()
        rl.prompt("xml_analyzer_error_focused")
        assert "xml_analyzer_error_focused" in buf.getvalue()

    def test_process_message(self):
        """process는 메시지를 출력한다."""
        rl, buf = _make_logger()
        rl.process("JSON 파싱 → 3개 이슈")
        assert "JSON 파싱" in buf.getvalue()

    def test_llm_request_message(self):
        """llm_request는 메시지를 출력한다."""
        rl, buf = _make_logger()
        rl.llm_request("gpt-5-mini 요청 중...")
        assert "gpt-5-mini" in buf.getvalue()

    def test_llm_response_message(self):
        """llm_response는 메시지를 출력한다."""
        rl, buf = _make_logger()
        rl.llm_response("4,682 tokens, 18.2초")
        assert "4,682 tokens" in buf.getvalue()


class TestResultWithIssues:
    """result 메서드 이슈 목록 출력 테스트."""

    def test_result_without_issues(self):
        """이슈 없이도 동작한다."""
        rl, buf = _make_logger()
        rl.result("0 issues")
        output = buf.getvalue()
        assert "0 issues" in output

    def test_result_with_issues(self):
        """이슈 목록이 severity + ID + title로 출력된다."""
        rl, buf = _make_logger()
        rl.result("2 issues", issues=[
            {"severity": "high", "issue_id": "XML-001", "title": "중복 ID"},
            {"severity": "low", "issue_id": "XML-002", "title": "네이밍"},
        ])
        output = buf.getvalue()
        assert "HIGH" in output
        assert "XML-001" in output
        assert "중복 ID" in output
        assert "LOW" in output
        assert "XML-002" in output
