"""ExecutionPlan 스키마 단위 테스트."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from mider.models.execution_plan import (
    DependencyEdge,
    DependencyGraph,
    ExecutionPlan,
    FileMetadata,
    SubTask,
)


class TestFileMetadata:
    def test_valid(self):
        meta = FileMetadata(
            file_size=2048,
            line_count=85,
            last_modified=datetime(2026, 2, 20, 10, 30),
        )
        assert meta.file_size == 2048
        assert meta.line_count == 85

    def test_missing_required(self):
        with pytest.raises(ValidationError):
            FileMetadata(file_size=100, line_count=10)  # type: ignore[call-arg]


class TestSubTask:
    def test_valid(self):
        task = SubTask(
            task_id="task_1",
            file="/app/src/db/orders.sql",
            language="sql",
            priority=1,
            metadata=FileMetadata(
                file_size=2048,
                line_count=85,
                last_modified=datetime(2026, 2, 20, 10, 30),
            ),
        )
        assert task.task_id == "task_1"
        assert task.language == "sql"

    def test_invalid_language(self):
        with pytest.raises(ValidationError):
            SubTask(
                task_id="task_1",
                file="/app/test.py",
                language="python",  # type: ignore[arg-type]
                priority=1,
                metadata=FileMetadata(
                    file_size=100,
                    line_count=10,
                    last_modified=datetime(2026, 1, 1),
                ),
            )


class TestDependencyEdge:
    def test_valid(self):
        edge = DependencyEdge(
            source="/app/src/calc.c",
            target="/app/src/utils.h",
            type="include",
        )
        assert edge.type == "include"

    def test_invalid_type(self):
        with pytest.raises(ValidationError):
            DependencyEdge(
                source="/a",
                target="/b",
                type="unknown",  # type: ignore[arg-type]
            )


class TestDependencyGraph:
    def test_defaults(self):
        graph = DependencyGraph()
        assert graph.edges == []
        assert graph.has_circular is False
        assert graph.warnings == []

    def test_with_edges(self):
        edge = DependencyEdge(source="/a", target="/b", type="import")
        graph = DependencyGraph(edges=[edge], has_circular=False)
        assert len(graph.edges) == 1


class TestExecutionPlan:
    def test_valid(self):
        plan = ExecutionPlan(
            sub_tasks=[
                SubTask(
                    task_id="task_1",
                    file="/app/orders.sql",
                    language="sql",
                    priority=1,
                    metadata=FileMetadata(
                        file_size=2048,
                        line_count=85,
                        last_modified=datetime(2026, 2, 20, 10, 30),
                    ),
                ),
            ],
            dependencies=DependencyGraph(),
            total_files=1,
            estimated_time_seconds=60,
        )
        assert plan.total_files == 1
        assert len(plan.sub_tasks) == 1

    def test_json_roundtrip(self):
        plan = ExecutionPlan(
            sub_tasks=[
                SubTask(
                    task_id="task_1",
                    file="/app/orders.sql",
                    language="sql",
                    priority=1,
                    metadata=FileMetadata(
                        file_size=2048,
                        line_count=85,
                        last_modified=datetime(2026, 2, 20, 10, 30),
                    ),
                ),
            ],
            dependencies=DependencyGraph(
                edges=[
                    DependencyEdge(source="/a", target="/b", type="import")
                ],
                has_circular=False,
            ),
            total_files=1,
            estimated_time_seconds=120,
        )
        json_str = plan.model_dump_json()
        restored = ExecutionPlan.model_validate_json(json_str)
        assert restored.sub_tasks[0].task_id == "task_1"
        assert restored.dependencies.edges[0].type == "import"
