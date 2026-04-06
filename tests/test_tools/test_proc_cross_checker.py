"""ProCCrossChecker 단위 테스트.

7개 교차 검사 규칙을 검증한다.
"""

from __future__ import annotations

import pytest

from mider.models.proc_partition import (
    CSegment,
    CursorLifecycleEvent,
    CursorUnit,
    EmbeddedSQLUnit,
    FunctionUnit,
    GlobalContext,
    HostVarUnit,
    PartitionResult,
    SQLKind,
    TransactionPoint,
)
from mider.tools.static_analysis.proc_cross_checker import ProCCrossChecker
from mider.tools.utility.proc_symbol_graph import (
    ProCSymbolGraphBuilder,
    SymbolGraph,
)


def _make_partition(**kwargs) -> PartitionResult:
    defaults = dict(
        source_file="test.pc",
        total_lines=200,
        file_content="",
        global_context=GlobalContext(),
        functions=[],
        c_segments=[],
        sql_blocks=[],
        host_variables=[],
        cursor_map=[],
        transaction_points=[],
    )
    defaults.update(kwargs)
    return PartitionResult(**defaults)


def _build_graph(partition: PartitionResult) -> SymbolGraph:
    return ProCSymbolGraphBuilder().build(partition)


@pytest.fixture
def checker() -> ProCCrossChecker:
    return ProCCrossChecker()


# ──────────────────────────────────────────
# 규칙 1: CROSS_HOST_VAR_UNDECLARED
# ──────────────────────────────────────────


class TestHostVarUndeclared:
    def test_detects_undeclared_var(self, checker):
        partition = _make_partition(
            sql_blocks=[
                EmbeddedSQLUnit(
                    block_id="sql_001",
                    function_name="fn",
                    sql_kind=SQLKind.SELECT,
                    raw_content="EXEC SQL SELECT a INTO :unknown_var FROM tab ;",
                    sql_text="SELECT a INTO :unknown_var FROM tab",
                    origin_start_line=10,
                    origin_end_line=12,
                    line_count=3,
                    host_variables=["unknown_var"],
                ),
            ],
            host_variables=[],  # 선언 없음
        )
        graph = _build_graph(partition)
        findings = checker.check(graph, partition)
        matched = [f for f in findings if f.rule_id == "CROSS_HOST_VAR_UNDECLARED"]
        assert len(matched) == 1
        assert matched[0].severity == "critical"

    def test_no_finding_when_declared(self, checker):
        partition = _make_partition(
            sql_blocks=[
                EmbeddedSQLUnit(
                    block_id="sql_001",
                    function_name="fn",
                    sql_kind=SQLKind.SELECT,
                    raw_content="EXEC SQL SELECT a INTO :x FROM tab ;",
                    sql_text="SELECT a INTO :x FROM tab",
                    origin_start_line=10,
                    origin_end_line=12,
                    line_count=3,
                    host_variables=["x"],
                ),
            ],
            host_variables=[
                HostVarUnit(name="x", declared_type="int", declared_line=3),
            ],
        )
        graph = _build_graph(partition)
        findings = checker.check(graph, partition)
        assert not any(f.rule_id == "CROSS_HOST_VAR_UNDECLARED" for f in findings)


# ──────────────────────────────────────────
# 규칙 2: CROSS_HOST_VAR_TYPE_MISMATCH
# ──────────────────────────────────────────


class TestHostVarTypeMismatch:
    def test_detects_type_mismatch_to_number(self, checker):
        """char 타입 변수를 TO_NUMBER()에 사용하면 탐지."""
        partition = _make_partition(
            functions=[
                FunctionUnit(
                    function_name="fn",
                    line_start=1, line_end=50, line_count=50,
                    signature="void fn(void)",
                ),
            ],
            sql_blocks=[
                EmbeddedSQLUnit(
                    block_id="sql_001",
                    function_name="fn",
                    sql_kind=SQLKind.SELECT,
                    raw_content="EXEC SQL SELECT TO_NUMBER(:svc_cd) FROM dual ;",
                    sql_text="SELECT TO_NUMBER(:svc_cd) FROM dual",
                    origin_start_line=10,
                    origin_end_line=12,
                    line_count=3,
                    host_variables=["svc_cd"],
                ),
            ],
            host_variables=[
                HostVarUnit(
                    name="svc_cd",
                    declared_type="char[32]",
                    declared_in_function="fn",
                    declared_line=5,
                ),
            ],
        )
        graph = _build_graph(partition)
        findings = checker.check(graph, partition)
        # char와 NUMBER는 호환 안 됨
        matched = [f for f in findings if f.rule_id == "CROSS_HOST_VAR_TYPE_MISMATCH"]
        assert len(matched) == 1

    def test_no_finding_compatible_types(self, checker):
        """int 타입 변수를 TO_NUMBER()에 사용하면 호환."""
        partition = _make_partition(
            functions=[
                FunctionUnit(
                    function_name="fn",
                    line_start=1, line_end=50, line_count=50,
                    signature="void fn(void)",
                ),
            ],
            sql_blocks=[
                EmbeddedSQLUnit(
                    block_id="sql_001",
                    function_name="fn",
                    sql_kind=SQLKind.SELECT,
                    raw_content="EXEC SQL SELECT TO_NUMBER(:cnt) FROM dual ;",
                    sql_text="SELECT TO_NUMBER(:cnt) FROM dual",
                    origin_start_line=10,
                    origin_end_line=12,
                    line_count=3,
                    host_variables=["cnt"],
                ),
            ],
            host_variables=[
                HostVarUnit(
                    name="cnt",
                    declared_type="int",
                    declared_in_function="fn",
                    declared_line=5,
                ),
            ],
        )
        graph = _build_graph(partition)
        findings = checker.check(graph, partition)
        assert not any(f.rule_id == "CROSS_HOST_VAR_TYPE_MISMATCH" for f in findings)


