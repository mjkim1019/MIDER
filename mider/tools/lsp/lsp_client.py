"""LSPClient: Language Server Protocol 기반 심볼 탐색 Tool.

LSP 서버를 통해 심볼 정의(goto_definition), 참조(find_references),
타입 정보(hover)를 조회한다.

폐쇄망 환경에서 portable LSP 서버 바이너리를 사용하며,
바이너리가 없으면 graceful degradation (빈 결과 반환).
"""

import json
import logging
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult

logger = logging.getLogger(__name__)

# 패키지 기준 기본 경로 (mider/)
_PACKAGE_DIR = Path(__file__).parent.parent.parent

# 지원하는 LSP 액션
_SUPPORTED_ACTIONS = {"goto_definition", "find_references", "hover"}

# 언어별 LSP 서버 바이너리 후보
_LSP_SERVER_CANDIDATES: dict[str, list[Path]] = {
    "c": [
        _PACKAGE_DIR / "resources" / "binaries" / "clangd",
        Path("/usr/bin/clangd"),
        Path("/usr/local/bin/clangd"),
    ],
    "javascript": [
        _PACKAGE_DIR / "resources" / "binaries" / "typescript-language-server",
        Path("/usr/local/bin/typescript-language-server"),
    ],
}

# LSP 요청 타임아웃 (초)
_TIMEOUT_SECONDS = 30


def _detect_language(file_path: Path) -> str | None:
    """파일 확장자로 언어를 감지한다."""
    ext_map: dict[str, str] = {
        ".c": "c",
        ".h": "c",
        ".pc": "c",  # Pro*C도 C 계열
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "javascript",
        ".tsx": "javascript",
    }
    return ext_map.get(file_path.suffix.lower())


def _find_lsp_server(language: str) -> Path | None:
    """언어에 맞는 LSP 서버 바이너리를 찾는다."""
    candidates = _LSP_SERVER_CANDIDATES.get(language, [])
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _build_lsp_request(
    action: str,
    file_uri: str,
    line: int,
    column: int,
    request_id: int = 1,
) -> dict[str, Any]:
    """LSP JSON-RPC 요청을 생성한다."""
    method_map = {
        "goto_definition": "textDocument/definition",
        "find_references": "textDocument/references",
        "hover": "textDocument/hover",
    }

    params: dict[str, Any] = {
        "textDocument": {"uri": file_uri},
        "position": {"line": line, "character": column},
    }

    if action == "find_references":
        params["context"] = {"includeDeclaration": True}

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method_map[action],
        "params": params,
    }


def _parse_lsp_response(
    response: dict[str, Any],
    action: str,
) -> dict[str, Any]:
    """LSP 응답을 파싱하여 표준 형식으로 변환한다."""
    result = response.get("result")

    if result is None:
        return {"locations": [], "hover_info": None}

    if action == "hover":
        contents = result.get("contents", {})
        if isinstance(contents, dict):
            value = contents.get("value", "")
        elif isinstance(contents, str):
            value = contents
        else:
            value = str(contents)
        return {
            "locations": [],
            "hover_info": value,
        }

    # goto_definition, find_references → 위치 리스트
    if isinstance(result, dict):
        result = [result]

    locations = []
    for loc in result:
        uri = loc.get("uri", "")
        rng = loc.get("range", {})
        start = rng.get("start", {})
        parsed_uri = urlparse(uri)
        file_path = unquote(parsed_uri.path)
        locations.append({
            "file": file_path,
            "line": start.get("line", 0) + 1,  # LSP는 0-based
            "column": start.get("character", 0) + 1,
        })

    return {"locations": locations, "hover_info": None}


