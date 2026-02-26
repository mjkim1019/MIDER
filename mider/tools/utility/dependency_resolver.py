"""DependencyResolver: 파일 간 의존성 분석 Tool.

선택된 파일 리스트에서 import/include 구문을 추출하여
파일 간 의존성 그래프를 생성한다. 순환 의존성도 탐지한다.
"""

import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult
from mider.tools.file_io.file_reader import FileReader

logger = logging.getLogger(__name__)

# 언어별 import/include 패턴
_JS_IMPORT_PATTERN = re.compile(
    r"(?:import\s+.*?from\s+['\"](.+?)['\"]|require\s*\(\s*['\"](.+?)['\"]\s*\))"
)
_C_INCLUDE_PATTERN = re.compile(r'#include\s+[<"](.+?)[>"]')
_PROC_INCLUDE_PATTERN = re.compile(
    r"(?:EXEC\s+SQL\s+INCLUDE\s+(\w+)|#include\s+[<\"](.+?)[>\"])",
    re.IGNORECASE,
)

# 확장자 → 언어 매핑
_EXT_TO_LANGUAGE: dict[str, str] = {
    ".js": "javascript",
    ".c": "c",
    ".h": "c",
    ".pc": "proc",
    ".sql": "sql",
}


def _detect_language(file_path: str) -> str | None:
    """파일 확장자로 언어를 감지한다."""
    ext = Path(file_path).suffix.lower()
    return _EXT_TO_LANGUAGE.get(ext)


def _extract_imports(content: str, language: str) -> list[tuple[str, str]]:
    """파일 내용에서 import/include를 추출한다.

    Returns:
        (참조 문자열, 의존성 유형) 튜플 리스트
    """
    results: list[tuple[str, str]] = []

    if language == "javascript":
        for match in _JS_IMPORT_PATTERN.finditer(content):
            ref = match.group(1) or match.group(2)
            results.append((ref, "import"))

    elif language == "c":
        for match in _C_INCLUDE_PATTERN.finditer(content):
            ref = match.group(1)
            results.append((ref, "include"))

    elif language == "proc":
        for match in _PROC_INCLUDE_PATTERN.finditer(content):
            ref = match.group(1) or match.group(2)
            dep_type = "exec_sql_include" if match.group(1) else "include"
            results.append((ref, dep_type))

    return results


def _resolve_ref(ref: str, source_file: str, file_set: set[str]) -> str | None:
    """import/include 참조를 선택된 파일 중에서 매칭한다."""
    source_dir = Path(source_file).parent

    # 상대 경로 시도
    for ext in ["", ".js", ".c", ".h", ".pc", ".sql"]:
        candidate = (source_dir / (ref + ext)).resolve()
        candidate_str = str(candidate)
        if candidate_str in file_set:
            return candidate_str

    # 파일명만으로 매칭
    ref_name = Path(ref).name
    for f in file_set:
        if Path(f).name == ref_name or Path(f).stem == Path(ref_name).stem:
            return f

    return None


def _detect_circular(
    edges: list[dict[str, str]],
) -> tuple[bool, list[str]]:
    """순환 의존성을 탐지한다 (DFS 기반)."""
    graph: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        graph[edge["source"]].append(edge["target"])

    visited: set[str] = set()
    rec_stack: set[str] = set()
    warnings: list[str] = []

    def dfs(node: str) -> bool:
        visited.add(node)
        rec_stack.add(node)
        for neighbor in graph[node]:
            if neighbor not in visited:
                if dfs(neighbor):
                    return True
            elif neighbor in rec_stack:
                warnings.append(
                    f"순환 의존성 발견: {node} → {neighbor}"
                )
                return True
        rec_stack.discard(node)
        return False

    for node in list(graph.keys()):
        if node not in visited:
            dfs(node)

    return len(warnings) > 0, warnings


class DependencyResolver(BaseTool):
    """파일 간 의존성을 분석하는 Tool."""

    def __init__(self) -> None:
        self._file_reader = FileReader()

    def execute(self, *, files: list[str], **kwargs: Any) -> ToolResult:
        """선택된 파일들의 의존성 그래프를 생성한다.

        Args:
            files: 분석 대상 파일 경로 리스트

        Returns:
            ToolResult (data: edges, has_circular, warnings)

        Raises:
            ToolExecutionError: 파일 목록이 비어있을 때
        """
        if not files:
            raise ToolExecutionError(
                "dependency_resolver", "files list is empty"
            )

        # 절대 경로로 정규화
        resolved_files = [str(Path(f).resolve()) for f in files]
        file_set = set(resolved_files)

        edges: list[dict[str, str]] = []

        for file_path in resolved_files:
            language = _detect_language(file_path)
            if language is None or language == "sql":
                continue

            try:
                read_result = self._file_reader.execute(path=file_path)
            except ToolExecutionError:
                logger.warning(f"파일 읽기 실패, 건너뜀: {file_path}")
                continue

            content = read_result.data["content"]
            imports = _extract_imports(content, language)

            for ref, dep_type in imports:
                target = _resolve_ref(ref, file_path, file_set)
                if target is not None and target != file_path:
                    edges.append({
                        "source": file_path,
                        "target": target,
                        "type": dep_type,
                    })

        has_circular, warnings = _detect_circular(edges)

        logger.debug(
            f"의존성 분석 완료: {len(resolved_files)}개 파일, "
            f"{len(edges)}개 엣지, 순환: {has_circular}"
        )

        return ToolResult(
            success=True,
            data={
                "edges": edges,
                "has_circular": has_circular,
                "warnings": warnings,
            },
        )
