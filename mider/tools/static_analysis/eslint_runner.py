"""ESLintRunner: JavaScript 정적 분석 Tool.

ESLint를 실행하여 JavaScript 파일의 오류와 경고를 추출한다.
폐쇄망 환경에서 portable node + eslint 바이너리를 사용한다.
"""

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult

logger = logging.getLogger(__name__)

# 패키지 기준 기본 경로
_PACKAGE_DIR = Path(__file__).parent.parent.parent  # mider/
_DEFAULT_CONFIG = _PACKAGE_DIR / "resources" / "lint-configs" / ".eslintrc.json"
_DEFAULT_BINARY = _PACKAGE_DIR / "resources" / "binaries" / "node"
_BINARIES_DIR = _PACKAGE_DIR / "resources" / "binaries"

# ESLint 실행 타임아웃 (초)
_TIMEOUT_SECONDS = 60


class ESLintRunner(BaseTool):
    """ESLint 정적 분석 Tool.

    JavaScript 파일에 대해 ESLint를 실행하고
    errors/warnings 리스트를 반환한다.
    """

    def __init__(
        self,
        binary_path: str | None = None,
        config_path: str | None = None,
    ) -> None:
        self._binary = Path(binary_path) if binary_path else _DEFAULT_BINARY
        self._config = Path(config_path) if config_path else _DEFAULT_CONFIG

    def execute(
        self,
        *,
        file: str,
        config: str | None = None,
    ) -> ToolResult:
        """ESLint를 실행하여 분석 결과를 반환한다.

        Args:
            file: 분석할 JavaScript 파일 경로
            config: ESLint 설정 파일 경로 (없으면 기본 설정 사용)

        Returns:
            ToolResult (data: errors, warnings, total_errors, total_warnings)

        Raises:
            ToolExecutionError: 바이너리/파일 없음, 실행 실패 시
        """
        file_path = Path(file)
        if not file_path.exists():
            raise ToolExecutionError("eslint_runner", f"file not found: {file}")

        config_path = Path(config) if config else self._config
        if not config_path.exists():
            raise ToolExecutionError(
                "eslint_runner", f"config not found: {config_path}"
            )

        # 바이너리 존재 확인 (resources → 시스템 PATH → skip)
        if not self._binary.exists():
            import shutil
            system_node = shutil.which("node")
            if system_node:
                self._binary = Path(system_node)
            else:
                logger.info(
                    "node 바이너리 없음 — Heuristic 모드로 분석합니다. "
                    "(resources/binaries/ 또는 시스템 PATH에 node가 없음)"
                )
                return ToolResult(
                    success=True,
                    data={"errors": [], "warnings": [], "skipped": True},
                )

        # ESLint 실행 (JSON 출력 형식)
        cmd = [
            str(self._binary),
            str(self._find_eslint()),
            "--no-eslintrc",
            "--config", str(config_path),
            "--format", "json",
            "--no-color",
            str(file_path),
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            raise ToolExecutionError(
                "eslint_runner",
                f"binary not found: {self._binary}",
            )
        except subprocess.TimeoutExpired:
            raise ToolExecutionError(
                "eslint_runner",
                f"timeout after {_TIMEOUT_SECONDS}s: {file}",
            )

        return self._parse_output(proc.stdout, proc.stderr, proc.returncode)

    def _find_eslint(self) -> Path:
        """ESLint 실행 파일 경로를 찾는다."""
        # node_modules 내 eslint CLI
        candidates = [
            # .js 엔트리포인트 우선 (node로 직접 실행 가능)
            _BINARIES_DIR / "node_modules" / "eslint" / "bin" / "eslint.js",
            self._binary.parent / "node_modules" / "eslint" / "bin" / "eslint.js",
            _BINARIES_DIR / "eslint",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate

        raise ToolExecutionError(
            "eslint_runner",
            "eslint binary not found in resources/binaries/",
        )

    def _parse_output(
        self,
        stdout: str,
        stderr: str,
        returncode: int,
    ) -> ToolResult:
        """ESLint JSON 출력을 파싱한다."""
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        if not stdout.strip():
            # ESLint가 출력 없이 종료한 경우
            if returncode != 0 and stderr:
                raise ToolExecutionError(
                    "eslint_runner", f"execution failed: {stderr[:500]}"
                )
            return ToolResult(
                success=True,
                data={
                    "errors": [],
                    "warnings": [],
                    "total_errors": 0,
                    "total_warnings": 0,
                },
            )

        try:
            results = json.loads(stdout)
        except json.JSONDecodeError as e:
            raise ToolExecutionError(
                "eslint_runner", f"invalid JSON output: {e}"
            ) from e

        for file_result in results:
            for msg in file_result.get("messages", []):
                line_num = msg.get("line", 0)
                col_num = msg.get("column", 0)
                item = {
                    "rule": msg.get("ruleId") or "unknown",
                    "message": msg.get("message", ""),
                    "line": line_num,
                    "column": col_num,
                    "end_line": msg.get("endLine", line_num),
                    "end_column": msg.get("endColumn", col_num),
                }

                severity = msg.get("severity", 0)
                if severity == 2:
                    errors.append(item)
                elif severity == 1:
                    warnings.append(item)
                # severity 0 (off) → 무시

        logger.debug(
            f"ESLint 분석 완료: {len(errors)} errors, {len(warnings)} warnings"
        )

        return ToolResult(
            success=True,
            data={
                "errors": errors,
                "warnings": warnings,
                "total_errors": len(errors),
                "total_warnings": len(warnings),
            },
        )
