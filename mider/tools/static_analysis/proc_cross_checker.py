"""ProCCrossChecker: SymbolGraph 기반 C↔SQL 경계면 교차 검사.

설계서 V3 §5.2 기반. 7개 규칙:
  CROSS_HOST_VAR_UNDECLARED       — SQL에서 참조하는 host var이 DECLARE SECTION에 없음
  CROSS_HOST_VAR_TYPE_MISMATCH    — host var C 타입과 SQL 사용 맥락 불일치
  CROSS_CURSOR_FUNC_SPLIT         — cursor OPEN/CLOSE가 다른 함수, 호출 관계 불확실
  CROSS_CURSOR_INCOMPLETE         — cursor lifecycle 불완전
  CROSS_SQLCA_NO_ERROR_PATH       — SQLCA 체크 후 에러 처리 경로 없음
  CROSS_TRANSACTION_SPLIT         — DML과 COMMIT/ROLLBACK이 다른 함수에 분리
  CROSS_TRANSACTION_MISSING_ROLLBACK — COMMIT 있으나 ROLLBACK 경로 없음
"""

from __future__ import annotations

import logging
import re

from mider.models.proc_partition import (
    CursorUnit,
    EmbeddedSQLUnit,
    Finding,
    HostVarUnit,
    PartitionResult,
    SQLKind,
    TransactionPoint,
)
from mider.tools.utility.proc_symbol_graph import (
    EdgeType,
    NodeType,
    SymbolGraph,
)

logger = logging.getLogger(__name__)

# DML 종류
_DML_KINDS = {SQLKind.SELECT, SQLKind.INSERT, SQLKind.UPDATE, SQLKind.DELETE, SQLKind.MERGE}

# C 타입 → SQL 타입 호환성 매핑 (간이)
_TYPE_COMPAT: dict[str, set[str]] = {
    "int": {"NUMBER", "INTEGER", "INT", "SMALLINT", "NUMERIC"},
    "long": {"NUMBER", "INTEGER", "BIGINT", "NUMERIC"},
    "short": {"NUMBER", "SMALLINT", "INTEGER"},
    "float": {"NUMBER", "FLOAT", "REAL", "NUMERIC"},
    "double": {"NUMBER", "FLOAT", "DOUBLE", "NUMERIC"},
    "char": {"VARCHAR", "VARCHAR2", "CHAR", "NCHAR", "NVARCHAR2", "CLOB", "TEXT"},
}

# 에러 처리 경로 패턴 (return, goto, break, exit)
_ERROR_PATH_PATTERN = re.compile(
    r"\b(return[\s;]|goto\s|break\s*;|exit\s*\(|abort\s*\()", re.IGNORECASE,
)


