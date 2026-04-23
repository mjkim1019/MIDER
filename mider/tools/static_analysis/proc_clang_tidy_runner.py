"""ProCClangTidyRunner: Pro*C 파일에 대한 clang-tidy 정적 분석.

.pc 파일에서 EXEC SQL 블록을 제거한 임시 .c 파일을 생성하고,
StubHeaderGenerator + ClangTidyRunner를 사용하여 C 레벨 결함을 탐지한다.

라인 번호는 1:1 보존 (EXEC SQL → 빈 줄 치환) → 별도 매핑 불필요.
"""

import logging
import re
import tempfile
from pathlib import Path
from typing import Any

from mider.models.proc_partition import Finding
from mider.tools.static_analysis.clang_tidy_runner import ClangTidyRunner
from mider.tools.static_analysis.stub_header_generator import StubHeaderGenerator

logger = logging.getLogger(__name__)

# EXEC SQL 블록 매칭 (여러 줄에 걸칠 수 있음)
# EXEC SQL ... ; 또는 EXEC ORACLE ... ;
_EXEC_SQL_PATTERN = re.compile(
    r"EXEC\s+(?:SQL|ORACLE)\b.*?;",
    re.DOTALL | re.IGNORECASE,
)

# EXEC SQL INCLUDE 구문 (헤더 포함)
_EXEC_SQL_INCLUDE = re.compile(
    r"^\s*EXEC\s+SQL\s+INCLUDE\b.*?;\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# Pro*C 전용 키워드가 포함된 줄 (WHENEVER 등)
_PROC_DIRECTIVE = re.compile(
    r"^\s*EXEC\s+SQL\s+(?:WHENEVER|BEGIN\s+DECLARE|END\s+DECLARE|TYPE|VAR)\b",
    re.IGNORECASE,
)

# sqlca.h → 표준 헤더로 치환
_SQLCA_INCLUDE = re.compile(
    r'^\s*#\s*include\s*[<"]sqlca\.h[>"]\s*$',
    re.MULTILINE,
)

# clang-tidy check → Finding severity 매핑
_SEVERITY_MAP: dict[str, str] = {
    "clang-analyzer-deadcode": "medium",
    "clang-analyzer-core": "high",
    "clang-analyzer-security": "critical",
    "bugprone-uninitialized": "high",
    "bugprone-undefined-memory": "high",
    "bugprone-use-after-move": "high",
    "bugprone-sizeof": "medium",
    "cppcoreguidelines-init-variables": "high",
}

# clang-tidy check → Finding category 매핑
_CATEGORY_MAP: dict[str, str] = {
    "clang-analyzer-deadcode": "code_quality",
    "clang-analyzer-core": "data_integrity",
    "clang-analyzer-security": "security",
    "bugprone": "data_integrity",
    "cppcoreguidelines": "data_integrity",
}

# Pro*C 전용 clang-tidy 체크 — 초기화 체크 추가
_PROC_CHECKS = (
    "-*,"
    "clang-analyzer-*,"
    "bugprone-*,"
    "cppcoreguidelines-init-variables,"
    "-bugprone-branch-clone"
)


class ProCClangTidyRunner:
    """Pro*C 파일에 clang-tidy를 실행하여 C 레벨 결함을 탐지한다.

    흐름:
    1. .pc 파일 읽기
    2. EXEC SQL 블록 → 빈 줄 치환 (라인 번호 보존)
    3. 임시 .c 파일 생성
    4. StubHeaderGenerator로 가짜 헤더 생성
    5. ClangTidyRunner 실행
    6. clang-tidy warnings → Finding 변환
    7. 임시 파일/헤더 정리
    """

    def __init__(self) -> None:
        self._clang_tidy = ClangTidyRunner()
        self._stub_gen = StubHeaderGenerator()

    def analyze(
        self,
        file: str,
        source_file: str | None = None,
    ) -> list[Finding]:
        """Pro*C 파일에 clang-tidy를 실행하여 Finding 리스트를 반환한다.

        Args:
            file: .pc 파일 경로
            source_file: Finding에 기록할 원본 파일명 (없으면 file 사용)

        Returns:
            clang-tidy 기반 Finding 리스트
        """
        file_path = Path(file)
        source_name = source_file or str(file_path)

        if not file_path.exists():
            logger.warning(f"파일 없음: {file}")
            return []

        # 1. .pc 파일 읽기
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = file_path.read_text(encoding="cp949", errors="replace")
            except Exception as e:
                logger.warning(f"인코딩 에러: {file} - {e}")
                return []

        # 2. EXEC SQL 제거 → 순수 C 코드
        c_content = self._strip_exec_sql(content)

        # 3. 임시 .c 파일 생성 + stub 헤더 + clang-tidy 실행
        tmp_dir = Path(tempfile.mkdtemp(prefix="mider_proc_ct_"))
        tmp_c_file = tmp_dir / file_path.with_suffix(".c").name
        stubs_dir = tmp_dir / "stubs"

        try:
            tmp_c_file.write_text(c_content, encoding="utf-8")

            # 4. clang-tidy 실행 — 헤더 해석은 ClangTidyRunner 내부에서 수행한다.
            #    tmp_c_file은 tempfile 디렉토리에 있으므로 실제 헤더를 찾으려면
            #    원본 .pc 파일의 디렉토리를 walk-up 앵커로 넘겨야 한다.
            result = self._clang_tidy.execute(
                file=str(tmp_c_file),
                checks=_PROC_CHECKS,
                search_anchor=file_path.parent,
            )

            if result.data.get("skipped"):
                logger.info("clang-tidy 바이너리 없음 — Pro*C clang-tidy 건너뜀")
                return []

            warnings = result.data.get("warnings", [])
            if not warnings:
                return []

            # 6. 헤더 에러 제외 + Finding 변환
            findings = self._convert_warnings(warnings, source_name)

            logger.info(
                f"ProC clang-tidy [{file_path.name}]: "
                f"{len(warnings)}건 경고 → {len(findings)}건 Finding"
            )
            return findings

        except Exception as e:
            logger.warning(f"ProC clang-tidy 실행 실패: {file} - {e}")
            return []
        finally:
            # 7. 정리
            self._stub_gen.cleanup(stubs_dir)
            try:
                if tmp_c_file.exists():
                    tmp_c_file.unlink()
                if tmp_dir.exists():
                    tmp_dir.rmdir()
            except Exception:
                pass

    @staticmethod
    def _strip_exec_sql(content: str) -> str:
        """EXEC SQL 블록을 빈 줄로 치환하여 라인 번호를 보존한다.

        - EXEC SQL ... ; → 해당 줄 수만큼 빈 줄
        - EXEC SQL INCLUDE sqlca.h → 제거
        - #include <sqlca.h> → /* sqlca.h removed */
        """
        # sqlca.h include 제거
        content = _SQLCA_INCLUDE.sub("/* sqlca.h removed for clang-tidy */", content)

        # EXEC SQL 블록을 빈 줄로 치환 (줄 수 보존)
        def _replace_with_blank_lines(match: re.Match) -> str:
            matched_text = match.group(0)
            line_count = matched_text.count("\n")
            return "\n" * line_count

        content = _EXEC_SQL_PATTERN.sub(_replace_with_blank_lines, content)

        return content

    @staticmethod
    def _convert_warnings(
        warnings: list[dict[str, Any]],
        source_file: str,
    ) -> list[Finding]:
        """clang-tidy 경고를 Finding 리스트로 변환한다.

        헤더 에러(fatal error: '*.h' file not found)는 제외한다.
        """
        findings: list[Finding] = []
        counter = 0

        for w in warnings:
            message = w.get("message", "")
            check = w.get("check", "")
            line = w.get("line", 0)

            # 헤더 에러 제외
            if "file not found" in message.lower():
                continue
            # note 레벨 제외 (이미 ClangTidyRunner에서 필터링하지만 안전장치)
            if w.get("severity") == "note":
                continue

            counter += 1
            severity = _guess_severity(check)
            category = _guess_category(check)

            findings.append(Finding(
                finding_id=f"CT-{counter:03d}",
                source_layer="static",
                tool="clang_tidy",
                rule_id=check,
                severity=severity,
                category=category,
                title=f"[clang-tidy] {message}",
                description=(
                    f"clang-tidy 체크 [{check}]: {message} "
                    f"(L{line}, col {w.get('column', 0)})"
                ),
                origin_line_start=line,
                origin_line_end=line,
                function_name=None,
                raw_match=message,
            ))

        return findings


# ──────────────────────────────────────────
# 헬퍼 함수
# ──────────────────────────────────────────


def _guess_severity(check_name: str) -> str:
    """clang-tidy 체크명으로 severity를 추정한다."""
    for prefix, sev in _SEVERITY_MAP.items():
        if check_name.startswith(prefix):
            return sev
    # 기본: warning → medium
    return "medium"


def _guess_category(check_name: str) -> str:
    """clang-tidy 체크명으로 category를 추정한다."""
    for prefix, cat in _CATEGORY_MAP.items():
        if check_name.startswith(prefix):
            return cat
    return "code_quality"
