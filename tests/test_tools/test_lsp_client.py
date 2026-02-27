"""LSPClient 단위 테스트."""

import json
from unittest.mock import MagicMock, patch

import pytest

from mider.tools.base_tool import ToolExecutionError
from mider.tools.lsp.lsp_client import (
    LSPClient,
    _build_lsp_request,
    _detect_language,
    _find_lsp_server,
    _parse_lsp_response,
)


@pytest.fixture
def mock_server(tmp_path):
    """mock LSP 서버 바이너리."""
    server = tmp_path / "clangd"
    server.write_text("#!/bin/sh\necho mock")
    server.chmod(0o755)
    return server


@pytest.fixture
def client(mock_server):
    """LSPClient with mock server."""
    return LSPClient(server_path=str(mock_server))


@pytest.fixture
def c_file(tmp_path):
    """테스트용 C 파일."""
    f = tmp_path / "test.c"
    f.write_text("int main() { return 0; }")
    return f


@pytest.fixture
def js_file(tmp_path):
    """테스트용 JS 파일."""
    f = tmp_path / "test.js"
    f.write_text("const x = 1;")
    return f


class TestDetectLanguage:
    def test_c_file(self, tmp_path):
        assert _detect_language(tmp_path / "test.c") == "c"

    def test_h_file(self, tmp_path):
        assert _detect_language(tmp_path / "test.h") == "c"

    def test_pc_file(self, tmp_path):
        assert _detect_language(tmp_path / "test.pc") == "c"

    def test_js_file(self, tmp_path):
        assert _detect_language(tmp_path / "test.js") == "javascript"

    def test_ts_file(self, tmp_path):
        assert _detect_language(tmp_path / "test.ts") == "javascript"

    def test_unsupported(self, tmp_path):
        assert _detect_language(tmp_path / "test.py") is None


class TestFindLSPServer:
    def test_finds_existing_binary(self, tmp_path):
        server = tmp_path / "clangd"
        server.write_text("#!/bin/sh")
        server.chmod(0o755)
        with patch(
            "mider.tools.lsp.lsp_client._LSP_SERVER_CANDIDATES",
            {"c": [server]},
        ):
            result = _find_lsp_server("c")
            assert result == server

    def test_returns_none_if_not_found(self):
        with patch(
            "mider.tools.lsp.lsp_client._LSP_SERVER_CANDIDATES",
            {"c": []},
        ):
            assert _find_lsp_server("c") is None

    def test_returns_none_for_unknown_language(self):
        assert _find_lsp_server("unknown") is None


class TestBuildLSPRequest:
    def test_goto_definition(self):
        req = _build_lsp_request(
            action="goto_definition",
            file_uri="file:///test.c",
            line=10,
            column=5,
        )
        assert req["method"] == "textDocument/definition"
        assert req["params"]["position"] == {"line": 10, "character": 5}

    def test_find_references(self):
        req = _build_lsp_request(
            action="find_references",
            file_uri="file:///test.c",
            line=0,
            column=0,
        )
        assert req["method"] == "textDocument/references"
        assert req["params"]["context"]["includeDeclaration"] is True

    def test_hover(self):
        req = _build_lsp_request(
            action="hover",
            file_uri="file:///test.c",
            line=5,
            column=3,
        )
        assert req["method"] == "textDocument/hover"


class TestParseLSPResponse:
    def test_goto_definition_single(self):
        response = {
            "result": {
                "uri": "file:///app/test.c",
                "range": {"start": {"line": 9, "character": 4}},
            }
        }
        parsed = _parse_lsp_response(response, "goto_definition")
        assert len(parsed["locations"]) == 1
        assert parsed["locations"][0]["file"] == "/app/test.c"
        assert parsed["locations"][0]["line"] == 10  # 0-based → 1-based
        assert parsed["locations"][0]["column"] == 5

    def test_find_references_multiple(self):
        response = {
            "result": [
                {
                    "uri": "file:///a.c",
                    "range": {"start": {"line": 0, "character": 0}},
                },
                {
                    "uri": "file:///b.c",
                    "range": {"start": {"line": 19, "character": 7}},
                },
            ]
        }
        parsed = _parse_lsp_response(response, "find_references")
        assert len(parsed["locations"]) == 2
        assert parsed["locations"][1]["file"] == "/b.c"
        assert parsed["locations"][1]["line"] == 20

    def test_hover_dict_contents(self):
        response = {
            "result": {
                "contents": {"kind": "plaintext", "value": "int main()"},
            }
        }
        parsed = _parse_lsp_response(response, "hover")
        assert parsed["hover_info"] == "int main()"
        assert parsed["locations"] == []

    def test_hover_string_contents(self):
        response = {
            "result": {"contents": "void foo()"},
        }
        parsed = _parse_lsp_response(response, "hover")
        assert parsed["hover_info"] == "void foo()"

    def test_null_result(self):
        parsed = _parse_lsp_response({"result": None}, "goto_definition")
        assert parsed["locations"] == []
        assert parsed["hover_info"] is None


