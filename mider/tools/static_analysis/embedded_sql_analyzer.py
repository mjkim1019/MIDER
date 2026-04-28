"""EmbeddedSQLStaticAnalyzer: SQL 영역 결함을 규칙 기반으로 탐지한다.

설계서 V3 §4.2 기반. 8개 규칙:
  SQL_SQLCA_MISSING        — DML 후 SQLCA 체크 누락
  SQL_SELECT_INTO_MISMATCH — SELECT INTO 컬럼/변수 수 불일치
  SQL_HOST_VAR_COUNT       — INSERT/UPDATE bind variable 수 불일치
  SQL_INDICATOR_MISSING    — NULL 가능 컬럼에 indicator 누락
  SQL_CURSOR_OPEN_MISSING  — DECLARE된 cursor가 OPEN 안 됨
  SQL_CURSOR_CLOSE_MISSING — OPEN된 cursor가 CLOSE 안 됨
  SQL_CURSOR_FETCH_MISSING — OPEN된 cursor에 FETCH 없음
  SQL_COMMIT_MISSING       — DML은 있으나 COMMIT/ROLLBACK 없음
"""

from __future__ import annotations

import logging
import re

from mider.models.proc_partition import (
    CursorUnit,
    EmbeddedSQLUnit,
    Finding,
    GlobalContext,
    HostVarUnit,
    SQLKind,
    TransactionPoint,
)

logger = logging.getLogger(__name__)

# DML 종류
_DML_KINDS = {SQLKind.SELECT, SQLKind.INSERT, SQLKind.UPDATE, SQLKind.DELETE, SQLKind.MERGE}

# NVL 패턴 (Proframe 면제)
_NVL_PATTERN = re.compile(r"\bNVL\s*\(", re.IGNORECASE)

# SELECT 절 컬럼 수 추정용 패턴
_SELECT_COLUMNS_PATTERN = re.compile(
    r"SELECT\s+(.*?)\s+INTO\b",
    re.IGNORECASE | re.DOTALL,
)
_INTO_VARS_PATTERN = re.compile(
    r"INTO\s+(.*?)\s+FROM\b",
    re.IGNORECASE | re.DOTALL,
)


