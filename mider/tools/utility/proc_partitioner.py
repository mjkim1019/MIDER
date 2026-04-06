"""ProCPartitioner: .pc 파일을 의미 단위로 분해한다.

설계서 V3 §3.1에 따라 3단계 순차 파싱을 수행한다:
  Stage 1: 함수 경계 추출 (find_function_boundaries 재사용)
  Stage 2: EXEC SQL 블록 추출 + C/SQL 영역 분할 (상태 머신)
  Stage 3: Host Variable 추출

핵심 원칙:
  - 분리 결과는 메모리에 유지 (임시 파일 없음)
  - 모든 결과는 원본 .pc line 번호로 복원 가능
  - 실패 시 fallback (파이프라인 중단 없음)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from mider.models.proc_partition import (
    CSegment,
    CursorLifecycleEvent,
    CursorUnit,
    DeclareSection,
    EmbeddedSQLUnit,
    FunctionUnit,
    GlobalContext,
    GlobalVar,
    HostVarUnit,
    IncludeDirective,
    MacroDef,
    PartitionResult,
    SQLKind,
    TransactionPoint,
    TypeDef,
    WheneverDirective,
)
from mider.tools.utility.token_optimizer import find_function_boundaries

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 정규식 패턴
# ──────────────────────────────────────────────

# 함수명 추출 (sql_extractor.py의 _FUNC_NAME_PATTERN 재사용)
_FUNC_NAME_PATTERN = re.compile(
    r"^(?!\s*(?:if|else|for|while|switch|return|#|typedef|struct|union|enum)\b)"
    r"\s*(?:static\s+|extern\s+|inline\s+)*"
    r"(?:void|int|char|long|short|unsigned|float|double|size_t|ssize_t|\w+_t|\w+)\s*\*?\s+"
    r"(\w+)\s*\("
)

# EXEC SQL 시작 패턴
_EXEC_SQL_START = re.compile(r"EXEC\s+SQL\b", re.IGNORECASE)

# EXEC SQL 세부 분류 패턴
_EXEC_SQL_BEGIN_DECLARE = re.compile(
    r"EXEC\s+SQL\s+BEGIN\s+DECLARE\s+SECTION", re.IGNORECASE
)
_EXEC_SQL_END_DECLARE = re.compile(
    r"EXEC\s+SQL\s+END\s+DECLARE\s+SECTION", re.IGNORECASE
)
_EXEC_SQL_WHENEVER = re.compile(
    r"EXEC\s+SQL\s+WHENEVER\s+(SQLERROR|NOT\s+FOUND|SQLWARNING)\s+(.*)",
    re.IGNORECASE,
)
_EXEC_SQL_INCLUDE = re.compile(r"EXEC\s+SQL\s+INCLUDE\b", re.IGNORECASE)

# DML 분류
_SQL_KIND_PATTERNS: list[tuple[re.Pattern, SQLKind]] = [
    (re.compile(r"EXEC\s+SQL\s+DECLARE\s+\w+\s+CURSOR", re.IGNORECASE), SQLKind.CURSOR_DECLARE),
    (re.compile(r"EXEC\s+SQL\s+OPEN\s+\w+", re.IGNORECASE), SQLKind.CURSOR_OPEN),
    (re.compile(r"EXEC\s+SQL\s+FETCH\s+\w+", re.IGNORECASE), SQLKind.CURSOR_FETCH),
    (re.compile(r"EXEC\s+SQL\s+CLOSE\s+\w+", re.IGNORECASE), SQLKind.CURSOR_CLOSE),
    (re.compile(r"EXEC\s+SQL\s+COMMIT", re.IGNORECASE), SQLKind.COMMIT),
    (re.compile(r"EXEC\s+SQL\s+ROLLBACK", re.IGNORECASE), SQLKind.ROLLBACK),
    (re.compile(r"EXEC\s+SQL\s+SAVEPOINT", re.IGNORECASE), SQLKind.SAVEPOINT),
    (re.compile(r"EXEC\s+SQL\s+SELECT\b", re.IGNORECASE), SQLKind.SELECT),
    (re.compile(r"EXEC\s+SQL\s+INSERT\b", re.IGNORECASE), SQLKind.INSERT),
    (re.compile(r"EXEC\s+SQL\s+UPDATE\b", re.IGNORECASE), SQLKind.UPDATE),
    (re.compile(r"EXEC\s+SQL\s+DELETE\b", re.IGNORECASE), SQLKind.DELETE),
    (re.compile(r"EXEC\s+SQL\s+MERGE\b", re.IGNORECASE), SQLKind.MERGE),
]

# Host variable 패턴
_HOST_VAR_PATTERN = re.compile(r":(\w+)(?!:)")
_INDICATOR_VAR_PATTERN = re.compile(r":(\w+):(\w+)")

# SQLCA 체크 패턴
_SQLCA_CHECK_PATTERN = re.compile(
    r"sqlca\.sqlcode|SQLCA\.SQLCODE|WHENEVER\s+SQLERROR|WHENEVER\s+NOT\s+FOUND",
    re.IGNORECASE,
)

# C 변수 선언 패턴 (DECLARE SECTION 내)
_C_VAR_DECL_PATTERN = re.compile(
    r"^\s*(?:static\s+|extern\s+|const\s+)*"
    r"((?:unsigned\s+)?(?:int|char|long|short|float|double|size_t|ssize_t|\w+_t))"
    r"\s*(\*?)\s+"
    r"(\w+)"
    r"\s*(\[[^\]]*\])?"  # 배열 크기
    r"\s*(?:=|;|,)"
)

# 커서명 추출 패턴
_CURSOR_DECLARE_PAT = re.compile(
    r"EXEC\s+SQL\s+DECLARE\s+(\w+)\s+CURSOR", re.IGNORECASE
)
_CURSOR_OPEN_PAT = re.compile(r"EXEC\s+SQL\s+OPEN\s+(\w+)", re.IGNORECASE)
_CURSOR_FETCH_PAT = re.compile(r"EXEC\s+SQL\s+FETCH\s+(\w+)", re.IGNORECASE)
_CURSOR_CLOSE_PAT = re.compile(r"EXEC\s+SQL\s+CLOSE\s+(\w+)", re.IGNORECASE)

# 전역 변수 패턴
_GLOBAL_VAR_PATTERN = re.compile(
    r"^(?:static\s+|extern\s+)?(?:const\s+)?"
    r"(?:int|char|long|short|unsigned|float|double|size_t|\w+_t)\s+"
    r"\w+\s*(?:=|;|\[)"
)

# 보일러플레이트 함수 판별
_BOILERPLATE_PATTERN = re.compile(
    r"^(?:main|z\w{3}b\w+\d+)$|_(?:init|exit)_proc$", re.IGNORECASE
)


# ──────────────────────────────────────────────
# 파싱 상태 머신 상태
# ──────────────────────────────────────────────

class _ParseState:
    """Stage 2 상태 머신의 내부 상태."""

    C_CODE = "C_CODE"
    SQL_BLOCK = "SQL_BLOCK"
    DECLARE_SECTION = "DECLARE_SECTION"


# ──────────────────────────────────────────────
# ProCPartitioner
# ──────────────────────────────────────────────


class ProCPartitioner:
    """Pro*C 파일을 의미 단위로 분해한다."""

    # CSegment 병합 임계값: 인접 C 라인이 이 줄 수 미만이면 이전 CSegment에 병합
    MERGE_THRESHOLD = 5

    def partition(self, file_path: str) -> PartitionResult:
        """파일을 읽고 분해 결과를 반환한다.

        Args:
            file_path: .pc 파일 경로

        Returns:
            PartitionResult
        """
        path = Path(file_path)
        encoding = "utf-8"

        # 파일 읽기 (인코딩 fallback)
        try:
            content = path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            try:
                encoding = "euc-kr"
                content = path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                encoding = "utf-8"
                content = path.read_text(encoding=encoding, errors="replace")
                logger.warning(f"인코딩 오류, errors='replace' 모드로 읽기: {file_path}")

        return self.partition_content(content, file_path, encoding)

    def partition_content(
        self,
        file_content: str,
        source_file: str = "<string>",
        encoding: str = "utf-8",
    ) -> PartitionResult:
        """문자열 내용을 직접 분해한다 (테스트용).

        Args:
            file_content: 파일 내용
            source_file: 원본 파일 경로 (메타데이터용)
            encoding: 인코딩 정보

        Returns:
            PartitionResult
        """
        lines = file_content.splitlines()
        total_lines = len(lines)

        # Stage 1: 함수 경계 추출
        functions = self._stage1_function_boundaries(lines)

        # 함수 line → 함수명 매핑 (이후 단계에서 공통 사용)
        func_line_map = self._build_func_line_map(lines, functions)

        # Stage 2: EXEC SQL 블록 + C/SQL 영역 분할
        sql_blocks, c_segments, whenever_directives = self._stage2_split_regions(
            lines, functions, func_line_map, file_content,
        )

        # Stage 3: Host Variable 추출
        host_variables = self._stage3_extract_host_variables(
            lines, sql_blocks, functions, func_line_map,
        )

        # 부가 정보 수집
        cursor_map = self._build_cursor_map(sql_blocks, func_line_map)
        transaction_points = self._collect_transaction_points(sql_blocks, func_line_map)
        global_context = self._build_global_context(
            lines, functions, func_line_map, whenever_directives,
        )

        # FunctionUnit 생성
        function_units = self._build_function_units(lines, functions, func_line_map)

        return PartitionResult(
            source_file=source_file,
            encoding=encoding,
            total_lines=total_lines,
            file_content=file_content,
            global_context=global_context,
            functions=function_units,
            c_segments=c_segments,
            sql_blocks=sql_blocks,
            host_variables=host_variables,
            cursor_map=cursor_map,
            transaction_points=transaction_points,
        )

    # ──────────────────────────────────────────
    # Stage 1: 함수 경계 추출
    # ──────────────────────────────────────────

    def _stage1_function_boundaries(
        self, lines: list[str],
    ) -> list[tuple[int, int]]:
        """기존 find_function_boundaries를 호출한다."""
        try:
            boundaries = find_function_boundaries(lines, "proc")
        except Exception:
            logger.warning("함수 경계 추출 실패, 파일 전체를 단일 함수로 간주")
            boundaries = [(1, len(lines))] if lines else []
        return boundaries

    # ──────────────────────────────────────────
    # Stage 2: EXEC SQL 블록 + C/SQL 영역 분할
    # ──────────────────────────────────────────

    def _stage2_split_regions(
        self,
        lines: list[str],
        functions: list[tuple[int, int]],
        func_line_map: dict[int, str],
        file_content: str,
    ) -> tuple[list[EmbeddedSQLUnit], list[CSegment], list[WheneverDirective]]:
        """상태 머신으로 C 코드와 SQL 블록을 분리한다."""
        sql_blocks: list[EmbeddedSQLUnit] = []
        whenever_directives: list[WheneverDirective] = []

        # line 별 분류: "c", "sql", "declare", "whenever", "include", "directive"
        line_types: list[str] = ["c"] * len(lines)

        state = _ParseState.C_CODE
        sql_start_idx: int | None = None
        declare_start_idx: int | None = None
        sql_counter = 0
        active_whenever: str | None = None

        for i, line in enumerate(lines):
            stripped = line.strip()
            line_num = i + 1  # 1-based

            if state == _ParseState.C_CODE:
                # EXEC SQL BEGIN DECLARE SECTION
                if _EXEC_SQL_BEGIN_DECLARE.search(stripped):
                    state = _ParseState.DECLARE_SECTION
                    declare_start_idx = i
                    line_types[i] = "declare"
                    continue

                # EXEC SQL WHENEVER
                m_whenever = _EXEC_SQL_WHENEVER.search(stripped)
                if m_whenever:
                    line_types[i] = "whenever"
                    wd = self._parse_whenever(m_whenever, line_num, func_line_map)
                    whenever_directives.append(wd)
                    if wd.suppresses_sqlca_check:
                        active_whenever = f"WHENEVER {wd.condition} {wd.action}"
                    else:
                        active_whenever = None
                    continue

                # EXEC SQL INCLUDE
                if _EXEC_SQL_INCLUDE.search(stripped):
                    line_types[i] = "include"
                    continue

                # EXEC SQL (DML/커서/트랜잭션/기타)
                if _EXEC_SQL_START.search(stripped):
                    state = _ParseState.SQL_BLOCK
                    sql_start_idx = i
                    line_types[i] = "sql"
                    # 한 줄에 세미콜론까지 있는 경우
                    if ";" in stripped:
                        sql_block = self._finalize_sql_block(
                            lines, sql_start_idx, i, sql_counter,
                            func_line_map, file_content, active_whenever,
                        )
                        sql_blocks.append(sql_block)
                        sql_counter += 1
                        state = _ParseState.C_CODE
                        sql_start_idx = None
                    continue

            elif state == _ParseState.SQL_BLOCK:
                line_types[i] = "sql"
                if ";" in stripped:
                    assert sql_start_idx is not None
                    sql_block = self._finalize_sql_block(
                        lines, sql_start_idx, i, sql_counter,
                        func_line_map, file_content, active_whenever,
                    )
                    sql_blocks.append(sql_block)
                    sql_counter += 1
                    state = _ParseState.C_CODE
                    sql_start_idx = None

            elif state == _ParseState.DECLARE_SECTION:
                line_types[i] = "declare"
                if _EXEC_SQL_END_DECLARE.search(stripped):
                    state = _ParseState.C_CODE
                    declare_start_idx = None

        # SQL 블록이 닫히지 않은 경우 (fallback)
        if state == _ParseState.SQL_BLOCK and sql_start_idx is not None:
            logger.warning(
                f"EXEC SQL 블록이 세미콜론 없이 파일 끝 도달 (시작: L{sql_start_idx + 1})"
            )
            # C 코드로 재분류
            for j in range(sql_start_idx, len(lines)):
                line_types[j] = "c"

        # CSegment 생성
        c_segments = self._build_c_segments(lines, line_types, func_line_map)

        return sql_blocks, c_segments, whenever_directives

    def _finalize_sql_block(
        self,
        lines: list[str],
        start_idx: int,
        end_idx: int,
        counter: int,
        func_line_map: dict[int, str],
        file_content: str,
        active_whenever: str | None,
    ) -> EmbeddedSQLUnit:
        """파싱 완료된 SQL 블록으로 EmbeddedSQLUnit을 생성한다."""
        raw_lines = lines[start_idx : end_idx + 1]
        raw_content = "\n".join(raw_lines)

        # EXEC SQL 이후 SQL 본문 추출
        sql_text = re.sub(
            r"EXEC\s+SQL\s+", "", raw_content, count=1, flags=re.IGNORECASE,
        ).strip().rstrip(";").strip()

        # sql_kind 분류
        sql_kind = SQLKind.OTHER
        for pat, kind in _SQL_KIND_PATTERNS:
            if pat.search(raw_content):
                sql_kind = kind
                break

        # host variable / indicator 추출
        indicator_pairs = _INDICATOR_VAR_PATTERN.findall(raw_content)
        indicator_names = [ind for _, ind in indicator_pairs]
        cleaned = _INDICATOR_VAR_PATTERN.sub(r":\1", raw_content)
        host_vars = _HOST_VAR_PATTERN.findall(cleaned)

        # SQLCA 체크: SQL 블록 직후 ~200자 확인
        origin_start = start_idx + 1  # 1-based
        origin_end = end_idx + 1
        block_end_offset = sum(len(l) + 1 for l in lines[: end_idx + 1])
        after_text = file_content[block_end_offset : block_end_offset + 200]
        has_sqlca = bool(_SQLCA_CHECK_PATTERN.search(after_text))

        func_name = self._get_function_at_line(origin_start, func_line_map)

        return EmbeddedSQLUnit(
            block_id=f"sql_{counter:03d}",
            function_name=func_name,
            sql_kind=sql_kind,
            raw_content=raw_content,
            sql_text=sql_text,
            origin_start_line=origin_start,
            origin_end_line=origin_end,
            line_count=origin_end - origin_start + 1,
            host_variables=host_vars,
            indicator_variables=indicator_names,
            has_sqlca_check=has_sqlca,
            active_whenever=active_whenever,
        )

    def _build_c_segments(
        self,
        lines: list[str],
        line_types: list[str],
        func_line_map: dict[int, str],
    ) -> list[CSegment]:
        """line_types 배열에서 연속 C 코드 라인을 CSegment로 묶는다.

        병합 규칙: 인접 C 라인이 MERGE_THRESHOLD 미만이면 이전 CSegment에 병합.
        """
        raw_segments: list[tuple[int, int]] = []
        seg_start: int | None = None

        for i, lt in enumerate(line_types):
            if lt == "c":
                if seg_start is None:
                    seg_start = i
            else:
                if seg_start is not None:
                    raw_segments.append((seg_start, i - 1))
                    seg_start = None
        if seg_start is not None:
            raw_segments.append((seg_start, len(lines) - 1))

        # 함수별 병합: 같은 함수 내 짧은 C segment를 이전 segment에 병합
        merged: list[tuple[int, int]] = []
        for start, end in raw_segments:
            line_count = end - start + 1
            func_name = self._get_function_at_line(start + 1, func_line_map)

            if (
                merged
                and line_count < self.MERGE_THRESHOLD
                and func_name is not None
            ):
                prev_start, prev_end = merged[-1]
                prev_func = self._get_function_at_line(prev_start + 1, func_line_map)
                if prev_func == func_name:
                    # 이전 segment부터 현재 끝까지 확장 (SQL 블록도 포함)
                    merged[-1] = (prev_start, end)
                    continue

            merged.append((start, end))

        # CSegment 객체 생성
        segments: list[CSegment] = []
        for idx, (start, end) in enumerate(merged):
            origin_start = start + 1  # 1-based
            origin_end = end + 1
            func_name = self._get_function_at_line(origin_start, func_line_map)

            segments.append(CSegment(
                segment_id=f"cseg_{idx:03d}",
                function_name=func_name,
                origin_start_line=origin_start,
                origin_end_line=origin_end,
                line_count=origin_end - origin_start + 1,
            ))

        return segments

    def _parse_whenever(
        self,
        match: re.Match,
        line_num: int,
        func_line_map: dict[int, str],
    ) -> WheneverDirective:
        """WHENEVER 지시문을 파싱한다."""
        condition_raw = match.group(1).upper().replace(" ", "_")
        action_raw = match.group(2).strip().rstrip(";").strip()

        return WheneverDirective(
            condition=condition_raw,  # type: ignore[arg-type]
            action=action_raw if action_raw else "CONTINUE",
            line=line_num,
            function_name=self._get_function_at_line(line_num, func_line_map),
        )

    # ──────────────────────────────────────────
    # Stage 3: Host Variable 추출
    # ──────────────────────────────────────────

    def _stage3_extract_host_variables(
        self,
        lines: list[str],
        sql_blocks: list[EmbeddedSQLUnit],
        functions: list[tuple[int, int]],
        func_line_map: dict[int, str],
    ) -> list[HostVarUnit]:
        """DECLARE SECTION + SQL 바인드 변수에서 host variable을 추출한다."""
        host_vars: dict[str, HostVarUnit] = {}

        # 소스 1: DECLARE SECTION 블록
        self._extract_from_declare_sections(lines, functions, func_line_map, host_vars)

        # 소스 2: SQL 블록 내 :변수명
        for sql_block in sql_blocks:
            for var_name in sql_block.host_variables:
                if var_name not in host_vars:
                    host_vars[var_name] = HostVarUnit(
                        name=var_name,
                        declared_type="unknown",
                        declared_in_function=sql_block.function_name,
                        declared_line=sql_block.origin_start_line,
                    )

            # indicator variable도 등록
            for ind_name in sql_block.indicator_variables:
                if ind_name not in host_vars:
                    host_vars[ind_name] = HostVarUnit(
                        name=ind_name,
                        indicator_name=None,
                        declared_type="unknown",
                        declared_in_function=sql_block.function_name,
                        declared_line=sql_block.origin_start_line,
                    )

        return list(host_vars.values())

    def _extract_from_declare_sections(
        self,
        lines: list[str],
        functions: list[tuple[int, int]],
        func_line_map: dict[int, str],
        host_vars: dict[str, HostVarUnit],
    ) -> None:
        """EXEC SQL BEGIN/END DECLARE SECTION 내 변수 선언을 파싱한다."""
        in_declare = False

        for i, line in enumerate(lines):
            stripped = line.strip()
            line_num = i + 1

            if _EXEC_SQL_BEGIN_DECLARE.search(stripped):
                in_declare = True
                continue
            if _EXEC_SQL_END_DECLARE.search(stripped):
                in_declare = False
                continue

            if not in_declare:
                continue

            # C 변수 선언 파싱
            m = _C_VAR_DECL_PATTERN.match(stripped)
            if not m:
                continue

            base_type = m.group(1)
            pointer = m.group(2)
            var_name = m.group(3)
            array_size = m.group(4) or ""

            declared_type = f"{base_type}{pointer}{array_size}"
            func_name = self._get_function_at_line(line_num, func_line_map)

            host_vars[var_name] = HostVarUnit(
                name=var_name,
                declared_type=declared_type,
                declared_in_function=func_name,
                declared_line=line_num,
            )

        # 2차 패스: indicator 매칭
        for var_name in list(host_vars.keys()):
            ind_candidate = f"{var_name}_ind"
            if ind_candidate in host_vars:
                host_vars[var_name].indicator_name = ind_candidate

    # ──────────────────────────────────────────
    # 부가 정보 수집
    # ──────────────────────────────────────────

    def _build_cursor_map(
        self,
        sql_blocks: list[EmbeddedSQLUnit],
        func_line_map: dict[int, str],
    ) -> list[CursorUnit]:
        """SQL 블록에서 커서 lifecycle을 구축한다."""
        cursors: dict[str, CursorUnit] = {}

        event_map: dict[SQLKind, str] = {
            SQLKind.CURSOR_DECLARE: "DECLARE",
            SQLKind.CURSOR_OPEN: "OPEN",
            SQLKind.CURSOR_FETCH: "FETCH",
            SQLKind.CURSOR_CLOSE: "CLOSE",
        }

        cursor_name_patterns: dict[SQLKind, re.Pattern] = {
            SQLKind.CURSOR_DECLARE: _CURSOR_DECLARE_PAT,
            SQLKind.CURSOR_OPEN: _CURSOR_OPEN_PAT,
            SQLKind.CURSOR_FETCH: _CURSOR_FETCH_PAT,
            SQLKind.CURSOR_CLOSE: _CURSOR_CLOSE_PAT,
        }

        for sql_block in sql_blocks:
            if sql_block.sql_kind not in event_map:
                continue

            event_type = event_map[sql_block.sql_kind]
            pat = cursor_name_patterns[sql_block.sql_kind]
            m = pat.search(sql_block.raw_content)
            if not m:
                continue

            cursor_name = m.group(1)
            if cursor_name not in cursors:
                cursors[cursor_name] = CursorUnit(cursor_name=cursor_name)

            cursors[cursor_name].events.append(
                CursorLifecycleEvent(
                    event_type=event_type,  # type: ignore[arg-type]
                    line=sql_block.origin_start_line,
                    function_name=sql_block.function_name,
                )
            )

        return list(cursors.values())

    def _collect_transaction_points(
        self,
        sql_blocks: list[EmbeddedSQLUnit],
        func_line_map: dict[int, str],
    ) -> list[TransactionPoint]:
        """SQL 블록에서 COMMIT/ROLLBACK/SAVEPOINT를 수집한다."""
        kind_map: dict[SQLKind, str] = {
            SQLKind.COMMIT: "COMMIT",
            SQLKind.ROLLBACK: "ROLLBACK",
            SQLKind.SAVEPOINT: "SAVEPOINT",
        }

        points: list[TransactionPoint] = []
        for sql_block in sql_blocks:
            if sql_block.sql_kind in kind_map:
                points.append(TransactionPoint(
                    kind=kind_map[sql_block.sql_kind],  # type: ignore[arg-type]
                    function_name=sql_block.function_name,
                    line=sql_block.origin_start_line,
                ))
        return points

    def _build_global_context(
        self,
        lines: list[str],
        functions: list[tuple[int, int]],
        func_line_map: dict[int, str],
        whenever_directives: list[WheneverDirective],
    ) -> GlobalContext:
        """함수 밖 영역의 메타데이터를 수집한다."""
        func_ranges: set[int] = set()
        for start, end in functions:
            func_ranges.update(range(start, end + 1))

        includes: list[IncludeDirective] = []
        declare_sections: list[DeclareSection] = []
        type_defs: list[TypeDef] = []
        global_vars: list[GlobalVar] = []
        macros: list[MacroDef] = []

        in_declare = False
        declare_start: int | None = None
        declare_lines: list[str] = []

        for i, line in enumerate(lines):
            stripped = line.strip()
            line_num = i + 1

            # DECLARE SECTION 추적 (함수 안팎 모두)
            if _EXEC_SQL_BEGIN_DECLARE.search(stripped):
                in_declare = True
                declare_start = line_num
                declare_lines = [stripped]
                continue
            if in_declare:
                declare_lines.append(line.rstrip())
                if _EXEC_SQL_END_DECLARE.search(stripped):
                    in_declare = False
                    if declare_start is not None:
                        declare_sections.append(DeclareSection(
                            origin_start_line=declare_start,
                            origin_end_line=line_num,
                            raw_content="\n".join(declare_lines),
                        ))
                    declare_start = None
                    declare_lines = []
                continue

            # 함수 밖 영역만 처리
            if line_num in func_ranges:
                continue

            # #include
            if stripped.startswith("#include"):
                includes.append(IncludeDirective(
                    statement=stripped, line=line_num, is_exec_sql=False,
                ))
                continue

            # EXEC SQL INCLUDE
            if _EXEC_SQL_INCLUDE.search(stripped):
                includes.append(IncludeDirective(
                    statement=stripped.rstrip(";").strip() + ";",
                    line=line_num,
                    is_exec_sql=True,
                ))
                continue

            # #define
            if stripped.startswith("#define"):
                macros.append(MacroDef(statement=stripped, line=line_num))
                continue

            # typedef / struct
            if stripped.startswith("typedef ") or re.match(r"^struct\s+\w+", stripped):
                type_defs.append(TypeDef(statement=stripped, line=line_num))
                continue

            # 전역 변수
            if _GLOBAL_VAR_PATTERN.match(line):
                global_vars.append(GlobalVar(statement=line.rstrip(), line=line_num))

        return GlobalContext(
            includes=includes,
            declare_sections=declare_sections,
            type_definitions=type_defs,
            global_variables=global_vars,
            macros=macros,
            whenever_directives=whenever_directives,
        )

    def _build_function_units(
        self,
        lines: list[str],
        functions: list[tuple[int, int]],
        func_line_map: dict[int, str],
    ) -> list[FunctionUnit]:
        """함수 경계에서 FunctionUnit 목록을 생성한다."""
        units: list[FunctionUnit] = []
        for start, end in functions:
            name = func_line_map.get(start, f"unknown_L{start}")
            line_count = end - start + 1

            # 시그니처 추출
            sig_line = lines[start - 1].strip()
            if not _FUNC_NAME_PATTERN.match(lines[start - 1]) and start < len(lines):
                sig_line = lines[start - 1].rstrip() + " " + lines[start].lstrip()
                sig_line = sig_line.strip()
            if len(sig_line) > 80:
                paren = sig_line.find("(")
                if paren > 0:
                    sig_line = sig_line[:paren] + "(...)"

            is_bp = bool(_BOILERPLATE_PATTERN.search(name))

            units.append(FunctionUnit(
                function_name=name,
                line_start=start,
                line_end=end,
                line_count=line_count,
                signature=sig_line,
                is_boilerplate=is_bp,
            ))
        return units

    # ──────────────────────────────────────────
    # 유틸리티
    # ──────────────────────────────────────────

    def _build_func_line_map(
        self,
        lines: list[str],
        functions: list[tuple[int, int]],
    ) -> dict[int, str]:
        """함수 시작 line → 함수명 매핑을 생성한다.

        Returns:
            {start_line(1-based): func_name} + 내부적으로 line → func_name 조회를
            위해 _func_ranges도 캐시한다.
        """
        result: dict[int, str] = {}
        for start, _end in functions:
            idx = start - 1  # 0-based
            m = _FUNC_NAME_PATTERN.match(lines[idx])
            if m:
                result[start] = m.group(1)
                continue
            # 2줄 선언
            if idx + 1 < len(lines):
                combined = lines[idx].rstrip() + " " + lines[idx + 1].lstrip()
                m = _FUNC_NAME_PATTERN.match(combined)
                if m:
                    result[start] = m.group(1)

        # 내부 캐시: 전체 line → func_name 매핑 (get_function_at_line용)
        self._func_ranges: list[tuple[int, int, str]] = []
        for start, end in functions:
            name = result.get(start, f"unknown_L{start}")
            self._func_ranges.append((start, end, name))

        return result

    def _get_function_at_line(
        self, line_num: int, func_line_map: dict[int, str],
    ) -> str | None:
        """주어진 line이 속하는 함수명을 반환한다."""
        for start, end, name in self._func_ranges:
            if start <= line_num <= end:
                return name
        return None
