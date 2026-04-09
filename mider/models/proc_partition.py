"""ProC V3 분리 검사 데이터 구조.

ProCPartitioner가 .pc 파일을 분해한 결과와,
분석 파이프라인 전체에서 사용하는 중간/최종 데이터 모델을 정의한다.

설계 원칙:
- CSegment는 content를 저장하지 않는다 (원본 file_content + line 범위로 참조).
- 모든 객체는 origin_start_line / origin_end_line을 가진다 (원본 line 복원용).
- Finding은 내부 분석용, Issue(analysis_result.py)는 최종 출력용.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# 보조 타입
# ──────────────────────────────────────────────


class IncludeDirective(BaseModel):
    """#include 또는 EXEC SQL INCLUDE 지시문."""

    statement: str = Field(description="원본 구문 (예: '#include <stdio.h>')")
    line: int = Field(description="원본 line 번호 (1-based)")
    is_exec_sql: bool = Field(default=False, description="EXEC SQL INCLUDE 여부")


class DeclareSection(BaseModel):
    """EXEC SQL BEGIN/END DECLARE SECTION 블록."""

    origin_start_line: int
    origin_end_line: int
    raw_content: str = Field(description="블록 전체 원문")


class TypeDef(BaseModel):
    """typedef / struct 정의."""

    statement: str
    line: int


class GlobalVar(BaseModel):
    """함수 밖 전역 변수 선언."""

    statement: str
    line: int


class MacroDef(BaseModel):
    """#define 매크로 정의."""

    statement: str
    line: int


class WheneverDirective(BaseModel):
    """EXEC SQL WHENEVER 지시문.

    WHENEVER는 선언 이후의 모든 EXEC SQL에 적용되는 전역 에러 핸들러이다.
    """

    condition: Literal["SQLERROR", "NOT_FOUND", "SQLWARNING"] = Field(
        description="트리거 조건"
    )
    action: str = Field(
        description="액션 (CONTINUE, GOTO label, DO func, STOP)"
    )
    line: int = Field(description="원본 line 번호")
    function_name: Optional[str] = Field(
        default=None, description="함수 내부이면 함수명, 밖이면 None"
    )

    @property
    def suppresses_sqlca_check(self) -> bool:
        """이 WHENEVER가 명시적 SQLCA 검사를 대체하는지 여부."""
        return self.action.upper() != "CONTINUE"


# ──────────────────────────────────────────────
# GlobalContext
# ──────────────────────────────────────────────


class GlobalContext(BaseModel):
    """파일 전역 메타데이터."""

    includes: list[IncludeDirective] = Field(default_factory=list)
    declare_sections: list[DeclareSection] = Field(default_factory=list)
    type_definitions: list[TypeDef] = Field(default_factory=list)
    global_variables: list[GlobalVar] = Field(default_factory=list)
    macros: list[MacroDef] = Field(default_factory=list)
    whenever_directives: list[WheneverDirective] = Field(default_factory=list)


# ──────────────────────────────────────────────
# FunctionUnit
# ──────────────────────────────────────────────


class FunctionUnit(BaseModel):
    """함수 경계 단위."""

    function_name: str
    line_start: int = Field(description="원본 line (1-based)")
    line_end: int
    line_count: int
    signature: str = Field(description="예: 'int prt_ins_sel_agrmt_guid(...)'")
    is_boilerplate: bool = Field(
        default=False,
        description="main, *_init_proc, *_exit_proc 등 보일러플레이트 여부",
    )


# ──────────────────────────────────────────────
# CSegment
# ──────────────────────────────────────────────


class CSegment(BaseModel):
    """C 코드 영역 (SQL 블록 사이의 순수 C 코드).

    content를 직접 저장하지 않는다.
    원본 file_content에 대한 view 방식으로, line 범위만 보유한다.
    """

    segment_id: str = Field(description="예: 'cseg_001'")
    function_name: Optional[str] = Field(
        default=None, description="함수 밖이면 None"
    )
    origin_start_line: int
    origin_end_line: int
    line_count: int
    line_map: Optional[dict[int, int]] = Field(
        default=None,
        description="내부 line → 원본 line (불연속 시만 사용)",
    )

    def get_content(self, file_content: str) -> str:
        """원본 파일에서 해당 segment의 코드를 추출한다."""
        lines = file_content.splitlines()
        return "\n".join(
            lines[self.origin_start_line - 1 : self.origin_end_line]
        )


# ──────────────────────────────────────────────
# EmbeddedSQLUnit
# ──────────────────────────────────────────────


class SQLKind(str, Enum):
    """EXEC SQL 블록의 종류."""

    SELECT = "SELECT"
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    MERGE = "MERGE"
    CURSOR_DECLARE = "CURSOR_DECLARE"
    CURSOR_OPEN = "CURSOR_OPEN"
    CURSOR_FETCH = "CURSOR_FETCH"
    CURSOR_CLOSE = "CURSOR_CLOSE"
    COMMIT = "COMMIT"
    ROLLBACK = "ROLLBACK"
    SAVEPOINT = "SAVEPOINT"
    OTHER = "OTHER"


class EmbeddedSQLUnit(BaseModel):
    """EXEC SQL 블록 단위."""

    block_id: str = Field(description="예: 'sql_014'")
    function_name: Optional[str] = Field(default=None)
    sql_kind: SQLKind
    raw_content: str = Field(description="'EXEC SQL SELECT ... ;' 전체")
    sql_text: str = Field(description="'SELECT ... INTO :a, :b FROM ...'")
    origin_start_line: int
    origin_end_line: int
    line_count: int
    line_map: Optional[dict[int, int]] = Field(default=None)

    host_variables: list[str] = Field(default_factory=list)
    indicator_variables: list[str] = Field(default_factory=list)
    has_sqlca_check: bool = Field(default=False)
    active_whenever: Optional[str] = Field(
        default=None,
        description="이 블록 시점의 활성 WHENEVER 지시문 요약",
    )