class ProCCrossChecker:
    """SymbolGraph를 활용한 C↔SQL 경계면 교차 결함 검사."""

    def __init__(self) -> None:
        self._finding_counter = 0

    def check(
        self,
        graph: SymbolGraph,
        partition: PartitionResult,
    ) -> list[Finding]:
        """7개 규칙으로 교차 검사 수행."""
        self._finding_counter = 0
        findings: list[Finding] = []

        # SymbolGraph가 비어있으면 건너뜀 (Fallback: LLM에 위임)
        if not graph.nodes:
            logger.info("SymbolGraph가 비어있어 CrossChecker 건너뜀")
            return findings

        findings.extend(self._check_host_var_undeclared(graph, partition))
        findings.extend(self._check_host_var_type_mismatch(graph, partition))
        findings.extend(self._check_cursor_func_split(graph, partition))
        findings.extend(self._check_cursor_incomplete(partition))
        findings.extend(self._check_sqlca_no_error_path(graph, partition))
        findings.extend(self._check_transaction_split(graph, partition))
        findings.extend(self._check_transaction_missing_rollback(partition))

        return findings

    # ──────────────────────────────────────────
    # 규칙 1: CROSS_HOST_VAR_UNDECLARED
    # ──────────────────────────────────────────

    def _check_host_var_undeclared(
        self,
        graph: SymbolGraph,
        partition: PartitionResult,
    ) -> list[Finding]:
        """SQL에서 참조하는 host variable이 DECLARE SECTION에 선언되지 않은 경우."""
        findings: list[Finding] = []
        declared_names = {hv.name for hv in partition.host_variables}

        for block in partition.sql_blocks:
            for hvar_name in block.host_variables:
                if hvar_name not in declared_names:
                    findings.append(self._make_finding(
                        rule_id="CROSS_HOST_VAR_UNDECLARED",
                        severity="critical",
                        category="data_integrity",
                        title=f"호스트 변수 '{hvar_name}' 미선언",
                        description=(
                            f"함수 {block.function_name or '(global)'}의 "
                            f"{block.sql_kind.value} 문(L{block.origin_start_line})에서 "
                            f"참조하는 호스트 변수 :{hvar_name}이 "
                            f"DECLARE SECTION에 선언되어 있지 않습니다."
                        ),
                        line_start=block.origin_start_line,
                        line_end=block.origin_end_line,
                        function_name=block.function_name,
                    ))
        return findings

    # ──────────────────────────────────────────
    # 규칙 2: CROSS_HOST_VAR_TYPE_MISMATCH
    # ──────────────────────────────────────────

    def _check_host_var_type_mismatch(
        self,
        graph: SymbolGraph,
        partition: PartitionResult,
    ) -> list[Finding]:
        """host variable C 타입과 SQL 사용 맥락 불일치."""
        findings: list[Finding] = []
        # host var 타입 인덱스
        hvar_type_map: dict[str, str] = {}
        for hv in partition.host_variables:
            if hv.declared_type and hv.declared_type != "unknown":
                hvar_type_map[hv.name] = hv.declared_type

        if not hvar_type_map:
            return findings  # 타입 정보 없으면 건너뜀

        for block in partition.sql_blocks:
            if block.sql_kind not in (SQLKind.SELECT, SQLKind.INSERT, SQLKind.UPDATE):
                continue

            for hvar_name in block.host_variables:
                c_type = hvar_type_map.get(hvar_name)
                if not c_type:
                    continue

                # C 타입에서 기본형 추출 (char[32] → char, int → int)
                base_type = self._extract_base_type(c_type)
                if not base_type:
                    continue

                # SQL 문맥에서 숫자 바인딩인지 문자열 바인딩인지 추정
                sql_context = self._infer_sql_context(block.sql_text, hvar_name)
                if not sql_context:
                    continue

                compatible = _TYPE_COMPAT.get(base_type, set())
                if sql_context not in compatible and compatible:
                    findings.append(self._make_finding(
                        rule_id="CROSS_HOST_VAR_TYPE_MISMATCH",
                        severity="high",
                        category="data_integrity",
                        title=f"호스트 변수 '{hvar_name}' 타입 불일치 (C:{c_type}, SQL:{sql_context})",
                        description=(
                            f"함수 {block.function_name or '(global)'}의 "
                            f"{block.sql_kind.value} 문(L{block.origin_start_line})에서 "
                            f"호스트 변수 :{hvar_name}의 C 타입({c_type})이 "
                            f"SQL 컨텍스트({sql_context})와 호환되지 않을 수 있습니다."
                        ),
                        line_start=block.origin_start_line,
                        line_end=block.origin_end_line,
                        function_name=block.function_name,
                    ))
        return findings

    @staticmethod
    def _extract_base_type(c_type: str) -> str | None:
        """C 타입에서 기본형을 추출한다. 예: 'char[32]' → 'char'."""
        c_type = c_type.strip().lower()
        # 배열/포인터 제거
        base = re.sub(r"\s*[\[*].*", "", c_type).strip()
        # unsigned/signed 제거
        base = re.sub(r"^(unsigned|signed)\s+", "", base).strip()
        return base if base else None

    @staticmethod
    def _infer_sql_context(sql_text: str, hvar_name: str) -> str | None:
        """SQL 문맥에서 호스트 변수의 사용 패턴을 추정한다."""
        sql_upper = sql_text.upper()

        # TO_NUMBER(:var) → NUMBER 컨텍스트
        pattern_number = re.compile(
            rf"TO_NUMBER\s*\([^)]*:{re.escape(hvar_name)}", re.IGNORECASE,
        )
        if pattern_number.search(sql_text):
            return "NUMBER"

        # TO_CHAR(:var) → VARCHAR 컨텍스트
        pattern_char = re.compile(
            rf"TO_CHAR\s*\([^)]*:{re.escape(hvar_name)}", re.IGNORECASE,
        )
        if pattern_char.search(sql_text):
            return "VARCHAR"

        # WHERE col = :var — col 타입 추정은 제한적이므로 None
        return None

    # ──────────────────────────────────────────
    # 규칙 3: CROSS_CURSOR_FUNC_SPLIT
    # ──────────────────────────────────────────

    def _check_cursor_func_split(
        self,
        graph: SymbolGraph,
        partition: PartitionResult,
    ) -> list[Finding]:
        """cursor OPEN과 CLOSE가 다른 함수에 있고 호출 관계 불확실."""
        findings: list[Finding] = []

        # call graph 유효성 확인
        has_call_edges = any(
            e.edge_type == EdgeType.CALLS for e in graph.edges
        )

        for cursor in partition.cursor_map:
            open_funcs = set(cursor.open_functions)
            close_funcs = set(cursor.close_functions)

            if not open_funcs or not close_funcs:
                continue

            # OPEN과 CLOSE 함수가 다른 경우
            split_funcs = open_funcs - close_funcs
            if not split_funcs:
                continue

            for open_func in split_funcs:
                # call graph에서 open_func → close_func 경로 확인 (1-hop)
                has_path = False
                if has_call_edges:
                    for close_func in close_funcs:
                        if graph.has_path_1hop(
                            f"func:{open_func}", f"func:{close_func}",
                        ):
                            has_path = True
                            break

                if not has_path:
                    open_evt = next(
                        (e for e in cursor.events
                         if e.event_type == "OPEN" and e.function_name == open_func),
                        None,
                    )
                    line = open_evt.line if open_evt else 0
                    findings.append(self._make_finding(
                        rule_id="CROSS_CURSOR_FUNC_SPLIT",
                        severity="high",
                        category="data_integrity",
                        title=f"커서 {cursor.cursor_name} OPEN/CLOSE 함수 분리",
                        description=(
                            f"커서 {cursor.cursor_name}이 함수 {open_func}에서 OPEN되나 "
                            f"CLOSE는 {close_funcs}에서 수행됩니다. "
                            f"호출 관계가 확인되지 않아 자원 누수 위험이 있습니다."
                        ),
                        line_start=line,
                        line_end=line,
                        function_name=open_func,
                    ))
        return findings

    # ──────────────────────────────────────────
    # 규칙 4: CROSS_CURSOR_INCOMPLETE
    # ──────────────────────────────────────────

    def _check_cursor_incomplete(
        self, partition: PartitionResult,
    ) -> list[Finding]:
        """cursor lifecycle 불완전 (DECLARE/OPEN/FETCH/CLOSE 중 누락)."""
        findings: list[Finding] = []
        for cursor in partition.cursor_map:
            if cursor.is_complete:
                continue
            missing = cursor.missing_events
            if not missing:
                continue

            first_evt = cursor.events[0] if cursor.events else None
            line = first_evt.line if first_evt else 0
            func = first_evt.function_name if first_evt else None

            findings.append(self._make_finding(
                rule_id="CROSS_CURSOR_INCOMPLETE",
                severity="high",
                category="data_integrity",
                title=f"커서 {cursor.cursor_name} lifecycle 불완전",
                description=(
                    f"커서 {cursor.cursor_name}에서 "
                    f"{', '.join(missing)} 이벤트가 누락되었습니다."
                ),
                line_start=line,
                line_end=line,
                function_name=func,
            ))
        return findings

    # ──────────────────────────────────────────
    # 규칙 5: CROSS_SQLCA_NO_ERROR_PATH
    # ──────────────────────────────────────────

    def _check_sqlca_no_error_path(
        self,
        graph: SymbolGraph,
        partition: PartitionResult,
    ) -> list[Finding]:
        """SQL 실행 후 SQLCA 체크는 있으나 에러 처리 경로(return/goto/break) 없음."""
        findings: list[Finding] = []

        for block in partition.sql_blocks:
            if block.sql_kind not in _DML_KINDS:
                continue
            if not block.has_sqlca_check:
                continue

            # 해당 블록 직후의 C 코드에서 에러 처리 경로 확인
            # C segment 중 이 SQL 블록 직후에 오는 것을 찾음
            following_c = self._find_following_c_segment(
                block, partition,
            )
            if following_c is None:
                continue

            content = following_c.get_content(partition.file_content)
            if not _ERROR_PATH_PATTERN.search(content):
                findings.append(self._make_finding(
                    rule_id="CROSS_SQLCA_NO_ERROR_PATH",
                    severity="medium",
                    category="data_integrity",
                    title=f"SQLCA 체크 후 에러 처리 경로 없음 (L{block.origin_start_line})",
                    description=(
                        f"함수 {block.function_name or '(global)'}의 "
                        f"{block.sql_kind.value} 문(L{block.origin_start_line}) 후 "
                        f"SQLCA 체크는 있으나, return/goto 등 "
                        f"에러 처리 분기가 확인되지 않습니다."
                    ),
                    line_start=block.origin_end_line,
                    line_end=following_c.origin_end_line,
                    function_name=block.function_name,
                ))
        return findings

    @staticmethod
    def _find_following_c_segment(
        block: EmbeddedSQLUnit,
        partition: PartitionResult,
    ):
        """SQL 블록 직후의 C segment를 찾는다."""
        best = None
        best_start = float("inf")
        for seg in partition.c_segments:
            if seg.origin_start_line > block.origin_end_line:
                if seg.function_name == block.function_name:
                    if seg.origin_start_line < best_start:
                        best = seg
                        best_start = seg.origin_start_line
        return best

    # ──────────────────────────────────────────
    # 규칙 6: CROSS_TRANSACTION_SPLIT
    # ──────────────────────────────────────────

    def _check_transaction_split(
        self,
        graph: SymbolGraph,
        partition: PartitionResult,
    ) -> list[Finding]:
        """DML과 COMMIT/ROLLBACK이 다른 함수에 분리."""
        findings: list[Finding] = []

        # call graph 확인
        has_call_edges = any(
            e.edge_type == EdgeType.CALLS for e in graph.edges
        )
        if not has_call_edges:
            return findings  # call graph 없으면 건너뜀

        # DML이 있는 함수 집합
        dml_funcs: set[str] = set()
        for block in partition.sql_blocks:
            if block.sql_kind in _DML_KINDS and block.function_name:
                dml_funcs.add(block.function_name)

        # COMMIT/ROLLBACK이 있는 함수 집합
        tx_funcs: set[str] = set()
        for tp in partition.transaction_points:
            if tp.kind in ("COMMIT", "ROLLBACK") and tp.function_name:
                tx_funcs.add(tp.function_name)

        if not dml_funcs or not tx_funcs:
            return findings

        # DML 함수에서 트랜잭션 함수로의 호출 경로가 없는 경우
        for dml_func in dml_funcs:
            if dml_func in tx_funcs:
                continue  # 같은 함수에 있으면 OK

            has_path = False
            for tx_func in tx_funcs:
                if graph.has_path_1hop(f"func:{dml_func}", f"func:{tx_func}"):
                    has_path = True
                    break
                # 역방향도 확인 (tx_func → dml_func 호출)
                if graph.has_path_1hop(f"func:{tx_func}", f"func:{dml_func}"):
                    has_path = True
                    break

            if not has_path:
                # DML이 있는 첫 번째 블록 line
                first_dml = next(
                    (b for b in partition.sql_blocks
                     if b.sql_kind in _DML_KINDS and b.function_name == dml_func),
                    None,
                )
                line = first_dml.origin_start_line if first_dml else 0
                findings.append(self._make_finding(
                    rule_id="CROSS_TRANSACTION_SPLIT",
                    severity="medium",
                    category="data_integrity",
                    title=f"DML 함수({dml_func})와 트랜잭션 함수 분리",
                    description=(
                        f"함수 {dml_func}에 DML이 있으나 "
                        f"COMMIT/ROLLBACK은 {tx_funcs}에서 수행됩니다. "
                        f"직접 호출 관계가 확인되지 않아 "
                        f"트랜잭션 제어가 불확실합니다."
                    ),
                    line_start=line,
                    line_end=line,
                    function_name=dml_func,
                ))
        return findings

    # ──────────────────────────────────────────
    # 규칙 7: CROSS_TRANSACTION_MISSING_ROLLBACK
    # ──────────────────────────────────────────

    def _check_transaction_missing_rollback(
        self, partition: PartitionResult,
    ) -> list[Finding]:
        """COMMIT은 있으나 에러 시 ROLLBACK 경로 없음."""
        findings: list[Finding] = []
        tx_kinds = {tp.kind for tp in partition.transaction_points}

        has_commit = "COMMIT" in tx_kinds
        has_rollback = "ROLLBACK" in tx_kinds

        if has_commit and not has_rollback:
            commit_tp = next(
                tp for tp in partition.transaction_points if tp.kind == "COMMIT"
            )
            findings.append(self._make_finding(
                rule_id="CROSS_TRANSACTION_MISSING_ROLLBACK",
                severity="high",
                category="data_integrity",
                title="COMMIT 있으나 ROLLBACK 경로 없음",
                description=(
                    f"함수 {commit_tp.function_name or '(global)'}에서 "
                    f"COMMIT(L{commit_tp.line})이 수행되나 "
                    f"ROLLBACK이 없어 에러 발생 시 "
                    f"트랜잭션 롤백이 불가능합니다."
                ),
                line_start=commit_tp.line,
                line_end=commit_tp.line,
                function_name=commit_tp.function_name,
            ))
        return findings

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
        line_start: int,
        line_end: int,
        function_name: str | None,
    ) -> Finding:
        self._finding_counter += 1
        return Finding(
            finding_id=f"CF-{self._finding_counter:03d}",
            source_layer="cross",
            tool="cross_checker",
            rule_id=rule_id,
            severity=severity,
            category=category,
            title=title,
            description=description,
            origin_line_start=line_start,
            origin_line_end=line_end,
            function_name=function_name,
        )
