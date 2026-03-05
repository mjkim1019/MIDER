"""ProcRunner: Pro*C 프리컴파일러 Tool.

Oracle proc를 실행하여 Pro*C 파일의 문법/구문 오류를 추출한다.
폐쇄망 환경에서 portable proc 바이너리를 사용한다.
"""

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult

logger = logging.getLogger(__name__)

# 패키지 기준 기본 경로
_PACKAGE_DIR = Path(__file__).parent.parent.parent  # mider/
_DEFAULT_BINARY = _PACKAGE_DIR / "resources" / "binaries" / "proc"

# proc 출력 에러 파싱 정규표현식
# 형식: PCC-S-02201, Encountered the symbol "xxx" when expecting one of ...
# 또는: 파일명(라인): 에러 메시지
_PROC_ERROR_PATTERN = re.compile(
    r"^(?:PCC-[SWE]-\d+,\s+)?(.+?)(?:\((\d+)\))?:\s*(.+)$"
)

# PCC 에러 코드 패턴 (별도)
_PCC_CODE_PATTERN = re.compile(r"(PCC-[SWE]-\d+)")

# proc 에러 라인 패턴 (Semantic error at line N, column M)
_SEMANTIC_ERROR_PATTERN = re.compile(
    r"Semantic error at line (\d+),\s*column (\d+)",
    re.IGNORECASE,
)

# 실행 타임아웃 (초)
_TIMEOUT_SECONDS = 120


class ProcRunner(BaseTool):
    """Oracle proc 프리컴파일러 Tool.

    Pro*C 파일에 대해 proc를 실행하고
    errors 리스트와 성공 여부를 반환한다.
    """

    def __init__(self, binary_path: str | None = None) -> None:
        self._binary = Path(binary_path) if binary_path else _DEFAULT_BINARY

    def execute(
        self,
        *,
        file: str,
        include_dirs: list[str] | None = None,
    ) -> ToolResult:
        """proc를 실행하여 분석 결과를 반환한다.

        Args:
            file: 분석할 Pro*C 파일 경로
            include_dirs: include 디렉토리 리스트

        Returns:
            ToolResult (data: errors, success, total_errors)

        Raises:
            ToolExecutionError: 바이너리/파일 없음, 실행 실패 시
        """
        file_path = Path(file)
        if not file_path.exists():
            raise ToolExecutionError(
                "proc_runner", f"file not found: {file}"
            )

        if not self._binary.exists():
            import shutil
            system_proc = shutil.which("proc")
            if system_proc:
                self._binary = Path(system_proc)
            else:
                logger.info(
                    "Oracle proc 바이너리 없음 — Heuristic 모드로 분석합니다. "
                    "(resources/binaries/ 또는 시스템 PATH에 proc가 없음)"
                )
                return ToolResult(
                    success=True,
                    data={"errors": [], "total_errors": 0, "skipped": True},
                )

        include_dirs = include_dirs or []

        # proc 명령어 구성
        cmd = [
            str(self._binary),
            f"iname={file_path}",
            "parse=full",       # 전체 파싱 모드
            "sqlcheck=full",    # SQL 문법 검사
            "code=ansi_c",      # ANSI C 출력
            f"oname={os.devnull}",  # 출력 파일 없음 (검사만)
        ]

        for inc_dir in include_dirs:
            cmd.append(f"include={inc_dir}")

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            raise ToolExecutionError(
                "proc_runner",
                f"binary not found: {self._binary}",
            )
        except subprocess.TimeoutExpired:
            raise ToolExecutionError(
                "proc_runner",
                f"timeout after {_TIMEOUT_SECONDS}s: {file}",
            )

        return self._parse_output(
            proc.stdout, proc.stderr, proc.returncode, str(file_path)
        )

    def _parse_output(
        self,
        stdout: str,
        stderr: str,
        returncode: int,
        file_path: str,
    ) -> ToolResult:
        """proc 출력을 파싱한다."""
        combined = f"{stdout}\n{stderr}"
        errors: list[dict[str, Any]] = []
        last_pcc_code: str | None = None

        for line in combined.splitlines():
            line = line.strip()
            if not line:
                continue

            # PCC 에러 코드 추출 (이후 Semantic error에 연결하기 위해 기억)
            pcc_match = _PCC_CODE_PATTERN.search(line)
            if pcc_match:
                last_pcc_code = pcc_match.group(1)

            # Semantic error 패턴
            sem_match = _SEMANTIC_ERROR_PATTERN.search(line)
            if sem_match:
                pcc_code = pcc_match.group(1) if pcc_match else last_pcc_code
                errors.append({
                    "file": file_path,
                    "line": int(sem_match.group(1)),
                    "column": int(sem_match.group(2)),
                    "message": line,
                    "code": pcc_code,
                })
                last_pcc_code = None
                continue

            # PCC 코드가 포함된 에러 라인 (Semantic error가 아닌 경우)
            if pcc_match and line.startswith("PCC-"):
                err_match = _PROC_ERROR_PATTERN.match(line)
                if err_match:
                    line_num = int(err_match.group(2)) if err_match.group(2) else 0
                    errors.append({
                        "file": file_path,
                        "line": line_num,
                        "column": 0,
                        "message": err_match.group(3).strip(),
                        "code": last_pcc_code or pcc_match.group(1),
                    })

        success = returncode == 0 and len(errors) == 0

        logger.debug(
            f"proc 분석 완료: {file_path} → "
            f"{len(errors)} errors, success={success}"
        )

        return ToolResult(
            success=True,
            data={
                "errors": errors,
                "success": success,
                "total_errors": len(errors),
            },
        )
