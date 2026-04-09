"""Analyzer Agent 표준 로그 출력 검증 테스트.

T36: 각 Analyzer가 분석 경로, 도구 결과, 후처리 과정을
표준 Python logging에 기록하는지 검증한다.
"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from mider.agents.c_analyzer import CAnalyzerAgent, _deduplicate_issues
from mider.agents.js_analyzer import JavaScriptAnalyzerAgent
from mider.agents.proc_analyzer import ProCAnalyzerAgent
from mider.agents.sql_analyzer import SQLAnalyzerAgent
from mider.agents.xml_analyzer import XMLAnalyzerAgent
from mider.tools.base_tool import ToolResult


def _make_llm_response(issues: list[dict] | None = None) -> str:
    return json.dumps({"issues": issues or []})


def _make_issue(
    issue_id: str = "X-001",
    severity: str = "medium",
    title: str = "테스트 이슈",
) -> dict:
    return {
        "issue_id": issue_id,
        "category": "code_quality",
        "severity": severity,
        "title": title,
        "description": "테스트 설명",
        "location": {"file": "/test.c", "line_start": 1, "line_end": 1},
        "fix": {"before": "a", "after": "b", "description": "fix"},
        "source": "llm",
    }


# ──────────────────────────────────────────────
# JS Analyzer 로그 테스트
# ──────────────────────────────────────────────


@pytest.fixture
def js_file(tmp_path):
    f = tmp_path / "app.js"
    f.write_text("var x = 1;\nconsole.log(x);\n")
    return str(f)


@pytest.fixture
def js_agent():
    agent = JavaScriptAnalyzerAgent(model="gpt-4o")
    agent._llm_client = AsyncMock()
    return agent


class TestJSAnalyzerLogging:
    """JS Analyzer 로그 검증."""

    @pytest.mark.asyncio
    async def test_no_eslint_logged(self, js_agent, js_file, caplog):
        """ESLint 결과 없을 때 로그에 '없음'이 출력된다."""
        js_agent._llm_client.chat.return_value = _make_llm_response()

        with caplog.at_level(logging.INFO, logger="mider.agents.js_analyzer"):
            await js_agent.run(task_id="t1", file=js_file)

        eslint_logs = [r for r in caplog.records if "ESLint:" in r.message]
        assert len(eslint_logs) >= 1
        assert "없음" in eslint_logs[0].message
        assert "app.js" in eslint_logs[0].message

    @pytest.mark.asyncio
    async def test_eslint_results_logged(self, js_agent, js_file, caplog):
        """ESLint 결과 있을 때 건수가 로그에 출력된다."""
        js_agent._eslint_runner = MagicMock()
        js_agent._eslint_runner.execute.return_value = ToolResult(
            success=True,
            data={
                "errors": [{"line": 1, "message": "err", "rule": "no-redeclare"}],
                "warnings": [{"line": 2, "message": "warn", "rule": "no-shadow"}],
            },
        )
        js_agent._llm_client.chat.return_value = _make_llm_response()

        with caplog.at_level(logging.INFO, logger="mider.agents.js_analyzer"):
            await js_agent.run(task_id="t1", file=js_file)

        eslint_logs = [r for r in caplog.records if "ESLint:" in r.message]
        assert len(eslint_logs) >= 1
        assert "errors=1" in eslint_logs[0].message
        assert "warnings=1" in eslint_logs[0].message


# ──────────────────────────────────────────────
# C Analyzer 로그 테스트
# ──────────────────────────────────────────────


@pytest.fixture
def c_file(tmp_path):
    f = tmp_path / "calc.c"
    f.write_text(
        '#include <stdio.h>\n'
        'int main() {\n'
        '    return 0;\n'
        '}\n'
    )
    return str(f)


@pytest.fixture
def c_agent():
    agent = CAnalyzerAgent(model="gpt-4o")
    agent._llm_client = AsyncMock()
    return agent


class TestCAnalyzerLogging:
    """C Analyzer 로그 검증."""

    @pytest.mark.asyncio
    async def test_heuristic_path_logged(self, c_agent, c_file, caplog):
        """clang-tidy 없고 ≤500줄일 때 Heuristic 경로 로그."""
        c_agent._llm_client.chat.return_value = _make_llm_response()

        with caplog.at_level(logging.INFO, logger="mider.agents.c_analyzer"):
            await c_agent.run(task_id="t1", file=c_file)

        path_logs = [r for r in caplog.records if "경로:" in r.message]
        assert len(path_logs) >= 1
        assert "Heuristic" in path_logs[0].message

    @pytest.mark.asyncio
    async def test_error_focused_path_logged(self, c_agent, c_file, caplog):
        """clang-tidy 결과 있을 때 Error-Focused 경로 로그."""
        c_agent._clang_tidy_runner = MagicMock()
        c_agent._clang_tidy_runner.execute.return_value = ToolResult(
            success=True,
            data={
                "warnings": [
                    {"line": 3, "message": "warn", "check": "bugprone-x", "severity": "warning"},
                ],
            },
        )
        c_agent._llm_client.chat.return_value = _make_llm_response()

        with caplog.at_level(logging.INFO, logger="mider.agents.c_analyzer"):
            await c_agent.run(task_id="t1", file=c_file)

        path_logs = [r for r in caplog.records if "경로:" in r.message]
        assert len(path_logs) >= 1
        assert "Error-Focused" in path_logs[0].message

    def test_dedup_removes_noise_issues(self, caplog):
        """dedup이 스레드 안전성 등 노이즈 이슈를 제거한다."""
        issues = [
            _make_issue(issue_id="C-001", title="스레드 안전성 문제"),  # 제거 대상
            _make_issue(issue_id="C-002", title="strcpy 버퍼 오버플로우"),
        ]

        with caplog.at_level(logging.INFO, logger="mider.agents.c_analyzer"):
            result = _deduplicate_issues(issues)

        # 스레드 안전성 이슈가 제거되어 1건만 남아야 함
        assert len(result) == 1
        assert result[0]["title"] == "strcpy 버퍼 오버플로우"


# ──────────────────────────────────────────────
# ProC Analyzer 로그 테스트
# ──────────────────────────────────────────────


@pytest.fixture
def proc_file(tmp_path):
    f = tmp_path / "sample.pc"
    f.write_text(
        '#include <stdio.h>\n'
        'EXEC SQL INCLUDE SQLCA;\n'
        'int main() {\n'
        '    EXEC SQL SELECT 1 INTO :val FROM DUAL;\n'
        '    return 0;\n'
        '}\n'
    )
    return str(f)


@pytest.fixture
def proc_agent():
    agent = ProCAnalyzerAgent(model="gpt-4o")
    agent._llm_client = AsyncMock()
    # V3 파이프라인 비활성화 → V1 fallback 테스트
    agent._run_v3_pipeline = AsyncMock(side_effect=Exception("V3 disabled for V1 test"))
    return agent


class TestProCAnalyzerLogging:
    """ProC Analyzer 로그 검증."""

    @pytest.mark.asyncio
    async def test_tool_results_logged(self, proc_agent, proc_file, caplog):
        """도구 실행 결과가 표준 로그에 출력된다."""
        proc_agent._llm_client.chat.return_value = _make_llm_response()

        with caplog.at_level(logging.INFO, logger="mider.agents.proc_analyzer"):
            await proc_agent.run(task_id="t1", file=proc_file)

        tool_logs = [r for r in caplog.records if "도구:" in r.message]
        assert len(tool_logs) >= 1
        assert "proc에러=" in tool_logs[0].message
        assert "SQL블록=" in tool_logs[0].message
        assert "Scanner=" in tool_logs[0].message

    @pytest.mark.asyncio
    async def test_path_logged(self, proc_agent, proc_file, caplog):
        """분석 경로가 표준 로그에 출력된다."""
        proc_agent._llm_client.chat.return_value = _make_llm_response()

        with caplog.at_level(logging.INFO, logger="mider.agents.proc_analyzer"):
            await proc_agent.run(task_id="t1", file=proc_file)

        path_logs = [r for r in caplog.records if "경로:" in r.message]
        assert len(path_logs) >= 1
        assert "ProC" in path_logs[0].message


# ──────────────────────────────────────────────
# SQL Analyzer 로그 테스트
# ──────────────────────────────────────────────


@pytest.fixture
def sql_file(tmp_path):
    f = tmp_path / "query.sql"
    f.write_text("SELECT * FROM users WHERE id = 1;\n")
    return str(f)


@pytest.fixture
def sql_agent():
    agent = SQLAnalyzerAgent(model="gpt-4o")
    agent._llm_client = AsyncMock()
    return agent


class TestSQLAnalyzerLogging:
    """SQL Analyzer 로그 검증."""

    @pytest.mark.asyncio
    async def test_tool_results_logged(self, sql_agent, sql_file, caplog):
        """도구 실행 결과가 표준 로그에 출력된다."""
        sql_agent._llm_client.chat.return_value = _make_llm_response()

        with caplog.at_level(logging.INFO, logger="mider.agents.sql_analyzer"):
            await sql_agent.run(task_id="t1", file=sql_file)

        tool_logs = [r for r in caplog.records if "도구:" in r.message]
        assert len(tool_logs) >= 1
        assert "문법에러=" in tool_logs[0].message
        assert "패턴=" in tool_logs[0].message
        assert "튜닝포인트=" in tool_logs[0].message

    @pytest.mark.asyncio
    async def test_path_logged(self, sql_agent, sql_file, caplog):
        """분석 경로가 표준 로그에 출력된다."""
        sql_agent._llm_client.chat.return_value = _make_llm_response()

        with caplog.at_level(logging.INFO, logger="mider.agents.sql_analyzer"):
            await sql_agent.run(task_id="t1", file=sql_file)

        path_logs = [r for r in caplog.records if "경로:" in r.message]
        assert len(path_logs) >= 1

    @pytest.mark.asyncio
    async def test_merge_logged_when_static_issues(self, sql_agent, sql_file, caplog):
        """정적 이슈 병합 시 로그가 출력된다."""
        # Explain Plan에서 CARTESIAN 튜닝 포인트 → 정적 이슈 생성
        sql_agent._explain_plan_parser = MagicMock()
        sql_agent._explain_plan_parser.execute.return_value = ToolResult(
            success=True,
            data={
                "steps": [{"id": 0, "operation": "SELECT STATEMENT", "cost": 500}],
                "tuning_points": [
                    {
                        "severity": "critical",
                        "operation": "MERGE JOIN CARTESIAN",
                        "object": "USERS",
                        "cost": 500,
                        "suggestion": "JOIN 조건 추가",
                    },
                ],
                "formatted_table": "dummy",
            },
        )
        sql_agent._llm_client.chat.return_value = _make_llm_response()

        with caplog.at_level(logging.INFO, logger="mider.agents.sql_analyzer"):
            await sql_agent.run(
                task_id="t1", file=sql_file, explain_plan_file="dummy.txt",
            )

        merge_logs = [r for r in caplog.records if "병합:" in r.message]
        assert len(merge_logs) >= 1
        assert "LLM" in merge_logs[0].message
        assert "정적" in merge_logs[0].message


# ──────────────────────────────────────────────
# XML Analyzer 로그 테스트
# ──────────────────────────────────────────────


@pytest.fixture
def xml_file(tmp_path):
    f = tmp_path / "screen.xml"
    f.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<w2:screen xmlns:w2="http://www.inswave.com/websquare">\n'
        '  <w2:body>\n'
        '    <w2:input id="txt_name"/>\n'
        '  </w2:body>\n'
        '</w2:screen>\n'
    )
    return str(f)


@pytest.fixture
def xml_agent():
    agent = XMLAnalyzerAgent(model="gpt-4o")
    agent._llm_client = AsyncMock()
    agent._js_analyzer._llm_client = AsyncMock()
    agent._js_analyzer._llm_client.chat.return_value = _make_llm_response()
    return agent


class TestXMLAnalyzerLogging:
    """XML Analyzer 로그 검증."""

    @pytest.mark.asyncio
    async def test_parse_results_logged(self, xml_agent, xml_file, caplog):
        """parse 결과가 표준 로그에 출력된다."""
        xml_agent._llm_client.chat.return_value = _make_llm_response()

        with caplog.at_level(logging.INFO, logger="mider.agents.xml_analyzer"):
            await xml_agent.run(task_id="t1", file=xml_file)

        parse_logs = [r for r in caplog.records if "parse:" in r.message]
        assert len(parse_logs) >= 1
        assert "dataList=" in parse_logs[0].message
        assert "events=" in parse_logs[0].message

    @pytest.mark.asyncio
    async def test_structure_issues_logged(self, xml_agent, xml_file, caplog):
        """XML 구조 이슈 건수가 로그에 출력된다."""
        xml_agent._llm_client.chat.return_value = _make_llm_response()

        with caplog.at_level(logging.INFO, logger="mider.agents.xml_analyzer"):
            await xml_agent.run(task_id="t1", file=xml_file)

        struct_logs = [r for r in caplog.records if "구조 이슈:" in r.message]
        assert len(struct_logs) >= 1

    @pytest.mark.asyncio
    async def test_js_validation_logged(self, xml_agent, xml_file, caplog):
        """JS 교차검증 결과가 표준 로그에 출력된다."""
        xml_agent._llm_client.chat.return_value = _make_llm_response()

        with caplog.at_level(logging.INFO, logger="mider.agents.xml_analyzer"):
            await xml_agent.run(task_id="t1", file=xml_file)

        js_logs = [r for r in caplog.records if "JS검증:" in r.message]
        assert len(js_logs) >= 1