# ──────────────────────────────────────────
# 규칙 3: CROSS_CURSOR_FUNC_SPLIT
# ──────────────────────────────────────────


class TestCursorFuncSplit:
    def test_detects_split_cursor(self, checker):
        """OPEN과 CLOSE가 다른 함수, 호출 관계 없음."""
        file_content = "\n".join(["line"] * 200)
        partition = _make_partition(
            file_content=file_content,
            functions=[
                FunctionUnit(
                    function_name="opener",
                    line_start=1, line_end=50, line_count=50,
                    signature="void opener(void)",
                ),
                FunctionUnit(
                    function_name="closer",
                    line_start=51, line_end=100, line_count=50,
                    signature="void closer(void)",
                ),
            ],
            cursor_map=[
                CursorUnit(
                    cursor_name="c_emp",
                    events=[
                        CursorLifecycleEvent(event_type="DECLARE", line=5, function_name="opener"),
                        CursorLifecycleEvent(event_type="OPEN", line=10, function_name="opener"),
                        CursorLifecycleEvent(event_type="FETCH", line=15, function_name="opener"),
                        CursorLifecycleEvent(event_type="CLOSE", line=55, function_name="closer"),
                    ],
                ),
            ],
        )
        graph = _build_graph(partition)
        findings = checker.check(graph, partition)
        matched = [f for f in findings if f.rule_id == "CROSS_CURSOR_FUNC_SPLIT"]
        assert len(matched) == 1

    def test_no_finding_same_func(self, checker):
        """OPEN과 CLOSE가 같은 함수."""
        partition = _make_partition(
            functions=[
                FunctionUnit(
                    function_name="fn",
                    line_start=1, line_end=100, line_count=100,
                    signature="void fn(void)",
                ),
            ],
            cursor_map=[
                CursorUnit(
                    cursor_name="c_emp",
                    events=[
                        CursorLifecycleEvent(event_type="DECLARE", line=5, function_name="fn"),
                        CursorLifecycleEvent(event_type="OPEN", line=10, function_name="fn"),
                        CursorLifecycleEvent(event_type="FETCH", line=30, function_name="fn"),
                        CursorLifecycleEvent(event_type="CLOSE", line=50, function_name="fn"),
                    ],
                ),
            ],
        )
        graph = _build_graph(partition)
        findings = checker.check(graph, partition)
        assert not any(f.rule_id == "CROSS_CURSOR_FUNC_SPLIT" for f in findings)


# ──────────────────────────────────────────
# 규칙 4: CROSS_CURSOR_INCOMPLETE
# ──────────────────────────────────────────


class TestCursorIncomplete:
    def test_detects_incomplete_cursor(self, checker):
        partition = _make_partition(
            cursor_map=[
                CursorUnit(
                    cursor_name="c_emp",
                    events=[
                        CursorLifecycleEvent(event_type="DECLARE", line=5, function_name="fn"),
                        CursorLifecycleEvent(event_type="OPEN", line=10, function_name="fn"),
                        # FETCH, CLOSE 누락
                    ],
                ),
            ],
        )
        graph = _build_graph(partition)
        findings = checker.check(graph, partition)
        matched = [f for f in findings if f.rule_id == "CROSS_CURSOR_INCOMPLETE"]
        assert len(matched) == 1
        assert "CLOSE" in matched[0].description
        assert "FETCH" in matched[0].description

    def test_complete_cursor_no_finding(self, checker):
        partition = _make_partition(
            cursor_map=[
                CursorUnit(
                    cursor_name="c_emp",
                    events=[
                        CursorLifecycleEvent(event_type="DECLARE", line=5, function_name="fn"),
                        CursorLifecycleEvent(event_type="OPEN", line=10, function_name="fn"),
                        CursorLifecycleEvent(event_type="FETCH", line=15, function_name="fn"),
                        CursorLifecycleEvent(event_type="CLOSE", line=20, function_name="fn"),
                    ],
                ),
            ],
        )
        graph = _build_graph(partition)
        findings = checker.check(graph, partition)
        assert not any(f.rule_id == "CROSS_CURSOR_INCOMPLETE" for f in findings)


