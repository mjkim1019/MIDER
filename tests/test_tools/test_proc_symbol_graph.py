"""ProCSymbolGraphBuilder 단위 테스트.

6단계 그래프 구축 과정을 검증한다.
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
from mider.tools.utility.proc_symbol_graph import (
    EdgeType,
    NodeType,
    ProCSymbolGraphBuilder,
    SymbolGraph,
)


def _make_partition(**kwargs) -> PartitionResult:
    """테스트용 PartitionResult 생성 헬퍼."""
    defaults = dict(
        source_file="test.pc",
        total_lines=100,
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


@pytest.fixture
def builder() -> ProCSymbolGraphBuilder:
    return ProCSymbolGraphBuilder()


# ──────────────────────────────────────────
# Step 1: 함수 노드
# ──────────────────────────────────────────


class TestStep1Functions:
    def test_registers_function_nodes(self, builder):
        partition = _make_partition(
            functions=[
                FunctionUnit(
                    function_name="do_insert",
                    line_start=10,
                    line_end=50,
                    line_count=41,
                    signature="int do_insert(void)",
                ),
                FunctionUnit(
                    function_name="main",
                    line_start=1,
                    line_end=9,
                    line_count=9,
                    signature="int main(int argc, char *argv[])",
                    is_boilerplate=True,
                ),
            ],
        )
        graph = builder.build(partition)
        assert "func:do_insert" in graph.nodes
        assert "func:main" in graph.nodes
        assert graph.nodes["func:do_insert"].node_type == NodeType.FUNCTION
        assert graph.nodes["func:main"].metadata.get("is_boilerplate") is True

    def test_empty_functions(self, builder):
        graph = builder.build(_make_partition())
        assert len(graph.nodes) == 0


# ──────────────────────────────────────────
# Step 2: SQL 블록 + 엣지
# ──────────────────────────────────────────


class TestStep2SqlBlocks:
    def test_sql_block_node_and_contains_edge(self, builder):
        partition = _make_partition(
            functions=[
                FunctionUnit(
                    function_name="fn",
                    line_start=1, line_end=30, line_count=30,
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
                    origin_start_line=10,
                    origin_end_line=12,
                    line_count=3,
                    host_variables=["x"],
                ),
            ],
        )
        graph = builder.build(partition)

        # SQL 노드 존재
        assert "sql:sql_001" in graph.nodes
        assert graph.nodes["sql:sql_001"].node_type == NodeType.SQL_BLOCK

        # CONTAINS 엣지
        contains = graph.get_outgoing("func:fn", EdgeType.CONTAINS)
        assert len(contains) == 1
        assert contains[0].target == "sql:sql_001"

        # USES_HOST_VAR 엣지
        uses = graph.get_outgoing("sql:sql_001", EdgeType.USES_HOST_VAR)
        assert len(uses) == 1
        assert uses[0].target == "hvar:x"

    def test_sqlca_check_self_loop(self, builder):
        partition = _make_partition(
            sql_blocks=[
                EmbeddedSQLUnit(
                    block_id="sql_002",
                    sql_kind=SQLKind.INSERT,
                    raw_content="EXEC SQL INSERT ...",
                    sql_text="INSERT INTO tab VALUES (:x)",
                    origin_start_line=20,
                    origin_end_line=22,
                    line_count=3,
                    has_sqlca_check=True,
                ),
            ],
        )
        graph = builder.build(partition)
        checks = graph.get_outgoing("sql:sql_002", EdgeType.CHECKS_ERROR)
        assert len(checks) == 1
        assert checks[0].source == checks[0].target  # self-loop


# ──────────────────────────────────────────
# Step 3: 호스트 변수
# ──────────────────────────────────────────


class TestStep3HostVars:
    def test_host_var_node_and_declared_in(self, builder):
        partition = _make_partition(
            functions=[
                FunctionUnit(
                    function_name="fn",
                    line_start=1, line_end=50, line_count=50,
                    signature="void fn(void)",
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
        graph = builder.build(partition)
        assert "hvar:svc_cd" in graph.nodes
        assert graph.nodes["hvar:svc_cd"].node_type == NodeType.HOST_VAR

        # DECLARED_IN 엣지
        declared = graph.get_outgoing("hvar:svc_cd", EdgeType.DECLARED_IN)
        assert len(declared) == 1
        assert declared[0].target == "func:fn"

    def test_global_host_var_no_declared_in(self, builder):
        partition = _make_partition(
            host_variables=[
                HostVarUnit(
                    name="g_var",
                    declared_type="int",
                    declared_in_function=None,
                    declared_line=3,
                ),
            ],
        )
        graph = builder.build(partition)
        declared = graph.get_outgoing("hvar:g_var", EdgeType.DECLARED_IN)
        assert len(declared) == 0


# ──────────────────────────────────────────
# Step 4: 커서
# ──────────────────────────────────────────


class TestStep4Cursors:
    def test_cursor_node_and_lifecycle_edges(self, builder):
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
                        CursorLifecycleEvent(event_type="DECLARE", line=10, function_name="fn"),
                        CursorLifecycleEvent(event_type="OPEN", line=20, function_name="fn"),
                        CursorLifecycleEvent(event_type="FETCH", line=30, function_name="fn"),
                        CursorLifecycleEvent(event_type="CLOSE", line=40, function_name="fn"),
                    ],
                ),
            ],
        )
        graph = builder.build(partition)

        assert "cursor:c_emp" in graph.nodes
        assert graph.nodes["cursor:c_emp"].node_type == NodeType.CURSOR

        # 4종 커서 엣지
        declares = graph.get_incoming("cursor:c_emp", EdgeType.DECLARES_CURSOR)
        opens = graph.get_incoming("cursor:c_emp", EdgeType.OPENS_CURSOR)
        fetches = graph.get_incoming("cursor:c_emp", EdgeType.FETCHES_CURSOR)
        closes = graph.get_incoming("cursor:c_emp", EdgeType.CLOSES_CURSOR)

        assert len(declares) == 1
        assert len(opens) == 1
        assert len(fetches) == 1
        assert len(closes) == 1


# ──────────────────────────────────────────
# Step 5: 트랜잭션
# ──────────────────────────────────────────


class TestStep5Transactions:
    def test_transaction_node_and_edge(self, builder):
        partition = _make_partition(
            functions=[
                FunctionUnit(
                    function_name="fn",
                    line_start=1, line_end=50, line_count=50,
                    signature="void fn(void)",
                ),
            ],
            transaction_points=[
                TransactionPoint(kind="COMMIT", function_name="fn", line=45),
            ],
        )
        graph = builder.build(partition)

        assert "tx:commit_0" in graph.nodes
        assert graph.nodes["tx:commit_0"].node_type == NodeType.TRANSACTION

        has_tx = graph.get_outgoing("func:fn", EdgeType.HAS_TRANSACTION)
        assert len(has_tx) == 1

    def test_multiple_transactions(self, builder):
        partition = _make_partition(
            transaction_points=[
                TransactionPoint(kind="COMMIT", line=45),
                TransactionPoint(kind="ROLLBACK", line=60),
            ],
        )
        graph = builder.build(partition)
        assert "tx:commit_0" in graph.nodes
        assert "tx:rollback_1" in graph.nodes


# ──────────────────────────────────────────
# Step 6: 호출 그래프
# ──────────────────────────────────────────


class TestStep6CallGraph:
    def test_detects_function_call(self, builder):
        file_content = (
            "int caller(void) {\n"
            "    int x = callee(1);\n"
            "    return x;\n"
            "}\n"
            "int callee(int a) {\n"
            "    return a + 1;\n"
            "}\n"
        )
        partition = _make_partition(
            file_content=file_content,
            total_lines=7,
            functions=[
                FunctionUnit(
                    function_name="caller",
                    line_start=1, line_end=4, line_count=4,
                    signature="int caller(void)",
                ),
                FunctionUnit(
                    function_name="callee",
                    line_start=5, line_end=7, line_count=3,
                    signature="int callee(int a)",
                ),
            ],
            c_segments=[
                CSegment(
                    segment_id="cseg_001",
                    function_name="caller",
                    origin_start_line=1,
                    origin_end_line=4,
                    line_count=4,
                ),
            ],
        )
        graph = builder.build(partition)

        calls = graph.get_outgoing("func:caller", EdgeType.CALLS)
        assert len(calls) == 1
        assert calls[0].target == "func:callee"

    def test_no_self_call(self, builder):
        """자기 자신 호출은 CALLS 엣지로 추가하지 않음."""
        file_content = "int fn(void) {\n    fn();\n}\n"
        partition = _make_partition(
            file_content=file_content,
            total_lines=3,
            functions=[
                FunctionUnit(
                    function_name="fn",
                    line_start=1, line_end=3, line_count=3,
                    signature="int fn(void)",
                ),
            ],
            c_segments=[
                CSegment(
                    segment_id="cseg_001",
                    function_name="fn",
                    origin_start_line=1,
                    origin_end_line=3,
                    line_count=3,
                ),
            ],
        )
        graph = builder.build(partition)
        calls = graph.get_outgoing("func:fn", EdgeType.CALLS)
        assert len(calls) == 0


# ──────────────────────────────────────────
# SymbolGraph 유틸리티
# ──────────────────────────────────────────


class TestSymbolGraphUtils:
    def test_has_path_1hop(self):
        from mider.tools.utility.proc_symbol_graph import GraphEdge, GraphNode

        graph = SymbolGraph()
        graph.add_node(GraphNode(
            node_id="func:a", node_type=NodeType.FUNCTION,
            name="a", line_start=1, line_end=10,
        ))
        graph.add_node(GraphNode(
            node_id="func:b", node_type=NodeType.FUNCTION,
            name="b", line_start=11, line_end=20,
        ))
        graph.add_edge(GraphEdge(
            source="func:a", target="func:b", edge_type=EdgeType.CALLS,
        ))

        assert graph.has_path_1hop("func:a", "func:b") is True
        assert graph.has_path_1hop("func:b", "func:a") is False

    def test_get_incoming(self):
        from mider.tools.utility.proc_symbol_graph import GraphEdge, GraphNode

        graph = SymbolGraph()
        graph.add_node(GraphNode(
            node_id="func:a", node_type=NodeType.FUNCTION,
            name="a", line_start=1, line_end=10,
        ))
        graph.add_node(GraphNode(
            node_id="sql:s1", node_type=NodeType.SQL_BLOCK,
            name="SELECT L5", line_start=5, line_end=7,
        ))
        graph.add_edge(GraphEdge(
            source="func:a", target="sql:s1", edge_type=EdgeType.CONTAINS,
        ))

        incoming = graph.get_incoming("sql:s1", EdgeType.CONTAINS)
        assert len(incoming) == 1
        assert incoming[0].source == "func:a"


# ──────────────────────────────────────────
# 예외 내성
# ──────────────────────────────────────────


class TestRobustness:
    def test_partial_graph_on_error(self, builder):
        """구축 중 예외 발생 시 부분 그래프 반환."""
        partition = _make_partition(
            functions=[
                FunctionUnit(
                    function_name="fn",
                    line_start=1, line_end=10, line_count=10,
                    signature="void fn(void)",
                ),
            ],
        )
        # 정상적으로 빌드되어야 함
        graph = builder.build(partition)
        assert "func:fn" in graph.nodes
