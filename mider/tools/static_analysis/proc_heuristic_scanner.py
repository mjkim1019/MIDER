"""ProCHeuristicScanner: Pro*C 코드 위험 패턴 정적 스캐너.

실제 장애 유발 패턴 6종을 regex로 사전 스캔한다:
1. FORMAT_STRUCT: %s에 구조체 전달 (Core Dump)
2. MEMSET_SIZEOF_MISMATCH: memset 변수/sizeof 타입 불일치
3. LOOP_INIT_MISSING: 루프 내 구조체 초기화 누락 (구조체별 추적)
4. FCLOSE_MISSING: fopen/fclose 짝 불일치
5. CURSOR_DUPLICATE_CLOSE: 같은 함수 안에서 같은 커서 2회 이상 close
6. FORMAT_ARG_MISMATCH: s?n?printf format 지정자 vs 인자 개수 불일치
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

logger = logging.getLogger(__name__)


# ── 패턴 1: %s에 구조체 전달 ─────────────────────
# PFM_DSP("...%s...", var.member.arr[0])  ← .arr[0] 뒤에 .멤버 없음 → 구조체 전체
_FORMAT_STRUCT_RE = re.compile(
    r"(PFM_DSP|PFM_ERR|PFM_ERRB|printf|sprintf|snprintf|fprintf)"
    r"\s*\([^;]*%s[^;]*,"          # %s 포함 포맷 문자열
    r"[^;]*\w+\.\w+\.\w+\[\d+\]"  # xxx.yyy.zzz[0] 형태
    r"\s*[,)]",                     # .멤버 접근 없이 바로 , 또는 )
)

# ── 패턴 2: memset sizeof 불일치 ─────────────────
# memset(&xxx_u0010_in, 0, sizeof(xxx_s0009_in_t))
_MEMSET_RE = re.compile(
    r"memset\s*\(\s*&?\s*(\w+)\s*,"   # 변수명 캡처
    r"[^,]+,\s*sizeof\s*\(\s*(\w+)\s*\)",  # sizeof 타입명 캡처
)

# ── 패턴 3: 루프 키워드 ─────────────────────────
_LOOP_START_RE = re.compile(r"^\s*(while|for)\s*\(")
_INIT_CALL_RE = re.compile(
    r"(INIT2VCHAR|INIT2STR|memset|memcpy)\s*\("
)
# C 주석(블록 + 라인) 제거용 — 구조체별 초기화 판정 시 주석 제거본과 원본을 비교
_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/|//[^\n]*")

# ── 패턴 4: 파일 open/close (Proframe seq_open 포함) ───
_FOPEN_RE = re.compile(r"\b(fopen|seq_open)\s*\(")
_FCLOSE_RE = re.compile(r"\b(fclose|seq_close)\s*\(")


def _extract_core_name(name: str) -> str:
    """변수명/타입명에서 핵심 식별자(DBIO ID 포함)를 추출한다.

    예: zord_abn_sale_spc_u0010_in → zord_abn_sale_spc_u0010
        zord_abn_sale_spc_s0009_in_t → zord_abn_sale_spc_s0009
    _in/_out/_in_t/_out_t 접미사만 제거, DBIO 번호(u0010/s0009)는 유지.
    """
    # _in, _out, _in_t, _out_t, _ar, _ar_t 제거
    cleaned = re.sub(r"(_in_t|_out_t|_ar_t|_in|_out|_ar|_t)$", "", name)
    return cleaned


# ProFrame 변수 prefix 패턴 (memset/sizeof 비교 시 prefix 제거 후 비교)
# l_/lc_/ll_/ld_/ls_/li_/lf_/lb_  : 로컬
# g_/gc_/gst_/gl_/gn_/gb_         : 전역
_PROFRAME_VAR_PREFIX_RE = re.compile(
    r"^(?:l|ll|lc|ld|ls|li|lf|lb|g|gc|gst|gl|gn|gb)_"
)


def _strip_proframe_prefix(var_name: str) -> str:
    """ProFrame 명명 규약의 prefix 제거 (예: l_ctx → ctx, gst_hd → hd)."""
    return _PROFRAME_VAR_PREFIX_RE.sub("", var_name)


# 주석 제거용 (라인/블록 둘 다)
_COMMENT_STRIP_RE = re.compile(r"/\*[\s\S]*?\*/|//[^\n]*")


def _find_var_declaration_type(content: str, var_name: str) -> str | None:
    """파일 안에서 var_name의 실제 선언 타입을 찾는다.

    형식: `<type> [*]<var>;` 또는 `<type> <var>[...];`. ProFrame 타입은
    `_t` 접미사가 없는 경우도 흔함 (예: `st_result_set gst_rpset;`)이라
    type 식별자에 접미사 강제하지 않음. 검색 전에 C 주석 제거 (주석 처리된
    옛 선언이 활성 선언보다 앞에 있는 경우 매칭됨 방지).

    매칭 실패 시 None. 다중 매칭 시 첫 번째 사용 (대부분의 경우 충분).
    """
    cleaned = _COMMENT_STRIP_RE.sub("", content)
    # f-string 대신 concatenation으로 빌드 (regex의 `{` 와 충돌 회피)
    pat = re.compile(
        r"(?:^|[\s;{])(\w+)\s+(?:\*\s*)?"
        + re.escape(var_name) + r"\b\s*[;,\[]"
    )
    m = pat.search(cleaned)
    return m.group(1) if m else None


class ProCHeuristicScanner(BaseTool):
    """Pro*C 코드 위험 패턴 스캐너.

    실제 장애 유발 패턴을 regex로 스캔하여
    의심 위치를 반환한다.
    """

    def execute(self, *, file: str) -> ToolResult:
        """Pro*C 파일을 스캔하여 위험 패턴을 반환한다.

        Args:
            file: 분석할 Pro*C 파일 경로

        Returns:
            ToolResult (data: findings, total_findings)
        """
        file_path = Path(file)
        if not file_path.exists():
            raise ToolExecutionError(
                "proc_heuristic_scanner", f"file not found: {file}"
            )

        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()

        findings: list[dict[str, Any]] = []

        # Pattern 1: FORMAT_STRUCT
        findings.extend(self._scan_format_struct(lines))

        # Pattern 2: MEMSET_SIZEOF_MISMATCH
        findings.extend(self._scan_memset_mismatch(lines, content))

        # Pattern 3: LOOP_INIT_MISSING
        findings.extend(self._scan_loop_init_missing(lines))

        # Pattern 4: FCLOSE_MISSING
        findings.extend(self._scan_fclose_missing(lines, content))

        # Pattern 5: CURSOR_DUPLICATE_CLOSE
        findings.extend(scan_cursor_duplicate_close(content, language="proc"))

        # Pattern 6: FORMAT_ARG_MISMATCH
        findings.extend(scan_format_arg_mismatch(content))

        logger.debug(
            f"Pro*C 휴리스틱 스캔 완료: {file} → {len(findings)} findings"
        )

        return ToolResult(
            success=True,
            data={
                "findings": findings,
                "total_findings": len(findings),
            },
        )

    @staticmethod
    def _scan_format_struct(lines: list[str]) -> list[dict[str, Any]]:
        """Pattern 1: %s에 구조체 전달 탐지."""
        findings: list[dict[str, Any]] = []
        for line_num, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("/*"):
                continue
            if _FORMAT_STRUCT_RE.search(line):
                # .멤버 접근이 있는지 추가 확인
                # xxx.yyy.zzz[0].member 형태면 정상, [0] 뒤에 . 없으면 구조체
                if not re.search(r"\[\d+\]\.\w+", line):
                    findings.append({
                        "pattern_id": "FORMAT_STRUCT",
                        "severity": "critical",
                        "line": line_num,
                        "code": stripped[:120],
                        "description": (
                            "%s 포맷에 구조체 배열 원소가 직접 전달됨 "
                            "— .멤버 접근 없이 구조체 전체가 %s에 전달되면 "
                            "타입 불일치로 Core Dump 발생"
                        ),
                    })
        return findings

    @staticmethod
    def _scan_memset_mismatch(
        lines: list[str], content: str = "",
    ) -> list[dict[str, Any]]:
        """Pattern 2: memset sizeof 변수/타입 불일치 탐지.

        판정 우선순위:
        1. 파일에서 var의 실제 선언 타입을 찾을 수 있으면 그것과 sizeof 타입을 비교.
           일치 → 정상, 불일치 → 진짜 정탐.
        2. 선언을 못 찾으면 prefix-stripped 핵심명 + abbreviation 휴리스틱으로 판정.

        ProFrame 명명규약(`l_ctx: bat_ctx_t`, `gst_hd: sms_file_header_bo_t` 등)
        때문에 단순 이름 비교는 false positive가 다수 발생함.
        """
        findings: list[dict[str, Any]] = []
        for line_num, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("/*"):
                continue
            m = _MEMSET_RE.search(line)
            if not m:
                continue
            var_name = m.group(1)
            type_name = m.group(2)

            # `sizeof(var)` 패턴은 자기 자신 크기 — 안전
            if var_name == type_name:
                continue

            # 1차: 실제 선언 타입과 비교 (가장 정확)
            declared_type = (
                _find_var_declaration_type(content, var_name) if content else None
            )
            if declared_type:
                if declared_type == type_name:
                    continue  # 정상: 선언 타입과 sizeof 타입 일치
                # 진짜 mismatch — finding으로 보고 (아래 finding append 흐름으로 진입)
                var_core = declared_type
                type_core = type_name
            else:
                # 2차: prefix-aware 휴리스틱 (선언 없으면 fallback)
                var_core = _strip_proframe_prefix(_extract_core_name(var_name))
                type_core = _extract_core_name(type_name)
                # 핵심명이 서로 substring이면 안전 (gst_read ↔ st_db_read 등)
                var_lower = var_core.lower().replace("_", "")
                type_lower = type_core.lower().replace("_", "")
                is_abbreviation = (
                    bool(var_lower) and bool(type_lower)
                    and (var_lower in type_lower or type_lower in var_lower)
                )
                if not (var_core and type_core and var_core != type_core
                        and not is_abbreviation):
                    continue  # 안전 추정 — finding 안 만듦

            findings.append({
                "pattern_id": "MEMSET_SIZEOF_MISMATCH",
                "severity": "critical",
                "line": line_num,
                "code": stripped[:120],
                "description": (
                    f"memset 대상 변수({var_name})와 sizeof 타입({type_name})의 "
                    f"핵심 이름이 불일치: '{var_core}' ≠ '{type_core}'. "
                    "잘못된 크기로 초기화되어 이전 데이터가 잔류할 수 있음"
                ),
            })
        return findings

    @staticmethod
    def _scan_loop_init_missing(lines: list[str]) -> list[dict[str, Any]]:
        """Pattern 3: 루프 내 구조체 초기화 누락 탐지.

        while/for 루프 본문에서 `strncpy/snprintf/memcpy/strcpy(<var>.<멤버>, ...)`로
        쓰여지는 각 구조체에 대해 `INIT2VCHAR/INIT2STR/memset(&?<var>, ...)`로
        초기화되는지 개별 확인한다. 일부 구조체만 초기화되고 다른 구조체가
        누락된 경우도 구조체마다 finding으로 보고한다.
        """
        findings: list[dict[str, Any]] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if not _LOOP_START_RE.match(line):
                i += 1
                continue

            # 루프 본문 범위 탐색 (중괄호 매칭)
            # 루프 시작 라인 뒤 첫 `{`를 loop_start로 기록, 같은 depth의
            # `}`를 만나면 loop_end. 중첩된 `{...}`는 brace_count로 추적하되
            # loop_start는 한 번만 기록.
            loop_start: int | None = None
            loop_end: int | None = None
            brace_count = 0
            for j in range(i, min(i + 500, len(lines))):
                opens = lines[j].count("{")
                closes = lines[j].count("}")
                brace_count += opens - closes
                if loop_start is None and opens > 0:
                    loop_start = j
                if loop_start is not None and brace_count <= 0 and j >= loop_start:
                    loop_end = j
                    break

            if loop_start is None or loop_end is None or loop_end <= loop_start:
                i += 1
                continue

            loop_body = "\n".join(lines[loop_start:loop_end + 1])
            # 주석 제거본(블록/라인 주석 둘 다) — 실제 컴파일되는 코드
            code_only = _COMMENT_RE.sub("", loop_body)

            # 쓰기 대상 구조체 이름 집합 (strncpy/snprintf/memcpy/strcpy의 첫 인자에서
            # `<var>.<member>` 형식을 찾는다. `(char *)` 등 캐스트는 건너뛴다.)
            written_structs = set(re.findall(
                r"(?:strncpy|snprintf|memcpy|strcpy)\s*\("
                r"\s*(?:\([^)]*\))?\s*&?\s*(\w+)\.",
                code_only,
            ))
            if not written_structs:
                i = loop_end + 1
                continue

            for struct in sorted(written_structs):
                init_pat = re.compile(
                    r"(?:INIT2VCHAR|INIT2STR|memset|memcpy)\s*\(\s*&?\s*"
                    + re.escape(struct) + r"\b"
                )
                # 실제 코드(주석 제거본)에 초기화가 있으면 정상
                if init_pat.search(code_only):
                    continue
                # 원본에는 있는데 주석 제거본에 없다 = 주석 처리된 초기화
                has_commented_init = bool(init_pat.search(loop_body))
                findings.append({
                    "pattern_id": "LOOP_INIT_MISSING",
                    "severity": "high",
                    "line": i + 1,  # 1-based 루프 헤더 라인
                    "variable": struct,
                    "code": line.strip()[:120],
                    "description": (
                        f"루프 본문에서 구조체 {struct}에 쓰기(strncpy/snprintf 등)를 "
                        "수행하지만 INIT2VCHAR/memset 초기화가 "
                        f"{'주석 처리되어 있음' if has_commented_init else '없음'}. "
                        "반복 시 이전 데이터가 누적되어 금액 오표기 등 발생 가능"
                    ),
                })
            i = loop_end + 1
        return findings

    @staticmethod
    def _scan_fclose_missing(
        lines: list[str], content: str,
    ) -> list[dict[str, Any]]:
        """Pattern 4: fopen/fclose 짝 불일치 탐지."""
        findings: list[dict[str, Any]] = []
        fopen_count = len(_FOPEN_RE.findall(content))
        fclose_count = len(_FCLOSE_RE.findall(content))

        if fopen_count > 0 and fopen_count > fclose_count:
            # fopen 위치 찾기
            fopen_lines = []
            for line_num, line in enumerate(lines, start=1):
                if _FOPEN_RE.search(line):
                    fopen_lines.append(line_num)

            findings.append({
                "pattern_id": "FCLOSE_MISSING",
                "severity": "high",
                "line": fopen_lines[0] if fopen_lines else 0,
                "code": f"fopen {fopen_count}건, fclose {fclose_count}건",
                "description": (
                    f"fopen({fopen_count}건)과 fclose({fclose_count}건) 수가 불일치. "
                    "파일 핸들이 닫히지 않으면 장시간 배치 시 "
                    "'Too many open files' 에러 또는 데이터 flush 실패 발생"
                ),
            })

        return findings
