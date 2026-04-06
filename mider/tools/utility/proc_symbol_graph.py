"""ProCSymbolGraphBuilder: PartitionResult → SymbolGraph 관계 그래프 구축.

설계서 V3 §3.2 기반.
노드 타입: FUNCTION, SQL_BLOCK, HOST_VAR, CURSOR, TRANSACTION
엣지 타입: CONTAINS, USES_HOST_VAR, DECLARED_IN, cursor 4종, HAS_TRANSACTION, CALLS, CHECKS_ERROR
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from mider.models.proc_partition import (
    CSegment,
    CursorUnit,
    EmbeddedSQLUnit,
    FunctionUnit,
    HostVarUnit,
    PartitionResult,
    SQLKind,
    TransactionPoint,
)


# ──────────────────────────────────────────────
# 노드/엣지 타입
# ──────────────────────────────────────────────


class NodeType(str, Enum):
    FUNCTION = "function"
    SQL_BLOCK = "sql_block"
    HOST_VAR = "host_var"
    CURSOR = "cursor"
    TRANSACTION = "transaction"


class EdgeType(str, Enum):
    CONTAINS = "contains"
    USES_HOST_VAR = "uses_host_var"
    DECLARED_IN = "declared_in"
    DECLARES_CURSOR = "declares_cursor"
    OPENS_CURSOR = "opens_cursor"
    FETCHES_CURSOR = "fetches_cursor"
    CLOSES_CURSOR = "closes_cursor"
    HAS_TRANSACTION = "has_transaction"
    CALLS = "calls"
    CHECKS_ERROR = "checks_error"


# ──────────────────────────────────────────────
# 그래프 데이터 구조
# ──────────────────────────────────────────────


@dataclass
class GraphNode:
    node_id: str
    node_type: NodeType
    name: str
    line_start: int
    line_end: int
    metadata: dict = field(default_factory=dict)


@dataclass
class GraphEdge:
    source: str  # node_id
    target: str  # node_id
    edge_type: EdgeType
    metadata: dict = field(default_factory=dict)


@dataclass
class SymbolGraph:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)
    adjacency: dict[str, list[GraphEdge]] = field(default_factory=dict)
    reverse_adjacency: dict[str, list[GraphEdge]] = field(default_factory=dict)

    def add_node(self, node: GraphNode) -> None:
        self.nodes[node.node_id] = node
        if node.node_id not in self.adjacency:
            self.adjacency[node.node_id] = []
        if node.node_id not in self.reverse_adjacency:
            self.reverse_adjacency[node.node_id] = []

    def add_edge(self, edge: GraphEdge) -> None:
        self.edges.append(edge)
        self.adjacency.setdefault(edge.source, []).append(edge)
        self.reverse_adjacency.setdefault(edge.target, []).append(edge)

    def get_outgoing(self, node_id: str, edge_type: EdgeType | None = None) -> list[GraphEdge]:
        edges = self.adjacency.get(node_id, [])
        if edge_type:
            return [e for e in edges if e.edge_type == edge_type]
        return edges

    def get_incoming(self, node_id: str, edge_type: EdgeType | None = None) -> list[GraphEdge]:
        edges = self.reverse_adjacency.get(node_id, [])
        if edge_type:
            return [e for e in edges if e.edge_type == edge_type]
        return edges

    def has_path_1hop(self, source: str, target: str) -> bool:
        """source → target 1-hop 직접 CALLS 경로가 있는지 확인."""
        for edge in self.adjacency.get(source, []):
            if edge.edge_type == EdgeType.CALLS and edge.target == target:
                return True
        return False


# ──────────────────────────────────────────────
# Builder
# ──────────────────────────────────────────────

# 함수명 추출 정규식 (기존 sql_extractor의 _FUNC_NAME_PATTERN)
_FUNC_NAME_PATTERN = re.compile(
    r"^(?!\s*(?:if|else|for|while|switch|return|#|typedef|struct|union|enum)\b)"
    r"\s*(?:static\s+|extern\s+|inline\s+)*"
    r"(?:void|int|char|long|short|unsigned|float|double|size_t|ssize_t|\w+_t|\w+)\s*\*?\s+"
    r"(\w+)\s*\("
)

# 커서 이벤트 → 엣지 타입 매핑
_CURSOR_EVENT_EDGE: dict[str, EdgeType] = {
    "DECLARE": EdgeType.DECLARES_CURSOR,
    "OPEN": EdgeType.OPENS_CURSOR,
    "FETCH": EdgeType.FETCHES_CURSOR,
    "CLOSE": EdgeType.CLOSES_CURSOR,
}


class ProCSymbolGraphBuilder:
    """PartitionResult로부터 SymbolGraph를 구축한다."""

    def build(self, partition: PartitionResult) -> SymbolGraph:
        """6단계 순회로 그래프를 구축한다."""
        graph = SymbolGraph()

        try:
            self._step1_functions(graph, partition.functions)
            self._step2_sql_blocks(graph, partition.sql_blocks)
            self._step3_host_vars(graph, partition.host_variables)
            self._step4_cursors(graph, partition.cursor_map)
            self._step5_transactions(graph, partition.transaction_points)
            self._step6_call_graph(graph, partition)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "SymbolGraph 구축 중 예외, 부분 그래프 반환", exc_info=True,
            )

        return graph

    def _step1_functions(
        self, graph: SymbolGraph, functions: list[FunctionUnit],
    ) -> None:
        for func in functions:
            graph.add_node(GraphNode(
                node_id=f"func:{func.function_name}",
                node_type=NodeType.FUNCTION,
                name=func.function_name,
                line_start=func.line_start,
                line_end=func.line_end,
                metadata={"is_boilerplate": func.is_boilerplate},
            ))

    def _step2_sql_blocks(
        self, graph: SymbolGraph, sql_blocks: list[EmbeddedSQLUnit],
    ) -> None:
        for block in sql_blocks:
            node_id = f"sql:{block.block_id}"
            graph.add_node(GraphNode(
                node_id=node_id,
                node_type=NodeType.SQL_BLOCK,
                name=f"{block.sql_kind.value} L{block.origin_start_line}",
                line_start=block.origin_start_line,
                line_end=block.origin_end_line,
                metadata={"sql_kind": block.sql_kind.value},
            ))

            # 함수 → SQL CONTAINS
            if block.function_name:
                graph.add_edge(GraphEdge(
                    source=f"func:{block.function_name}",
                    target=node_id,
                    edge_type=EdgeType.CONTAINS,
                ))

            # SQL → host var USES_HOST_VAR
            for hvar in block.host_variables:
                graph.add_edge(GraphEdge(
                    source=node_id,
                    target=f"hvar:{hvar}",
                    edge_type=EdgeType.USES_HOST_VAR,
                ))

            # SQLCA 체크 엣지
            if block.has_sqlca_check:
                graph.add_edge(GraphEdge(
                    source=node_id,
                    target=node_id,  # self-loop
                    edge_type=EdgeType.CHECKS_ERROR,
                ))

    def _step3_host_vars(
        self, graph: SymbolGraph, host_variables: list[HostVarUnit],
    ) -> None:
        for hvar in host_variables:
            node_id = f"hvar:{hvar.name}"
            graph.add_node(GraphNode(
                node_id=node_id,
                node_type=NodeType.HOST_VAR,
                name=hvar.name,
                line_start=hvar.declared_line,
                line_end=hvar.declared_line,
                metadata={
                    "declared_type": hvar.declared_type,
                    "indicator": hvar.indicator_name,
                },
            ))
            # DECLARED_IN 엣지
            if hvar.declared_in_function:
                graph.add_edge(GraphEdge(
                    source=f"hvar:{hvar.name}",
                    target=f"func:{hvar.declared_in_function}",
                    edge_type=EdgeType.DECLARED_IN,
                ))

    def _step4_cursors(
        self, graph: SymbolGraph, cursor_map: list[CursorUnit],
    ) -> None:
        for cursor in cursor_map:
            node_id = f"cursor:{cursor.cursor_name}"
            first_line = cursor.events[0].line if cursor.events else 0
            last_line = cursor.events[-1].line if cursor.events else 0
            graph.add_node(GraphNode(
                node_id=node_id,
                node_type=NodeType.CURSOR,
                name=cursor.cursor_name,
                line_start=first_line,
                line_end=last_line,
            ))
            for event in cursor.events:
                edge_type = _CURSOR_EVENT_EDGE.get(event.event_type)
                if edge_type and event.function_name:
                    graph.add_edge(GraphEdge(
                        source=f"func:{event.function_name}",
                        target=node_id,
                        edge_type=edge_type,
                        metadata={"line": event.line},
                    ))

    def _step5_transactions(
        self, graph: SymbolGraph, transaction_points: list[TransactionPoint],
    ) -> None:
        for i, tp in enumerate(transaction_points):
            node_id = f"tx:{tp.kind.lower()}_{i}"
            graph.add_node(GraphNode(
                node_id=node_id,
                node_type=NodeType.TRANSACTION,
                name=f"{tp.kind} L{tp.line}",
                line_start=tp.line,
                line_end=tp.line,
            ))
            if tp.function_name:
                graph.add_edge(GraphEdge(
                    source=f"func:{tp.function_name}",
                    target=node_id,
                    edge_type=EdgeType.HAS_TRANSACTION,
                ))

    def _step6_call_graph(
        self, graph: SymbolGraph, partition: PartitionResult,
    ) -> None:
        """C segment에서 함수 호출 관계를 휴리스틱으로 추출한다."""
        known_funcs = {f.function_name for f in partition.functions}
        if not known_funcs:
            return

        # 함수명 패턴: \bfunc_name\s*\(
        func_call_pattern = re.compile(
            r"\b(" + "|".join(re.escape(f) for f in known_funcs) + r")\s*\("
        )

        for seg in partition.c_segments:
            if not seg.function_name:
                continue
            content = seg.get_content(partition.file_content)
            for m in func_call_pattern.finditer(content):
                callee = m.group(1)
                if callee != seg.function_name:  # 자기 자신 호출 제외
                    graph.add_edge(GraphEdge(
                        source=f"func:{seg.function_name}",
                        target=f"func:{callee}",
                        edge_type=EdgeType.CALLS,
                    ))