class EmbeddedSQLStaticAnalyzer:
    """EXEC SQL 블록의 정적 규칙 검사."""

    def __init__(self) -> None:
        self._finding_counter = 0

    def analyze(
        self,
        sql_blocks: list[EmbeddedSQLUnit],
        host_variables: list[HostVarUnit],
        cursor_map: list[CursorUnit],
        transaction_points: list[TransactionPoint],
        global_context: GlobalContext,
    ) -> list[Finding]:
        """8개 규칙으로 SQL 영역 결함을 탐지한다."""
        self._finding_counter = 0
        findings: list[Finding] = []

        findings.extend(self._check_sqlca_missing(sql_blocks, global_context))
        findings.extend(self._check_select_into_mismatch(sql_blocks))
        findings.extend(self._check_host_var_count(sql_blocks))
        findings.extend(self._check_indicator_missing(sql_blocks, cursor_map))
        findings.extend(self._check_cursor_open_missing(cursor_map))
        findings.extend(self._check_cursor_close_missing(cursor_map))
        findings.extend(self._check_cursor_fetch_missing(cursor_map))
        findings.extend(self._check_commit_missing(sql_blocks, transaction_points))

        return findings

    # ──────────────────────────────────────────
    # 규칙 1: SQL_SQLCA_MISSING
    # ──────────────────────────────────────────

    def _check_sqlca_missing(
        self,
        sql_blocks: list[EmbeddedSQLUnit],
        global_context: GlobalContext,
    ) -> list[Finding]:
        findings: list[Finding] = []
        for block in sql_blocks:
            if block.sql_kind not in _DML_KINDS:
                continue
            if block.has_sqlca_check:
                continue
            # WHENEVER가 활성이면 면제
            if block.active_whenever:
                continue
            findings.append(self._make_finding(
                rule_id="SQL_SQLCA_MISSING",
                severity="high",
                category="data_integrity",
                title=f"EXEC SQL {block.sql_kind.value} 후 SQLCA 에러 체크 누락",
                description=(
                    f"함수 {block.function_name or '(global)'}의 "
                    f"{block.sql_kind.value} 문(L{block.origin_start_line}) 실행 후 "
                    f"sqlca.sqlcode를 검사하지 않아 SQL 에러가 무시될 수 있습니다."
                ),
                block=block,
            ))
        return findings

    # ──────────────────────────────────────────
    # 규칙 2: SQL_SELECT_INTO_MISMATCH
    # ──────────────────────────────────────────

    def _check_select_into_mismatch(
        self, sql_blocks: list[EmbeddedSQLUnit],
    ) -> list[Finding]:
        findings: list[Finding] = []
        for block in sql_blocks:
            if block.sql_kind != SQLKind.SELECT:
                continue

            col_count = self._count_select_columns(block.sql_text)
            if col_count is None:
                continue  # fallback: 파싱 불가

            var_count = self._count_into_variables(block.sql_text)
            if var_count is None:
                continue

            if col_count != var_count:
                findings.append(self._make_finding(
                    rule_id="SQL_SELECT_INTO_MISMATCH",
                    severity="high",
                    category="data_integrity",
                    title=f"SELECT INTO 컬럼 수({col_count})와 변수 수({var_count}) 불일치",
                    description=(
                        f"함수 {block.function_name or '(global)'}의 "
                        f"SELECT 문(L{block.origin_start_line})에서 "
                        f"SELECT 절 컬럼 {col_count}개, INTO 절 변수 {var_count}개로 불일치합니다."
                    ),
                    block=block,
                ))
        return findings

    def _count_select_columns(self, sql_text: str) -> int | None:
        """SELECT 절의 컬럼 수를 추정한다. 파싱 불가 시 None."""
        m = _SELECT_COLUMNS_PATTERN.search(sql_text)
        if not m:
            return None
        cols_str = m.group(1).strip()
        if not cols_str or cols_str == "*":
            return None
        # 서브쿼리/CASE 포함 시 건너뜀
        if "(" in cols_str:
            # 괄호 안의 쉼표를 무시하기 위해 간이 파싱
            depth = 0
            count = 1
            for ch in cols_str:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                elif ch == "," and depth == 0:
                    count += 1
            return count
        return len([c.strip() for c in cols_str.split(",") if c.strip()])

    def _count_into_variables(self, sql_text: str) -> int | None:
        """INTO 절의 host variable 수를 센다."""
        m = _INTO_VARS_PATTERN.search(sql_text)
        if not m:
            return None
        into_str = m.group(1).strip()
        # :var 패턴 카운트
        vars_list = re.findall(r":(\w+)", into_str)
        # indicator 제거 (:var:ind → 1개)
        return len(vars_list) if vars_list else None

    # ──────────────────────────────────────────
    # 규칙 3: SQL_HOST_VAR_COUNT
    # ──────────────────────────────────────────

    def _check_host_var_count(
        self, sql_blocks: list[EmbeddedSQLUnit],
    ) -> list[Finding]:
        """INSERT VALUES / UPDATE SET의 bind variable 수 불일치."""
        findings: list[Finding] = []
        for block in sql_blocks:
            if block.sql_kind == SQLKind.INSERT:
                result = self._check_insert_var_count(block)
                if result:
                    findings.append(result)
        return findings

    def _check_insert_var_count(self, block: EmbeddedSQLUnit) -> Finding | None:
        """INSERT INTO ... VALUES (...) 에서 컬럼/변수 수 비교."""
        sql = block.sql_text.upper()
        # 컬럼 목록이 명시된 경우만 검사
        col_match = re.search(
            r"INSERT\s+INTO\s+\w+\s*\(([^)]+)\)\s*VALUES",
            block.sql_text, re.IGNORECASE | re.DOTALL,
        )
        val_match = re.search(
            r"VALUES\s*\(([^)]+)\)",
            block.sql_text, re.IGNORECASE | re.DOTALL,
        )
        if not col_match or not val_match:
            return None

        col_count = len([c.strip() for c in col_match.group(1).split(",") if c.strip()])
        val_count = len([v.strip() for v in val_match.group(1).split(",") if v.strip()])

        if col_count != val_count:
            return self._make_finding(
                rule_id="SQL_HOST_VAR_COUNT",
                severity="high",
                category="data_integrity",
                title=f"INSERT 컬럼 수({col_count})와 VALUES 수({val_count}) 불일치",
                description=(
                    f"함수 {block.function_name or '(global)'}의 "
                    f"INSERT 문(L{block.origin_start_line})에서 불일치합니다."
                ),
                block=block,
            )
        return None

    # ──────────────────────────────────────────
    # 규칙 4: SQL_INDICATOR_MISSING
    # ──────────────────────────────────────────

    def _check_indicator_missing(
        self,
        sql_blocks: list[EmbeddedSQLUnit],
        cursor_map: list[CursorUnit] | None = None,
    ) -> list[Finding]:
        """SELECT/FETCH에서 INDICATOR 누락 검사.

        안 B: CURSOR_FETCH의 경우 같은 cursor의 SELECT 본문(`cursor.select_body`)을
        조회해 NVL/COALESCE 처리가 있으면:
        - 모든 컬럼이 보호되었다고 판단되면 finding 자체를 생략
        - 일부만 보호되었으면 severity를 medium → low로 강등하고 description에
          어떤 보호가 있는지 명시
        함수가 분리된 코딩 표준(DECLARE/PREPARE 함수 ≠ FETCH 함수) 케이스의
        false positive를 줄이는 게 목적.
        """
        findings: list[Finding] = []
        cursor_by_name: dict[str, CursorUnit] = {
            c.cursor_name: c for c in (cursor_map or [])
        }

        for block in sql_blocks:
            if block.sql_kind not in (SQLKind.SELECT, SQLKind.CURSOR_FETCH):
                continue
            # 현재 블록 자체에 NVL이 있으면 면제 (기존 동작 유지)
            if _NVL_PATTERN.search(block.sql_text):
                continue
            # host variable은 있지만 indicator가 없는 경우
            if not (block.host_variables and not block.indicator_variables):
                continue

            # 안 B: CURSOR_FETCH면 cursor의 SELECT 본문에서 NULL 보호 여부 검사
            # - 모든 컬럼에 대응할 만한 NULL 보호가 있으면 finding 생략 (FP 가능성 큼)
            # - 일부만 보호되면 description에 note 첨부 (severity는 그대로 유지하여
            #   LLM Reviewer가 컨텍스트를 보고 최종 판정)
            select_body_note: str = ""
            severity: str = "medium"
            if block.sql_kind == SQLKind.CURSOR_FETCH and cursor_by_name:
                cursor_name = self._extract_cursor_name_from_fetch(block.raw_content)
                cursor = cursor_by_name.get(cursor_name) if cursor_name else None
                if cursor and cursor.select_body:
                    select_body = cursor.select_body
                    null_safe_count = self._count_null_protections(select_body)
                    fetch_var_count = len(block.host_variables)
                    if null_safe_count >= fetch_var_count > 0:
                        # 모든 INTO 변수에 대응할 만한 NULL 보호가 SELECT에 존재
                        # → false positive 가능성 큼, finding 생략
                        continue
                    if null_safe_count > 0:
                        select_body_note = (
                            f" (cursor {cursor_name}의 SELECT에 NVL/COALESCE/NULL "
                            f"처리 {null_safe_count}건 발견 — "
                            f"{cursor.select_origin_function or '?'} 함수에 정의됨. "
                            "일부 컬럼만 보호되었으니 LLM이 컬럼별로 검토 필요)"
                        )
                elif cursor:
                    # cursor는 추적되지만 select_body 추출 실패
                    select_body_note = (
                        f" (cursor {cursor_name}의 SELECT 본문 추적 실패 — "
                        "동적 조립이 복잡하거나 다른 함수에 분리되어 있을 수 있음)"
                    )

            findings.append(self._make_finding(
                rule_id="SQL_INDICATOR_MISSING",
                severity=severity,
                category="null_safety",
                title="SELECT/FETCH에서 INDICATOR 변수 누락",
                description=(
                    f"함수 {block.function_name or '(global)'}의 "
                    f"{block.sql_kind.value} 문(L{block.origin_start_line})에서 "
                    f"host variable에 indicator 변수가 없어 "
                    f"NULL 값 수신 시 비정상 동작할 수 있습니다.{select_body_note}"
                ),
                block=block,
            ))
        return findings

    @staticmethod
    def _extract_cursor_name_from_fetch(raw: str) -> str | None:
        """`EXEC SQL FETCH C_x INTO ...`에서 cursor 이름 추출."""
        m = re.search(r"FETCH\s+(\w+)", raw, re.IGNORECASE)
        return m.group(1) if m else None

    @staticmethod
    def _count_null_protections(select_body: str) -> int:
        """SELECT 본문에서 컬럼별 NULL 보호 처리 개수를 센다.

        검사 대상 패턴:
        - NVL(expr, default)
        - COALESCE(expr, ...)
        - decode(...., NULL, default, ...) 형태 (보호 의도 추정)
        - 'Y'/'N' fallback이 있는 decode-NVL 조합
        """
        count = 0
        count += len(re.findall(r"\bNVL\s*\(", select_body, re.IGNORECASE))
        count += len(re.findall(r"\bCOALESCE\s*\(", select_body, re.IGNORECASE))
        # decode(expr, NULL, ...) 형태도 NULL 보호로 카운트
        count += len(re.findall(
            r"\bdecode\s*\([^)]*\bNULL\b", select_body, re.IGNORECASE,
        ))
        return count

    # ──────────────────────────────────────────
    # 규칙 5~7: 커서 lifecycle
    # ──────────────────────────────────────────

    def _check_cursor_open_missing(self, cursor_map: list[CursorUnit]) -> list[Finding]:
        findings: list[Finding] = []
        for cursor in cursor_map:
            has_declare = any(e.event_type == "DECLARE" for e in cursor.events)
            has_open = any(e.event_type == "OPEN" for e in cursor.events)
            if has_declare and not has_open:
                declare_evt = next(e for e in cursor.events if e.event_type == "DECLARE")
                findings.append(self._make_finding_raw(
                    rule_id="SQL_CURSOR_OPEN_MISSING",
                    severity="high",
                    category="data_integrity",
                    title=f"커서 {cursor.cursor_name} DECLARE 후 OPEN 누락",
                    description=(
                        f"커서 {cursor.cursor_name}이 L{declare_evt.line}에서 "
                        f"DECLARE되었으나 OPEN이 없습니다."
                    ),
                    line_start=declare_evt.line,
                    line_end=declare_evt.line,
                    function_name=declare_evt.function_name,
                ))
        return findings

    def _check_cursor_close_missing(self, cursor_map: list[CursorUnit]) -> list[Finding]:
        findings: list[Finding] = []
        for cursor in cursor_map:
            has_open = any(e.event_type == "OPEN" for e in cursor.events)
            has_close = any(e.event_type == "CLOSE" for e in cursor.events)
            if has_open and not has_close:
                open_evt = next(e for e in cursor.events if e.event_type == "OPEN")
                findings.append(self._make_finding_raw(
                    rule_id="SQL_CURSOR_CLOSE_MISSING",
                    severity="high",
                    category="data_integrity",
                    title=f"커서 {cursor.cursor_name} OPEN 후 CLOSE 누락",
                    description=(
                        f"커서 {cursor.cursor_name}이 L{open_evt.line}에서 "
                        f"OPEN되었으나 CLOSE가 없어 DB 자원 누수 위험이 있습니다."
                    ),
                    line_start=open_evt.line,
                    line_end=open_evt.line,
                    function_name=open_evt.function_name,
                ))
        return findings

    def _check_cursor_fetch_missing(self, cursor_map: list[CursorUnit]) -> list[Finding]:
        findings: list[Finding] = []
        for cursor in cursor_map:
            has_open = any(e.event_type == "OPEN" for e in cursor.events)
            has_fetch = any(e.event_type == "FETCH" for e in cursor.events)
            if has_open and not has_fetch:
                open_evt = next(e for e in cursor.events if e.event_type == "OPEN")
                findings.append(self._make_finding_raw(
                    rule_id="SQL_CURSOR_FETCH_MISSING",
                    severity="medium",
                    category="data_integrity",
                    title=f"커서 {cursor.cursor_name} OPEN 후 FETCH 누락",
                    description=(
                        f"커서 {cursor.cursor_name}이 OPEN되었으나 FETCH가 없습니다."
                    ),
                    line_start=open_evt.line,
                    line_end=open_evt.line,
                    function_name=open_evt.function_name,
                ))
        return findings

    # ──────────────────────────────────────────
    # 규칙 8: SQL_COMMIT_MISSING
    # ──────────────────────────────────────────

    def _check_commit_missing(
        self,
        sql_blocks: list[EmbeddedSQLUnit],
        transaction_points: list[TransactionPoint],
    ) -> list[Finding]:
        has_dml = any(b.sql_kind in _DML_KINDS for b in sql_blocks)
        has_tx = len(transaction_points) > 0
        if has_dml and not has_tx:
            first_dml = next(b for b in sql_blocks if b.sql_kind in _DML_KINDS)
            return [self._make_finding(
                rule_id="SQL_COMMIT_MISSING",
                severity="medium",
                category="data_integrity",
                title="DML 문 존재하나 COMMIT/ROLLBACK 없음",
                description=(
                    "파일에 DML 문이 있으나 COMMIT 또는 ROLLBACK이 없어 "
                    "트랜잭션이 암묵적으로 커밋/롤백될 수 있습니다."
                ),
                block=first_dml,
            )]
        return []

    # ──────────────────────────────────────────
    # Finding 생성 유틸
    # ──────────────────────────────────────────

    def _make_finding(
        self,
        rule_id: str,
        severity: str,
        category: str,
        title: str,
        description: str,
        block: EmbeddedSQLUnit,
    ) -> Finding:
        self._finding_counter += 1
        return Finding(
            finding_id=f"SF-{self._finding_counter:03d}",
            source_layer="static",
            tool="embedded_sql_static",
            rule_id=rule_id,
            severity=severity,
            category=category,
            title=title,
            description=description,
            origin_line_start=block.origin_start_line,
            origin_line_end=block.origin_end_line,
            function_name=block.function_name,
            raw_match=block.raw_content[:200],
        )

    def _make_finding_raw(
        self,
        rule_id: str,
        severity: str,
        category: str,
        title: str,
        description: str,
        line_start: int,
        line_end: int,
        function_name: str | None,
    ) -> Finding:
        self._finding_counter += 1
        return Finding(
            finding_id=f"SF-{self._finding_counter:03d}",
            source_layer="static",
            tool="embedded_sql_static",
            rule_id=rule_id,
            severity=severity,
            category=category,
            title=title,
            description=description,
            origin_line_start=line_start,
            origin_line_end=line_end,
            function_name=function_name,
        )