class LSPClient(BaseTool):
    """LSP 기반 심볼 탐색 Tool.

    LSP 서버를 통해 심볼 정의, 참조, 타입 정보를 조회한다.
    바이너리가 없으면 graceful degradation (빈 결과 반환).
    """

    def __init__(self, server_path: str | None = None) -> None:
        self._server_path = Path(server_path) if server_path else None

    def execute(
        self,
        *,
        action: str,
        file: str,
        line: int,
        column: int = 1,
    ) -> ToolResult:
        """LSP 요청을 실행한다.

        Args:
            action: LSP 액션 (goto_definition, find_references, hover)
            file: 대상 파일 경로
            line: 행 번호 (1-based)
            column: 열 번호 (1-based, 기본값 1)

        Returns:
            ToolResult (data: locations, hover_info, available)

        Raises:
            ToolExecutionError: 파일 없음, 지원하지 않는 액션 시
        """
        if action not in _SUPPORTED_ACTIONS:
            raise ToolExecutionError(
                "lsp_client",
                f"unsupported action: {action}. "
                f"supported: {', '.join(sorted(_SUPPORTED_ACTIONS))}",
            )

        file_path = Path(file)
        if not file_path.exists():
            raise ToolExecutionError(
                "lsp_client", f"file not found: {file}"
            )

        language = _detect_language(file_path)
        if language is None:
            return ToolResult(
                success=True,
                data={
                    "locations": [],
                    "hover_info": None,
                    "available": False,
                    "reason": f"unsupported file type: {file_path.suffix}",
                },
            )

        # LSP 서버 바이너리 탐색
        server = self._server_path or _find_lsp_server(language)
        if server is None or not server.exists():
            logger.warning(
                f"LSP server not found for {language}. "
                "Returning empty result (graceful degradation)."
            )
            return ToolResult(
                success=True,
                data={
                    "locations": [],
                    "hover_info": None,
                    "available": False,
                    "reason": "LSP server binary not found",
                },
            )

        # LSP 요청 구성 (initialize → initialized → didOpen → 실제 요청)
        resolved_path = file_path.resolve()
        file_uri = resolved_path.as_uri()
        root_uri = resolved_path.parent.as_uri()

        lsp_message = self._build_full_lsp_message(
            action=action,
            file_uri=file_uri,
            file_content=resolved_path.read_text(errors="replace"),
            root_uri=root_uri,
            language=language,
            line=line - 1,      # 1-based → 0-based
            column=column - 1,  # 1-based → 0-based
        )

        try:
            proc = subprocess.run(
                [str(server), "--stdio"],
                input=lsp_message,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
                cwd=str(resolved_path.parent),
            )
        except FileNotFoundError:
            logger.warning(f"LSP server binary not executable: {server}")
            return ToolResult(
                success=True,
                data={
                    "locations": [],
                    "hover_info": None,
                    "available": False,
                    "reason": f"LSP server not executable: {server}",
                },
            )
        except subprocess.TimeoutExpired:
            raise ToolExecutionError(
                "lsp_client",
                f"LSP request timeout after {_TIMEOUT_SECONDS}s",
            )

        # returncode 검사
        if proc.returncode != 0 and proc.stderr:
            logger.warning(
                f"LSP server exited with code {proc.returncode}: "
                f"{proc.stderr[:200]}"
            )

        # 응답 파싱 (request_id=2가 실제 요청 응답)
        response_data = self._extract_response(proc.stdout, request_id=2)
        if response_data is None:
            logger.debug(f"LSP empty response for {action} on {file}:{line}")
            return ToolResult(
                success=True,
                data={
                    "locations": [],
                    "hover_info": None,
                    "available": True,
                    "reason": "empty response from LSP server",
                },
            )

        parsed = _parse_lsp_response(response_data, action)

        logger.debug(
            f"LSP {action}: {file}:{line}:{column} → "
            f"{len(parsed['locations'])} locations"
        )

        return ToolResult(
            success=True,
            data={
                **parsed,
                "available": True,
            },
        )

    @staticmethod
    def _encode_lsp_message(obj: dict[str, Any]) -> str:
        """JSON-RPC 객체를 LSP 메시지로 인코딩한다."""
        body = json.dumps(obj)
        length = len(body.encode("utf-8"))
        return f"Content-Length: {length}\r\n\r\n{body}"

    def _build_full_lsp_message(
        self,
        *,
        action: str,
        file_uri: str,
        file_content: str,
        root_uri: str,
        language: str,
        line: int,
        column: int,
    ) -> str:
        """LSP 프로토콜 전체 시퀀스를 생성한다.

        initialize → initialized → didOpen → 실제 요청 → shutdown
        """
        # 1. initialize 요청
        init_request = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "processId": None,
                "rootUri": root_uri,
                "capabilities": {},
            },
        }

        # 2. initialized 통지 (id 없음)
        initialized_notification = {
            "jsonrpc": "2.0",
            "method": "initialized",
            "params": {},
        }

        # 3. textDocument/didOpen 통지
        did_open_notification = {
            "jsonrpc": "2.0",
            "method": "textDocument/didOpen",
            "params": {
                "textDocument": {
                    "uri": file_uri,
                    "languageId": language,
                    "version": 1,
                    "text": file_content,
                },
            },
        }

        # 4. 실제 요청
        actual_request = _build_lsp_request(
            action=action,
            file_uri=file_uri,
            line=line,
            column=column,
            request_id=2,
        )

        # 5. shutdown 요청
        shutdown_request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "shutdown",
            "params": None,
        }

        parts = [
            self._encode_lsp_message(init_request),
            self._encode_lsp_message(initialized_notification),
            self._encode_lsp_message(did_open_notification),
            self._encode_lsp_message(actual_request),
            self._encode_lsp_message(shutdown_request),
        ]
        return "".join(parts)

    def _extract_response(
        self, stdout: str, *, request_id: int = 2
    ) -> dict[str, Any] | None:
        """LSP stdout에서 특정 id의 JSON-RPC 응답을 추출한다."""
        if not stdout.strip():
            return None

        # Content-Length 헤더로 각 메시지를 파싱
        import re
        pattern = re.compile(r"Content-Length:\s*(\d+)\r\n\r\n")
        responses: list[dict[str, Any]] = []

        for match in pattern.finditer(stdout):
            start = match.end()
            length = int(match.group(1))
            body = stdout[start:start + length]
            try:
                responses.append(json.loads(body))
            except json.JSONDecodeError:
                continue

        # 요청 id에 해당하는 응답 찾기
        for resp in responses:
            if resp.get("id") == request_id:
                return resp

        # Content-Length 파싱 실패 시 fallback
        if not responses:
            try:
                return json.loads(stdout.strip())
            except json.JSONDecodeError:
                return None

        # id 매칭 실패 시 마지막 응답 반환
        return responses[-1] if responses else None