class TestLSPClient:
    def test_unsupported_action(self, client, c_file):
        with pytest.raises(ToolExecutionError, match="unsupported action"):
            client.execute(action="invalid", file=str(c_file), line=1)

    def test_file_not_found(self, client):
        with pytest.raises(ToolExecutionError, match="file not found"):
            client.execute(
                action="goto_definition", file="/nonexistent.c", line=1
            )

    def test_unsupported_file_type(self, client, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("x = 1")
        result = client.execute(
            action="goto_definition", file=str(f), line=1
        )
        assert result.success is True
        assert result.data["available"] is False
        assert "unsupported file type" in result.data["reason"]

    def test_no_server_graceful_degradation(self, tmp_path):
        """서버 바이너리가 없을 때 빈 결과 반환."""
        client = LSPClient(server_path="/nonexistent/clangd")
        f = tmp_path / "test.c"
        f.write_text("int main() {}")
        result = client.execute(
            action="goto_definition", file=str(f), line=1
        )
        assert result.success is True
        assert result.data["available"] is False
        assert result.data["locations"] == []
        assert "not found" in result.data["reason"]

    @patch("mider.tools.lsp.lsp_client.subprocess.run")
    def test_goto_definition_success(self, mock_run, client, c_file):
        lsp_response = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "uri": f"file://{c_file}",
                "range": {"start": {"line": 0, "character": 4}},
            },
        })
        mock_run.return_value = MagicMock(
            stdout=f"Content-Length: {len(lsp_response)}\r\n\r\n{lsp_response}",
            stderr="",
            returncode=0,
        )

        result = client.execute(
            action="goto_definition", file=str(c_file), line=1, column=5
        )
        assert result.success is True
        assert result.data["available"] is True
        assert len(result.data["locations"]) == 1

    @patch("mider.tools.lsp.lsp_client.subprocess.run")
    def test_find_references_success(self, mock_run, client, c_file):
        lsp_response = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": [
                {
                    "uri": f"file://{c_file}",
                    "range": {"start": {"line": 0, "character": 0}},
                },
            ],
        })
        mock_run.return_value = MagicMock(
            stdout=f"Content-Length: {len(lsp_response)}\r\n\r\n{lsp_response}",
            stderr="",
            returncode=0,
        )

        result = client.execute(
            action="find_references", file=str(c_file), line=1
        )
        assert result.data["available"] is True
        assert len(result.data["locations"]) == 1

    @patch("mider.tools.lsp.lsp_client.subprocess.run")
    def test_hover_success(self, mock_run, client, c_file):
        lsp_response = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "contents": {"kind": "plaintext", "value": "int main()"},
            },
        })
        mock_run.return_value = MagicMock(
            stdout=f"Content-Length: {len(lsp_response)}\r\n\r\n{lsp_response}",
            stderr="",
            returncode=0,
        )

        result = client.execute(
            action="hover", file=str(c_file), line=1, column=5
        )
        assert result.data["hover_info"] == "int main()"

    @patch("mider.tools.lsp.lsp_client.subprocess.run")
    def test_empty_response(self, mock_run, client, c_file):
        mock_run.return_value = MagicMock(
            stdout="",
            stderr="",
            returncode=0,
        )

        result = client.execute(
            action="goto_definition", file=str(c_file), line=1
        )
        assert result.success is True
        assert result.data["locations"] == []

    @patch("mider.tools.lsp.lsp_client.subprocess.run")
    def test_timeout(self, mock_run, client, c_file):
        import subprocess as sp

        mock_run.side_effect = sp.TimeoutExpired(cmd="clangd", timeout=30)
        with pytest.raises(ToolExecutionError, match="timeout"):
            client.execute(
                action="goto_definition", file=str(c_file), line=1
            )

    @patch("mider.tools.lsp.lsp_client.subprocess.run")
    def test_server_not_executable(self, mock_run, client, c_file):
        mock_run.side_effect = FileNotFoundError("clangd not found")
        result = client.execute(
            action="goto_definition", file=str(c_file), line=1
        )
        assert result.success is True
        assert result.data["available"] is False

    @patch("mider.tools.lsp.lsp_client._find_lsp_server", return_value=None)
    def test_no_server_auto_detect(self, mock_find, tmp_path):
        """server_path 없이 자동 탐색 실패 시 graceful degradation."""
        client = LSPClient()
        f = tmp_path / "test.c"
        f.write_text("int x;")
        result = client.execute(
            action="goto_definition", file=str(f), line=1
        )
        assert result.success is True
        assert result.data["available"] is False
