"""CHeuristicScanner: C 코드 위험 패턴 정적 스캐너.

clang-tidy 없이도 전체 C 파일에서 위험 패턴을 탐지한다.
regex 기반으로 초기화 누락, 위험 함수 사용, NULL 미검증 등을 스캔하고
패턴이 속한 함수를 매핑하여 반환한다.
"""

import logging
import re
from pathlib import Path
from typing import Any

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult
from mider.tools.static_analysis.cursor_close_scanner import (
    scan_cursor_duplicate_close,
)
from mider.tools.static_analysis.format_arg_scanner import (
    scan_format_arg_mismatch,
)
from mider.tools.utility.token_optimizer import find_function_boundaries

logger = logging.getLogger(__name__)


# ── 위험 패턴 정의 ──────────────────────────────

_PATTERNS: list[dict[str, Any]] = [
    {
        "id": "UNINIT_VAR",
        "description": "초기화 없는 지역 변수 선언",
        "regex": re.compile(
            r"^\s+"  # 들여쓰기 있음 (지역 변수)
            r"(?:(?:unsigned\s+|signed\s+|static\s+|const\s+)*"
            r"(?:int|long|short|char|float|double|size_t|ssize_t|\w+_t))"
            r"\s+\*{0,2}\s*"  # 포인터 0~2개
            r"(\w+)"  # 변수명 캡처
            r"\s*;",  # = 없이 ; 으로 끝남
        ),
        "severity": "high",
    },
    {
        "id": "UNSAFE_FUNC",
        "description": "경계 미검증 위험 함수 사용",
        "regex": re.compile(
            r"\b(strcpy|sprintf|strcat|gets|scanf|vsprintf)\s*\("
        ),
        "severity": "high",
    },
    {
        "id": "BOUNDED_FUNC",
        "description": "경계 검사 필요 함수 사용",
        "regex": re.compile(
            r"\b(strncpy|memcpy|memset|memmove|snprintf)\s*\("
        ),
        "severity": "medium",
    },
    {
        "id": "MALLOC_NO_CHECK",
        "description": "malloc/calloc 반환값 NULL 미검증",
        "regex": re.compile(
            r"(\w+)\s*=\s*(?:malloc|calloc|realloc)\s*\("
        ),
        "severity": "high",
    },
    {
        "id": "BUFFER_INDEX",
        "description": "변수 인덱스 배열 접근",
        "regex": re.compile(
            r"(\w+)\s*\[\s*([a-zA-Z_]\w*)\s*\]"
        ),
        "severity": "medium",
    },
    {
        "id": "FORMAT_STRING",
        "description": "외부 입력 가능 포맷 스트링",
        "regex": re.compile(
            r"\b(printf|syslog)\s*\(\s*[^\"']"
        ),
        "severity": "high",
    },
]

# memset sizeof 타입 불일치 탐지 패턴
# memset(&변수명, 0x00, sizeof(타입명)) 에서 변수명+_t ≠ 타입명 → MISMATCH
_MEMSET_SIZEOF_PATTERN = re.compile(
    r"\bmemset\s*\(\s*&\s*(\w+)"     # memset(&변수명  — 구조체 멤버(->)는 제외
    r"\s*,\s*[^,]+,\s*"              # , 0x00,
    r"sizeof\s*\(\s*(\w+)\s*\)"      # sizeof(타입명)
)

# 주석/문자열 내부 매칭 제외용 패턴
_LINE_COMMENT = re.compile(r"//.*$")
_BLOCK_COMMENT_START = re.compile(r"/\*")
_BLOCK_COMMENT_END = re.compile(r"\*/")

# 함수 시그니처 추출 패턴 (함수명만)
_FUNC_NAME_PATTERN = re.compile(
    r"^(?!\s*(?:if|else|for|while|switch|return|#|typedef|struct|union|enum)\b)"
    r"\s*(?:static\s+|extern\s+|inline\s+)*"
    r"(?:void|int|char|long|short|unsigned|float|double|size_t|ssize_t|\w+_t|\w+)"
    r"\s*\*?\s+(\w+)\s*\("
)