# ──────────────────────────────────────────
# 규칙 5: CROSS_SQLCA_NO_ERROR_PATH
# ──────────────────────────────────────────


class TestSqlcaNoErrorPath:
    def test_detects_no_error_path(self, checker):
        """SQLCA 체크 후 return/goto 없는 경우."""
        file_content = (
            "void fn(void) {\n"           # L1
            "    EXEC SQL SELECT ...\n"    # L2
            "    ;\n"                      # L3
            "    if (sqlca.sqlcode < 0) {\n"  # L4
            "        printf(\"err\");\n"   # L5
            "    }\n"                      # L6
            "    // continue normally\n"   # L7
            "    x = 1;\n"                 # L8
            "}\n"                          # L9
        )
        partition = _make_partition(
            file_content=file_content,
            total_lines=9,
            functions=[
                FunctionUnit(
                    function_name="fn",
                    line_start=1, line_end=9, line_count=9,
                    signature="void fn(void)",
                ),
            ],
            sql_blocks=[
                EmbeddedSQLUnit(
                    block_id="sql_001",
                    function_name="fn",
                    sql_kind=SQLKind.SELECT,
                    raw_content="EXEC SQL SELECT ...",
                    sql_text="SELECT a INTO :x FROM tab",
                    origin_start_line=2,
                    origin_end_line=3,
                    line_count=2,
                    has_sqlca_check=True,
                ),
            ],
            c_segments=[
                CSegment(
                    segment_id="cseg_001",
                    function_name="fn",
                    origin_start_line=4,
                    origin_end_line=9,
                    line_count=6,
                ),
            ],
        )
        graph = _build_graph(partition)
        findings = checker.check(graph, partition)
        matched = [f for f in findings if f.rule_id == "CROSS_SQLCA_NO_ERROR_PATH"]
        assert len(matched) == 1

    def test_no_finding_with_return(self, checker):
        """SQLCA 체크 후 return이 있으면 OK."""
        file_content = (
            "void fn(void) {\n"                # L1
            "    EXEC SQL SELECT ...\n"         # L2
            "    ;\n"                           # L3
            "    if (sqlca.sqlcode < 0) {\n"    # L4
            "        return;\n"                 # L5
            "    }\n"                           # L6
            "}\n"                               # L7
        )
        partition = _make_partition(
            file_content=file_content,
            total_lines=7,
            functions=[
                FunctionUnit(
                    function_name="fn",
                    line_start=1, line_end=7, line_count=7,
                    signature="void fn(void)",
                ),
            ],
            sql_blocks=[
                EmbeddedSQLUnit(
                    block_id="sql_001",
                    function_name="fn",
                    sql_kind=SQLKind.SELECT,
                    raw_content="EXEC SQL SELECT ...",
                    sql_text="SELECT a INTO :x FROM tab",
                    origin_start_line=2,
                    origin_end_line=3,
                    line_count=2,
                    has_sqlca_check=True,
                ),
            ],
            c_segments=[
                CSegment(
                    segment_id="cseg_001",
                    function_name="fn",
                    origin_start_line=4,
                    origin_end_line=7,
                    line_count=4,
                ),
            ],
        )
        graph = _build_graph(partition)
        findings = checker.check(graph, partition)
        assert not any(f.rule_id == "CROSS_SQLCA_NO_ERROR_PATH" for f in findings)


# ──────────────────────────────────────────
# 규칙 6: CROSS_TRANSACTION_SPLIT
# ──────────────────────────────────────────