# ──────────────────────────────────────────────
# HostVarUnit
# ──────────────────────────────────────────────


class HostVarUnit(BaseModel):
    """호스트 변수 선언."""

    name: str = Field(description="예: 'svc_cd'")
    indicator_name: Optional[str] = Field(
        default=None, description="예: 'svc_cd_ind'"
    )
    declared_type: str = Field(
        description="예: 'char[32]', 'int', 'unknown'"
    )
    declared_in_function: Optional[str] = Field(
        default=None, description="함수 내 선언이면 함수명, 전역이면 None"
    )
    declared_line: int
    is_struct_member: bool = Field(default=False)
    struct_name: Optional[str] = Field(default=None)


# ──────────────────────────────────────────────
# CursorUnit
# ──────────────────────────────────────────────


class CursorLifecycleEvent(BaseModel):
    """커서 lifecycle 이벤트 하나."""

    event_type: Literal["DECLARE", "OPEN", "FETCH", "CLOSE"]
    line: int = Field(description="원본 line 번호")
    function_name: Optional[str] = Field(default=None)


class CursorUnit(BaseModel):
    """커서 lifecycle 추적 단위."""

    cursor_name: str
    events: list[CursorLifecycleEvent] = Field(default_factory=list)

    @property
    def declare_functions(self) -> list[str]:
        return [
            e.function_name
            for e in self.events
            if e.event_type == "DECLARE" and e.function_name
        ]

    @property
    def open_functions(self) -> list[str]:
        return [
            e.function_name
            for e in self.events
            if e.event_type == "OPEN" and e.function_name
        ]

    @property
    def close_functions(self) -> list[str]:
        return [
            e.function_name
            for e in self.events
            if e.event_type == "CLOSE" and e.function_name
        ]

    @property
    def is_complete(self) -> bool:
        types = {e.event_type for e in self.events}
        return types >= {"DECLARE", "OPEN", "FETCH", "CLOSE"}

    @property
    def missing_events(self) -> list[str]:
        """lifecycle에서 누락된 이벤트 타입 목록."""
        present = {e.event_type for e in self.events}
        required = {"DECLARE", "OPEN", "FETCH", "CLOSE"}
        return sorted(required - present)


# ──────────────────────────────────────────────
# TransactionPoint
# ──────────────────────────────────────────────


class TransactionPoint(BaseModel):
    """COMMIT / ROLLBACK / SAVEPOINT 위치."""

    kind: Literal["COMMIT", "ROLLBACK", "SAVEPOINT"]
    function_name: Optional[str] = Field(default=None)
    line: int


# ──────────────────────────────────────────────
# PartitionResult (최상위 컨테이너)
# ──────────────────────────────────────────────


class PartitionResult(BaseModel):
    """ProCPartitioner의 출력: .pc 파일 분해 결과 전체."""

    source_file: str
    encoding: str = Field(default="utf-8")
    total_lines: int
    file_content: str = Field(description="원본 전체 내용 (참조용)")

    global_context: GlobalContext = Field(default_factory=GlobalContext)
    functions: list[FunctionUnit] = Field(default_factory=list)
    c_segments: list[CSegment] = Field(default_factory=list)
    sql_blocks: list[EmbeddedSQLUnit] = Field(default_factory=list)
    host_variables: list[HostVarUnit] = Field(default_factory=list)
    cursor_map: list[CursorUnit] = Field(default_factory=list)
    transaction_points: list[TransactionPoint] = Field(default_factory=list)

    def get_function_by_line(self, line: int) -> Optional[FunctionUnit]:
        """주어진 line이 속하는 함수를 반환한다."""
        for func in self.functions:
            if func.line_start <= line <= func.line_end:
                return func
        return None

    def get_function_name_by_line(self, line: int) -> Optional[str]:
        """주어진 line이 속하는 함수명을 반환한다."""
        func = self.get_function_by_line(line)
        return func.function_name if func else None


# ──────────────────────────────────────────────
# Finding (공통 분석 결과 — 내부용)
# ──────────────────────────────────────────────


class Finding(BaseModel):
    """계층 1~3에서 탐지한 개별 결함 (내부 파이프라인용).

    최종 사용자에게 전달되는 Issue(analysis_result.py)와 구분된다.
    IssueMerger가 Finding → Issue로 변환한다.
    """

    finding_id: str = Field(description="예: 'SF-001' (static finding)")
    source_layer: Literal["static", "cross", "llm"] = Field(
        description="탐지 계층"
    )
    tool: str = Field(
        description="탐지 도구 (c_heuristic, proc_heuristic, proc_runner, "
        "embedded_sql_static, cross_checker, llm_reviewer)"
    )
    rule_id: str = Field(description="규칙별 고유 ID")
    severity: Literal["critical", "high", "medium", "low"]
    category: str = Field(
        description="memory_safety, null_safety, data_integrity 등"
    )
    title: str
    description: str
    origin_line_start: int = Field(description="원본 .pc 기준")
    origin_line_end: int
    function_name: Optional[str] = Field(default=None)
    raw_match: str = Field(default="", description="매칭된 코드 스니펫")
    confidence: float = Field(
        default=1.0, description="정적분석 1.0, LLM은 가변"
    )
    metadata: dict = Field(default_factory=dict)
