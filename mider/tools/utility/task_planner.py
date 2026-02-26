"""TaskPlanner: 분석 실행 계획 생성 Tool.

파일 목록과 의존성 그래프를 받아 분석 순서를 결정하고
ExecutionPlan 형태의 실행 계획을 생성한다.
"""

import logging
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult
from mider.tools.file_io.file_reader import FileReader

logger = logging.getLogger(__name__)

# 확장자 → 언어 매핑
_EXT_TO_LANGUAGE: dict[str, str] = {
    ".js": "javascript",
    ".c": "c",
    ".h": "c",
    ".pc": "proc",
    ".sql": "sql",
}

# 언어별 기본 분석 시간 추정 (초/파일)
_ESTIMATED_TIME_PER_FILE: dict[str, int] = {
    "javascript": 15,
    "c": 20,
    "proc": 25,
    "sql": 10,
}


def _detect_language(file_path: str) -> str | None:
    """파일 확장자로 언어를 감지한다."""
    ext = Path(file_path).suffix.lower()
    return _EXT_TO_LANGUAGE.get(ext)


def _topological_sort(
    files: list[str],
    edges: list[dict[str, str]],
) -> list[str]:
    """의존성 그래프 기반 토폴로지 정렬 (Kahn's algorithm).

    의존되는 파일(하위)이 먼저 오도록 정렬한다.
    순환 의존성이 있으면 남은 파일을 그대로 추가한다.
    """
    in_degree: dict[str, int] = defaultdict(int)
    graph: dict[str, list[str]] = defaultdict(list)
    file_set = set(files)

    for f in files:
        in_degree.setdefault(f, 0)

    for edge in edges:
        src, tgt = edge["source"], edge["target"]
        if src in file_set and tgt in file_set:
            graph[tgt].append(src)  # tgt가 먼저 분석되어야 함
            in_degree[src] = in_degree.get(src, 0) + 1

    queue = deque(f for f in files if in_degree.get(f, 0) == 0)
    sorted_files: list[str] = []

    while queue:
        node = queue.popleft()
        sorted_files.append(node)
        for neighbor in graph[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # 순환이 있으면 나머지 파일도 추가
    remaining = [f for f in files if f not in set(sorted_files)]
    sorted_files.extend(remaining)

    return sorted_files


class TaskPlanner(BaseTool):
    """분석 실행 계획을 생성하는 Tool."""

    def __init__(self) -> None:
        self._file_reader = FileReader()

    def execute(
        self,
        *,
        files: list[str],
        edges: list[dict[str, str]] | None = None,
        has_circular: bool = False,
        warnings: list[str] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        """파일 목록과 의존성 정보로 실행 계획을 생성한다.

        Args:
            files: 분석 대상 파일 경로 리스트
            edges: 의존성 엣지 리스트 (DependencyResolver 결과)
            has_circular: 순환 의존성 여부
            warnings: 의존성 경고 메시지 리스트

        Returns:
            ToolResult (data: sub_tasks, dependencies, total_files, estimated_time_seconds)

        Raises:
            ToolExecutionError: 파일 목록이 비어있거나 지원하지 않는 파일 포함 시
        """
        if not files:
            raise ToolExecutionError("task_planner", "files list is empty")

        edges = edges or []
        warnings = warnings or []

        # 지원 언어 확인 및 필터
        valid_files: list[str] = []
        for f in files:
            lang = _detect_language(f)
            if lang is None:
                logger.warning(f"지원하지 않는 파일 확장자, 건너뜀: {f}")
                continue
            valid_files.append(f)

        if not valid_files:
            raise ToolExecutionError(
                "task_planner",
                "no supported files found. supported: .js, .c, .h, .pc, .sql",
            )

        # 토폴로지 정렬
        sorted_files = _topological_sort(valid_files, edges)

        # SubTask 생성
        sub_tasks: list[dict[str, Any]] = []
        total_estimated = 0

        for priority, file_path in enumerate(sorted_files, start=1):
            language = _detect_language(file_path)
            if language is None:
                continue

            path = Path(file_path)
            # 파일 메타데이터 수집
            try:
                stat = path.stat()
                file_size = stat.st_size
                last_modified = datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat()

                read_result = self._file_reader.execute(path=file_path)
                line_count = read_result.data["line_count"]
            except Exception:
                file_size = 0
                line_count = 0
                last_modified = datetime.now(tz=timezone.utc).isoformat()

            est_time = _ESTIMATED_TIME_PER_FILE.get(language, 15)
            total_estimated += est_time

            sub_tasks.append({
                "task_id": f"task_{priority}",
                "file": file_path,
                "language": language,
                "priority": priority,
                "metadata": {
                    "file_size": file_size,
                    "line_count": line_count,
                    "last_modified": last_modified,
                },
            })

        logger.debug(
            f"실행 계획 생성 완료: {len(sub_tasks)}개 태스크, "
            f"예상 {total_estimated}초"
        )

        return ToolResult(
            success=True,
            data={
                "sub_tasks": sub_tasks,
                "dependencies": {
                    "edges": edges,
                    "has_circular": has_circular,
                    "warnings": warnings,
                },
                "total_files": len(sub_tasks),
                "estimated_time_seconds": total_estimated,
            },
        )