class TestTransactionSplit:
    def test_detects_split_transaction(self, checker):
        """DML과 COMMIT이 다른 함수, 호출 관계 없음."""
        # helper_fn → dml_fn 호출은 있지만 dml_fn → commit_fn 호출은 없음
        lines = [""] * 150
        lines[100] = "    dml_fn();"  # helper_fn에서 dml_fn 호출 (call graph 활성화용)
        file_content = "\n".join(lines)
        partition = _make_partition(
            file_content=file_content,
            total_lines=150,
            functions=[
                FunctionUnit(
                    function_name="dml_fn",
                    line_start=1, line_end=50, line_count=50,
                    signature="void dml_fn(void)",
                ),
                FunctionUnit(
                    function_name="commit_fn",
                    line_start=51, line_end=100, line_count=50,
                    signature="void commit_fn(void)",
                ),
                FunctionUnit(
                    function_name="helper_fn",
                    line_start=101, line_end=150, line_count=50,
                    signature="void helper_fn(void)",
                ),
            ],
            sql_blocks=[
                EmbeddedSQLUnit(
                    block_id="sql_001",
                    function_name="dml_fn",
                    sql_kind=SQLKind.INSERT,
                    raw_content="EXEC SQL INSERT ...",
                    sql_text="INSERT INTO tab VALUES (:x)",
                    origin_start_line=10,
                    origin_end_line=12,
                    line_count=3,
                ),
            ],
            transaction_points=[
                TransactionPoint(kind="COMMIT", function_name="commit_fn", line=60),
            ],
            c_segments=[
                CSegment(
                    segment_id="cseg_001",
                    function_name="dml_fn",
                    origin_start_line=1,
                    origin_end_line=50,
                    line_count=50,
                ),
                CSegment(
                    segment_id="cseg_002",
                    function_name="commit_fn",
                    origin_start_line=51,
                    origin_end_line=100,
                    line_count=50,
                ),
                CSegment(
                    segment_id="cseg_003",
                    function_name="helper_fn",
                    origin_start_line=101,
                    origin_end_line=150,
                    line_count=50,
                ),
            ],
        )
        graph = _build_graph(partition)
        findings = checker.check(graph, partition)
        matched = [f for f in findings if f.rule_id == "CROSS_TRANSACTION_SPLIT"]
        assert len(matched) == 1

    def test_no_finding_same_func(self, checker):
        """DML과 COMMIT이 같은 함수."""
        partition = _make_partition(
            functions=[
                FunctionUnit(
                    function_name="fn",
                    line_start=1, line_end=50, line_count=50,
                    signature="void fn(void)",
                ),
            ],
            sql_blocks=[
                EmbeddedSQLUnit(
                    block_id="sql_001",
                    function_name="fn",
                    sql_kind=SQLKind.INSERT,
                    raw_content="EXEC SQL INSERT ...",
                    sql_text="INSERT INTO tab VALUES (:x)",
                    origin_start_line=10,
                    origin_end_line=12,
                    line_count=3,
                ),
            ],
            transaction_points=[
                TransactionPoint(kind="COMMIT", function_name="fn", line=40),
            ],
        )
        graph = _build_graph(partition)
        findings = checker.check(graph, partition)
        assert not any(f.rule_id == "CROSS_TRANSACTION_SPLIT" for f in findings)


# ──────────────────────────────────────────
# 규칙 7: CROSS_TRANSACTION_MISSING_ROLLBACK
# ──────────────────────────────────────────


class TestTransactionMissingRollback:
    def test_detects_missing_rollback(self, checker):
        partition = _make_partition(
            transaction_points=[
                TransactionPoint(kind="COMMIT", function_name="fn", line=45),
            ],
        )
        graph = _build_graph(partition)
        findings = checker.check(graph, partition)
        matched = [f for f in findings if f.rule_id == "CROSS_TRANSACTION_MISSING_ROLLBACK"]
        assert len(matched) == 1
        assert matched[0].severity == "high"

    def test_no_finding_with_rollback(self, checker):
        partition = _make_partition(
            transaction_points=[
                TransactionPoint(kind="COMMIT", function_name="fn", line=45),
                TransactionPoint(kind="ROLLBACK", function_name="fn", line=50),
            ],
        )
        graph = _build_graph(partition)
        findings = checker.check(graph, partition)
        assert not any(f.rule_id == "CROSS_TRANSACTION_MISSING_ROLLBACK" for f in findings)


# ──────────────────────────────────────────
# Fallback / 빈 그래프
# ──────────────────────────────────────────


class TestFallback:
    def test_empty_graph_skips(self, checker):
        """SymbolGraph가 비어있으면 전체 건너뜀."""
        partition = _make_partition()
        empty_graph = SymbolGraph()
        findings = checker.check(empty_graph, partition)
        assert len(findings) == 0

    def test_finding_id_format(self, checker):
        """Finding ID는 CF- 접두사."""
        partition = _make_partition(
            transaction_points=[
                TransactionPoint(kind="COMMIT", function_name="fn", line=10),
            ],
        )
        graph = _build_graph(partition)
        findings = checker.check(graph, partition)
        for f in findings:
            assert f.finding_id.startswith("CF-")
            assert f.source_layer == "cross"
            assert f.tool == "cross_checker"
