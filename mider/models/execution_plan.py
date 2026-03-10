"""Phase 0 → Phase 1: ExecutionPlan 스키마.

TaskClassifierAgent가 생성하여 OrchestratorAgent에 반환하는 실행 계획.
"""

from datetime import datetime
from typing import List, Literal

from pydantic import BaseModel, Field


class FileMetadata(BaseModel):
    """파일 메타데이터."""

    file_size: int = Field(description="파일 크기 (bytes)")
    line_count: int = Field(description="총 라인 수")
    last_modified: datetime = Field(description="최종 수정 시각")


class SubTask(BaseModel):
    """분석 대상 파일 단위의 서브태스크."""

    task_id: str = Field(description="task_1, task_2, ...")
    file: str = Field(description="파일 경로 (절대경로)")
    language: Literal["javascript", "c", "proc", "sql", "xml"] = Field(
        description="파일 언어"
    )
    priority: int = Field(description="우선순위 1(높음) ~ N(낮음)")
    metadata: FileMetadata


class DependencyEdge(BaseModel):
    """파일 간 의존성 엣지."""

    source: str = Field(description="참조하는 파일")
    target: str = Field(description="참조되는 파일")
    type: Literal["import", "include", "exec_sql_include"] = Field(
        description="의존성 유형"
    )


class DependencyGraph(BaseModel):
    """파일 간 의존성 그래프."""

    edges: List[DependencyEdge] = Field(default_factory=list)
    has_circular: bool = Field(default=False, description="순환 의존성 여부")
    warnings: List[str] = Field(
        default_factory=list, description="순환 감지 시 경고 메시지"
    )


class ExecutionPlan(BaseModel):
    """Phase 0의 출력: 분석 실행 계획."""

    sub_tasks: List[SubTask]
    dependencies: DependencyGraph
    total_files: int = Field(description="분석 대상 파일 수")
    estimated_time_seconds: int = Field(description="예상 분석 시간 (초)")