class CHeuristicScanner(BaseTool):
    """C 코드 위험 패턴 스캐너.

    전체 파일을 regex로 스캔하여 위험 패턴을 탐지하고
    각 패턴이 속한 함수를 매핑한다.
    """

    def execute(self, *, file: str) -> ToolResult:
        """C 파일을 스캔하여 위험 패턴을 반환한다.

        Args:
            file: 분석할 C 파일 경로

        Returns:
            ToolResult (data: findings, functions_at_risk, total_findings)

        Raises:
            ToolExecutionError: 파일 없음 시
        """
        file_path = Path(file)
        if not file_path.exists():
            raise ToolExecutionError(
                "c_heuristic_scanner", f"file not found: {file}"
            )

        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()

        # 함수 경계 추출
        func_boundaries = find_function_boundaries(lines, "c")
        func_names = self._extract_func_names(lines, func_boundaries)

        # 패턴 스캔
        findings = self._scan_patterns(lines, func_boundaries, func_names)

        # CURSOR_DUPLICATE_CLOSE — 공용 모듈 사용
        for cf in scan_cursor_duplicate_close(content, language="c"):
            findings.append({
                "pattern_id": cf["pattern_id"],
                "severity": cf["severity"],
                "description": cf["description"],
                "line": cf["line"],
                "content": cf["code"],
                "match": f"close({cf['variable']})",
                "function": cf["function"],
                "variable": cf["variable"],
                "all_lines": cf["all_lines"],
            })

        # FORMAT_ARG_MISMATCH — 공용 모듈 사용
        for ff in scan_format_arg_mismatch(content):
            # 해당 호출이 속한 함수 이름 찾기
            func_name = None
            for start_1, end_1 in func_boundaries:
                if start_1 <= ff["line"] <= end_1:
                    func_name = func_names.get((start_1, end_1))
                    break
            findings.append({
                "pattern_id": ff["pattern_id"],
                "severity": ff["severity"],
                "description": ff["description"],
                "line": ff["line"],
                "content": ff["code"],
                "match": f"{ff['function_call']}(...)",
                "function": func_name,
                "format_count": ff["format_count"],
                "arg_count": ff["arg_count"],
            })

        # 위험 함수 목록 (중복 제거, 순서 유지)
        seen: set[str] = set()
        functions_at_risk: list[str] = []
        for f in findings:
            fname = f["function"]
            if fname and fname not in seen:
                seen.add(fname)
                functions_at_risk.append(fname)

        logger.debug(
            f"C 휴리스틱 스캔 완료: {file} → "
            f"{len(findings)} findings, {len(functions_at_risk)} risky functions"
        )

        return ToolResult(
            success=True,
            data={
                "findings": findings,
                "functions_at_risk": functions_at_risk,
                "total_findings": len(findings),
            },
        )

    def _extract_func_names(
        self,
        lines: list[str],
        boundaries: list[tuple[int, int]],
    ) -> dict[tuple[int, int], str]:
        """함수 경계별 함수명을 추출한다."""
        names: dict[tuple[int, int], str] = {}
        for start, end in boundaries:
            idx = start - 1  # 1-based → 0-based
            func_line = lines[idx]
            m = _FUNC_NAME_PATTERN.match(func_line)
            # 2줄 선언: 반환형만 있는 줄 → 다음 줄과 합침
            if not m and idx + 1 < len(lines):
                combined = func_line.rstrip() + " " + lines[idx + 1].lstrip()
                m = _FUNC_NAME_PATTERN.match(combined)
            if m:
                names[(start, end)] = m.group(1)
        return names

    def _find_enclosing_function(
        self,
        line_num: int,  # 1-based
        boundaries: list[tuple[int, int]],
        func_names: dict[tuple[int, int], str],
    ) -> str | None:
        """라인이 속한 함수명을 반환한다."""
        for start, end in boundaries:
            if start <= line_num <= end:
                return func_names.get((start, end))
        return None

    def _find_function_end(
        self,
        line_num: int,
        boundaries: list[tuple[int, int]],
    ) -> int | None:
        """라인이 속한 함수의 종료 라인(1-based)을 반환한다."""
        for start, end in boundaries:
            if start <= line_num <= end:
                return end
        return None

    @staticmethod
    def _strip_comments_and_strings(
        raw: str, in_block_comment: bool,
    ) -> tuple[str, bool]:
        """블록 주석 상태를 추적하며 한 라인을 정리.

        Returns:
            (clean_line, new_in_block_state). 라인이 블록 주석 안에 완전히 갇혀있으면
            clean_line은 빈 문자열.
        """
        if in_block_comment:
            if "*/" in raw:
                in_block_comment = False
                raw = raw.split("*/", 1)[1]
            else:
                return "", True
        if "/*" in raw and "*/" not in raw:
            in_block_comment = True
            raw = raw.split("/*", 1)[0]
        clean = re.sub(r"//.*", "", raw)
        clean = re.sub(r"/\*.*?\*/", "", clean)
        clean = re.sub(r'"[^"]*"', '""', clean)
        clean = re.sub(r"'[^']*'", "''", clean)
        return clean, in_block_comment

    @classmethod
    def _is_safely_initialized_before_use(
        cls,
        lines: list[str],
        var_name: str,
        decl_line_1based: int,
        func_end_1based: int,
    ) -> bool:
        """declaration 이후 첫 사용 전에 var이 안전하게 초기화되는지 검사.

        first-use가 init보다 먼저 나오면 False (진짜 미초기화 사용).
        끝까지 사용/초기화 없으면 True (사용되지 않으니 문제 아님).
        """
        if decl_line_1based >= func_end_1based:
            return True

        v = re.escape(var_name)
        # `==` 비교는 init 아님 → negative lookahead.
        # compound assignment(`+=` 등)는 `=` 앞에 다른 char가 있어 자동 배제됨.
        init_pat = re.compile(rf"\b{v}\s*=(?!=)")
        forinit_pat = re.compile(rf"for\s*\(\s*{v}\s*=")
        addr_pat = re.compile(rf"&\s*{v}\b")
        macro_pat = re.compile(rf"\b(?:INIT2VCHAR|INIT2STR)\s*\(\s*{v}\b")
        use_pat = re.compile(rf"\b{v}\b")

        in_block_comment = False
        for i in range(decl_line_1based, func_end_1based):
            if i >= len(lines):
                break
            clean, in_block_comment = cls._strip_comments_and_strings(
                lines[i], in_block_comment,
            )
            if not clean:
                continue
            if (
                forinit_pat.search(clean)
                or init_pat.search(clean)
                or addr_pat.search(clean)
                or macro_pat.search(clean)
            ):
                return True
            if use_pat.search(clean):
                return False
        return True

    def _scan_patterns(
        self,
        lines: list[str],
        boundaries: list[tuple[int, int]],
        func_names: dict[tuple[int, int], str],
    ) -> list[dict[str, Any]]:
        """전체 파일에서 위험 패턴을 스캔한다.

        UNINIT_VAR 패턴은 함수 내부에서만 탐지한다 (구조체 멤버 제외).
        """
        findings: list[dict[str, Any]] = []
        in_block_comment = False

        for i, line in enumerate(lines):
            line_num = i + 1  # 1-based

            # 블록 주석 처리
            if in_block_comment:
                if _BLOCK_COMMENT_END.search(line):
                    in_block_comment = False
                continue

            block_start_match = _BLOCK_COMMENT_START.search(line)
            if block_start_match and not _BLOCK_COMMENT_END.search(line):
                in_block_comment = True
                # /* 앞 코드만 스캔 대상으로 남김
                line = line[:block_start_match.start()]
                if not line.strip():
                    continue

            # 한 줄 주석 제거
            clean_line = _LINE_COMMENT.sub("", line)

            # 한 줄 블록 주석 제거 (/* ... */)
            clean_line = re.sub(r"/\*.*?\*/", "", clean_line)

            # 문자열 리터럴 내부 제거 (간이 처리)
            clean_line = re.sub(r'"[^"]*"', '""', clean_line)
            clean_line = re.sub(r"'[^']*'", "''", clean_line)

            for pattern in _PATTERNS:
                m = pattern["regex"].search(clean_line)
                if not m:
                    continue

                func_name = self._find_enclosing_function(
                    line_num, boundaries, func_names,
                )

                # UNINIT_VAR는 함수 내부에서만 의미 있음
                if pattern["id"] == "UNINIT_VAR" and func_name is None:
                    continue

                # 사용 전 안전한 초기화 패턴(`for(var=...)`, `var=...`, `&var`,
                # INIT2VCHAR/INIT2STR)이 있으면 false positive로 간주하고 건너뛴다.
                if pattern["id"] == "UNINIT_VAR":
                    func_end = self._find_function_end(line_num, boundaries)
                    if func_end is not None and self._is_safely_initialized_before_use(
                        lines, m.group(1), line_num, func_end,
                    ):
                        continue

                findings.append({
                    "pattern_id": pattern["id"],
                    "severity": pattern["severity"],
                    "description": pattern["description"],
                    "line": line_num,
                    "content": line.strip(),
                    "match": m.group(0),
                    "function": func_name,
                })

            # memset sizeof 타입 불일치 검사
            ms = _MEMSET_SIZEOF_PATTERN.search(clean_line)
            if ms:
                var_name = ms.group(1)      # e.g. zord_abn_sale_spc_u0010_in
                sizeof_type = ms.group(2)   # e.g. zord_abn_sale_spc_s0009_in_t
                # ProFrame 로컬 변수 접두사(ll_, lc_, ld_, ls_) 제거 후 비교
                stripped_var = re.sub(r"^l[lcds]_", "", var_name)
                expected_type = stripped_var + "_t"
                if expected_type != sizeof_type:
                    func_name = self._find_enclosing_function(
                        line_num, boundaries, func_names,
                    )
                    findings.append({
                        "pattern_id": "MEMSET_SIZE_MISMATCH",
                        "severity": "high",
                        "description": (
                            f"memset sizeof 타입 불일치: "
                            f"변수 '{var_name}'의 예상 타입 '{expected_type}' ≠ "
                            f"sizeof 타입 '{sizeof_type}'"
                        ),
                        "line": line_num,
                        "content": line.strip(),
                        "match": ms.group(0),
                        "function": func_name,
                    })

        return findings
