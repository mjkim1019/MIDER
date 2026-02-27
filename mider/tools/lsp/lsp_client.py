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
        locations.append({
            "file": uri.replace("file://", ""),
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
        column: int = 0,
    ) -> ToolResult:
        """LSP 요청을 실행한다.

        Args:
            action: LSP 액션 (goto_definition, find_references, hover)
            file: 대상 파일 경로
            line: 행 번호 (1-based)
            column: 열 번호 (1-based, 기본값 0)

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

        # LSP 요청 구성
        file_uri = file_path.resolve().as_uri()
        lsp_request = _build_lsp_request(
            action=action,
            file_uri=file_uri,
            line=line - 1,      # 1-based → 0-based
            column=column - 1,  # 1-based → 0-based
        )

        request_body = json.dumps(lsp_request)
        content_length = len(request_body.encode("utf-8"))
        lsp_message = (
            f"Content-Length: {content_length}\r\n"
            f"\r\n"
            f"{request_body}"
        )

        try:
            proc = subprocess.run(
                [str(server), "--stdio"],
                input=lsp_message,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
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

        # 응답 파싱
        response_data = self._extract_response(proc.stdout)
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

    def _extract_response(self, stdout: str) -> dict[str, Any] | None:
        """LSP stdout에서 JSON-RPC 응답을 추출한다."""
        if not stdout.strip():
            return None

        # Content-Length 헤더 이후 JSON 본문 추출
        parts = stdout.split("\r\n\r\n", 1)
        if len(parts) == 2:
            body = parts[1]
        else:
            # 헤더 없이 JSON만 온 경우
            body = stdout

        try:
            return json.loads(body)
        except json.JSONDecodeError:
            # 여러 응답이 연결된 경우 마지막 JSON 추출 시도
            lines = body.strip().splitlines()
            for line in reversed(lines):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        return json.loads(line)
                    except json.JSONDecodeError:
                        continue
            return None
