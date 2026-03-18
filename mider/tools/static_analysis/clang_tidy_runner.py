"""ClangTidyRunner: C 정적 분석 Tool.

clang-tidy를 실행하여 C 파일의 경고를 추출한다.
폐쇄망 환경에서 portable clang-tidy 바이너리를 사용한다.
"""

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult
from mider.tools.static_analysis.stub_header_generator import StubHeaderGenerator

logger = logging.getLogger(__name__)

# 패키지 기준 기본 경로
_PACKAGE_DIR = Path(__file__).parent.parent.parent  # mider/
_DEFAULT_BINARY = _PACKAGE_DIR / "resources" / "binaries" / "clang-tidy"
_DEFAULT_CHECKS = "-*,clang-analyzer-*,bugprone-*,-bugprone-branch-clone"

# clang-tidy 출력 파싱 정규표현식
# 형식: file:line:col: severity: message [check-name]
_OUTPUT_PATTERN = re.compile(
    r"^(.+?):(\d+):(\d+):\s+(warning|error|note):\s+(.+?)\s+\[(.+?)\]\s*$"
)

# 실행 타임아웃 (초)
_TIMEOUT_SECONDS = 120


class ClangTidyRunner(BaseTool):
    """clang-tidy 정적 분석 Tool.

    C 파일에 대해 clang-tidy를 실행하고
    warnings 리스트를 반환한다.
    """

    def __init__(self, binary_path: str | None = None) -> None:
        self._binary = Path(binary_path) if binary_path else _DEFAULT_BINARY

    def execute(
        self,
        *,
        file: str,
        checks: str | None = None,
    ) -> ToolResult:
        """clang-tidy를 실행하여 분석 결과를 반환한다.

        Args:
            file: 분석할 C 파일 경로
            checks: clang-tidy 체크 옵션 (없으면 기본값 사용)

        Returns:
            ToolResult (data: warnings, total_warnings)

        Raises:
            ToolExecutionError: 바이너리/파일 없음, 실행 실패 시
        """
        file_path = Path(file)
        if not file_path.exists():
            raise ToolExecutionError(
                "clang_tidy_runner", f"file not found: {file}"
            )

        if not self._binary.exists():
            resolved = self._find_binary()
            if resolved:
                self._binary = resolved
            else:
                logger.info(
                    "clang-tidy 바이너리 없음 — Heuristic 모드로 분석합니다. "
                    "(resources/binaries/, 시스템 PATH, homebrew 모두 없음)"
                )
                return ToolResult(
                    success=True,
                    data={"warnings": [], "total_warnings": 0, "skipped": True},
                )

        checks_arg = checks or _DEFAULT_CHECKS

        # 1. stub 생성
        stub_gen = StubHeaderGenerator()
        stubs_dir = file_path.parent / "stubs"
        try:
            stub_gen.generate(str(file_path), stubs_dir)

            cmd = [
                str(self._binary),
                f"--checks={checks_arg}",
                str(file_path),
                "--",  # 컴파일 옵션 구분자
                f"-I{stubs_dir}",
                "-std=c99",
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=_TIMEOUT_SECONDS,
                )
            except FileNotFoundError:
                raise ToolExecutionError(
                    "clang_tidy_runner",
                    f"binary not found: {self._binary}",
                )
            except subprocess.TimeoutExpired:
                raise ToolExecutionError(
                    "clang_tidy_runner",
                    f"timeout after {_TIMEOUT_SECONDS}s: {file}",
                )

            return self._parse_output(proc.stdout, proc.stderr)
        finally:
            # 2. 분석 종료 후 반드시 정리
            stub_gen.cleanup(stubs_dir)

    @staticmethod
    def _find_binary() -> Path | None:
        """시스템 PATH 및 homebrew에서 clang-tidy를 탐색한다."""
        import shutil

        # 1) 시스템 PATH
        system = shutil.which("clang-tidy")
        if system:
            return Path(system)

        # 2) homebrew llvm (macOS — PATH에 자동 등록 안 됨)
        brew_paths = [
            Path("/opt/homebrew/opt/llvm/bin/clang-tidy"),
            Path("/usr/local/opt/llvm/bin/clang-tidy"),
        ]
        # llvm@{version} 설치 탐색
        for base in (Path("/opt/homebrew/Cellar"), Path("/usr/local/Cellar")):
            if base.exists():
                for d in base.glob("llvm*/*/bin/clang-tidy"):
                    brew_paths.append(d)

        for p in brew_paths:
            if p.exists():
                logger.info(f"clang-tidy 발견 (homebrew): {p}")
                return p

        return None

    def _parse_output(self, stdout: str, stderr: str) -> ToolResult:
        """clang-tidy 출력을 파싱한다.

        clang-tidy는 결과를 stdout 또는 stderr에 출력할 수 있다.
        """
        combined = f"{stdout}\n{stderr}"
        warnings: list[dict[str, Any]] = []

        for line in combined.splitlines():
            match = _OUTPUT_PATTERN.match(line)
            if match:
                severity = match.group(4)
                if severity in ("warning", "error"):
                    warnings.append({
                        "file": match.group(1),
                        "line": int(match.group(2)),
                        "column": int(match.group(3)),
                        "severity": severity,
                        "message": match.group(5),
                        "check": match.group(6),
                    })

        logger.debug(f"clang-tidy 분석 완료: {len(warnings)} warnings")

        return ToolResult(
            success=True,
            data={
                "warnings": warnings,
                "total_warnings": len(warnings),
            },
        )
