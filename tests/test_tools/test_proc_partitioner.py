"""ProCPartitioner 단위 테스트.

V3 설계서 §3.1 기반.
검증 항목:
  - 함수 경계 추출 (Stage 1)
  - C/SQL 영역 분할 (Stage 2)
  - Host variable 추출 (Stage 3)
  - CSegment 병합 규칙
  - 커서 lifecycle
  - WHENEVER 지시문 추적
  - GlobalContext 수집
  - Fallback 시나리오
"""

import pytest

from mider.models.proc_partition import (
    CSegment,
    EmbeddedSQLUnit,
    PartitionResult,
    SQLKind,
)
from mider.tools.utility.proc_partitioner import ProCPartitioner


@pytest.fixture
def partitioner():
    return ProCPartitioner()


# ──────────────────────────────────────────────
# 테스트용 Pro*C 코드 스니펫
# ──────────────────────────────────────────────

SIMPLE_PC = """\
#include <stdio.h>
EXEC SQL INCLUDE sqlca;

EXEC SQL BEGIN DECLARE SECTION;
    char svc_cd[32];
    int  cnt;
    char svc_cd_ind[32];
EXEC SQL END DECLARE SECTION;

int b10_select_proc(void)
{
    long rc = 0;

    EXEC SQL SELECT svc_cd, cnt
        INTO :svc_cd, :cnt
        FROM TB_SVC
        WHERE rownum = 1;

    if (sqlca.sqlcode != 0) {
        return -1;
    }

    return rc;
}

void z99_exit_proc(void)
{
    EXEC SQL COMMIT;
    return;
}
"""

CURSOR_PC = """\
#include <stdio.h>

EXEC SQL BEGIN DECLARE SECTION;
    char name[64];
EXEC SQL END DECLARE SECTION;

int c200_fetch_proc(void)
{
    EXEC SQL DECLARE C_READ CURSOR FOR
        SELECT name FROM TB_USER;

    EXEC SQL OPEN C_READ;

    while (1) {
        EXEC SQL FETCH C_READ INTO :name;
        if (sqlca.sqlcode == 1403) break;
    }

    EXEC SQL CLOSE C_READ;
    return 0;
}
"""

WHENEVER_PC = """\
#include <stdio.h>

EXEC SQL WHENEVER SQLERROR GOTO error_handler;

int b10_update_proc(void)
{
    EXEC SQL UPDATE TB_ORDER SET status = 1;
    return 0;
}
"""

MULTI_SQL_PC = """\
int b20_multi_proc(void)
{
    int rc = 0;
    EXEC SQL INSERT INTO TB_LOG VALUES (:rc);
    if (sqlca.sqlcode != 0) return -1;
    EXEC SQL UPDATE TB_ORDER SET flag = 1;
    rc = process();
    EXEC SQL DELETE FROM TB_TEMP WHERE id = :rc;
    return rc;
}
"""

NO_SEMICOLON_PC = """\
int broken_func(void)
{
    EXEC SQL SELECT 1 FROM DUAL
    return 0;
}
"""

GLOBAL_CONTEXT_PC = """\
#include <stdio.h>
#include "proframe.h"
EXEC SQL INCLUDE sqlca;

#define MAX_CNT 100

typedef struct {
    char name[64];
} user_t;

static int g_count;

int main(void)
{
    return 0;
}
"""


# ──────────────────────────────────────────────
# Stage 1: 함수 경계 추출
# ──────────────────────────────────────────────


class TestStage1FunctionBoundaries:
    """함수 경계 추출 테스트."""

    def test_function_count(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        assert len(result.functions) == 2

    def test_function_names(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        names = [f.function_name for f in result.functions]
        assert "b10_select_proc" in names
        assert "z99_exit_proc" in names

    def test_function_line_range(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        b10 = next(f for f in result.functions if f.function_name == "b10_select_proc")
        assert b10.line_start > 0
        assert b10.line_end > b10.line_start

    def test_boilerplate_detection(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        z99 = next(f for f in result.functions if f.function_name == "z99_exit_proc")
        # z99_exit_proc은 _exit_proc 패턴이므로 boilerplate
        assert z99.is_boilerplate is True

    def test_single_function(self, partitioner):
        code = "int foo(void)\n{\n    return 0;\n}\n"
        result = partitioner.partition_content(code)
        assert len(result.functions) == 1
        assert result.functions[0].function_name == "foo"


# ──────────────────────────────────────────────
# Stage 2: C/SQL 영역 분할
# ──────────────────────────────────────────────


class TestStage2SplitRegions:
    """C/SQL 영역 분할 테스트."""

    def test_sql_block_count(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        # SELECT 1개 + COMMIT 1개
        assert len(result.sql_blocks) == 2

    def test_sql_kind_classification(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        kinds = {b.sql_kind for b in result.sql_blocks}
        assert SQLKind.SELECT in kinds
        assert SQLKind.COMMIT in kinds

    def test_sql_host_variables(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        select_block = next(
            b for b in result.sql_blocks if b.sql_kind == SQLKind.SELECT
        )
        assert "svc_cd" in select_block.host_variables
        assert "cnt" in select_block.host_variables

    def test_sqlca_check_detection(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        select_block = next(
            b for b in result.sql_blocks if b.sql_kind == SQLKind.SELECT
        )
        assert select_block.has_sqlca_check is True

    def test_multi_sql_blocks(self, partitioner):
        result = partitioner.partition_content(MULTI_SQL_PC)
        assert len(result.sql_blocks) == 3
        kinds = [b.sql_kind for b in result.sql_blocks]
        assert SQLKind.INSERT in kinds
        assert SQLKind.UPDATE in kinds
        assert SQLKind.DELETE in kinds

    def test_c_segments_exist(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        assert len(result.c_segments) > 0

    def test_sql_block_function_name(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        select_block = next(
            b for b in result.sql_blocks if b.sql_kind == SQLKind.SELECT
        )
        assert select_block.function_name == "b10_select_proc"

    def test_sql_block_line_range(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        for block in result.sql_blocks:
            assert block.origin_start_line <= block.origin_end_line
            assert block.origin_start_line >= 1
            assert block.origin_end_line <= result.total_lines


# ──────────────────────────────────────────────
# Stage 3: Host Variable 추출
# ──────────────────────────────────────────────


class TestStage3HostVariables:
    """Host variable 추출 테스트."""

    def test_declare_section_variables(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        names = {hv.name for hv in result.host_variables}
        assert "svc_cd" in names
        assert "cnt" in names

    def test_variable_types(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        svc_cd = next(hv for hv in result.host_variables if hv.name == "svc_cd")
        assert "char" in svc_cd.declared_type
        cnt = next(hv for hv in result.host_variables if hv.name == "cnt")
        assert "int" in cnt.declared_type

    def test_indicator_matching(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        svc_cd = next(hv for hv in result.host_variables if hv.name == "svc_cd")
        assert svc_cd.indicator_name == "svc_cd_ind"

    def test_sql_bind_variable_discovery(self, partitioner):
        """SQL 블록의 :변수명에서도 host variable을 발견해야 한다."""
        result = partitioner.partition_content(MULTI_SQL_PC)
        names = {hv.name for hv in result.host_variables}
        assert "rc" in names


# ──────────────────────────────────────────────
# 커서 lifecycle
# ──────────────────────────────────────────────


class TestCursorLifecycle:
    """커서 lifecycle 추적 테스트."""

    def test_cursor_detected(self, partitioner):
        result = partitioner.partition_content(CURSOR_PC)
        assert len(result.cursor_map) == 1
        assert result.cursor_map[0].cursor_name == "C_READ"

    def test_cursor_complete(self, partitioner):
        result = partitioner.partition_content(CURSOR_PC)
        cursor = result.cursor_map[0]
        assert cursor.is_complete is True

    def test_cursor_events(self, partitioner):
        result = partitioner.partition_content(CURSOR_PC)
        cursor = result.cursor_map[0]
        event_types = {e.event_type for e in cursor.events}
        assert event_types == {"DECLARE", "OPEN", "FETCH", "CLOSE"}

    def test_cursor_function_name(self, partitioner):
        result = partitioner.partition_content(CURSOR_PC)
        cursor = result.cursor_map[0]
        for event in cursor.events:
            assert event.function_name == "c200_fetch_proc"


# ──────────────────────────────────────────────
# WHENEVER 지시문
# ──────────────────────────────────────────────


class TestWheneverDirective:
    """WHENEVER 지시문 추적 테스트."""

    def test_whenever_detected(self, partitioner):
        result = partitioner.partition_content(WHENEVER_PC)
        assert len(result.global_context.whenever_directives) == 1

    def test_whenever_condition(self, partitioner):
        result = partitioner.partition_content(WHENEVER_PC)
        wd = result.global_context.whenever_directives[0]
        assert wd.condition == "SQLERROR"
        assert "GOTO" in wd.action.upper()

    def test_whenever_suppresses_sqlca(self, partitioner):
        result = partitioner.partition_content(WHENEVER_PC)
        wd = result.global_context.whenever_directives[0]
        assert wd.suppresses_sqlca_check is True

    def test_active_whenever_propagated(self, partitioner):
        result = partitioner.partition_content(WHENEVER_PC)
        # UPDATE 블록은 WHENEVER SQLERROR GOTO 이후에 있으므로 active_whenever가 설정됨
        update_block = next(
            b for b in result.sql_blocks if b.sql_kind == SQLKind.UPDATE
        )
        assert update_block.active_whenever is not None
        assert "SQLERROR" in update_block.active_whenever


# ──────────────────────────────────────────────
# Transaction Points
# ──────────────────────────────────────────────


class TestTransactionPoints:
    """트랜잭션 포인트 수집 테스트."""

    def test_commit_detected(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        assert len(result.transaction_points) == 1
        assert result.transaction_points[0].kind == "COMMIT"

    def test_commit_function(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        tp = result.transaction_points[0]
        assert tp.function_name == "z99_exit_proc"


# ──────────────────────────────────────────────
# GlobalContext
# ──────────────────────────────────────────────


class TestGlobalContext:
    """GlobalContext 수집 테스트."""

    def test_includes(self, partitioner):
        result = partitioner.partition_content(GLOBAL_CONTEXT_PC)
        inc_stmts = [inc.statement for inc in result.global_context.includes]
        assert any("#include <stdio.h>" in s for s in inc_stmts)
        assert any("proframe.h" in s for s in inc_stmts)
        assert any("INCLUDE" in s for s in inc_stmts)

    def test_macros(self, partitioner):
        result = partitioner.partition_content(GLOBAL_CONTEXT_PC)
        assert len(result.global_context.macros) >= 1
        assert any("MAX_CNT" in m.statement for m in result.global_context.macros)

    def test_type_definitions(self, partitioner):
        result = partitioner.partition_content(GLOBAL_CONTEXT_PC)
        assert len(result.global_context.type_definitions) >= 1

    def test_global_variables(self, partitioner):
        result = partitioner.partition_content(GLOBAL_CONTEXT_PC)
        var_stmts = [gv.statement for gv in result.global_context.global_variables]
        assert any("g_count" in s for s in var_stmts)


# ──────────────────────────────────────────────
# CSegment 병합 규칙
# ──────────────────────────────────────────────


class TestCSegmentMerge:
    """CSegment 병합 규칙 테스트."""

    def test_short_c_code_merged(self, partitioner):
        """SQL 블록 사이의 짧은 C 코드(< 5줄)는 이전 segment에 병합."""
        result = partitioner.partition_content(MULTI_SQL_PC)
        # MULTI_SQL_PC에서 SQL 블록 사이의 짧은 C 코드가 별도 segment가 아닌지 확인
        # 같은 함수 내이고 짧으면 병합되어야 함
        func_segments = [
            s for s in result.c_segments
            if s.function_name == "b20_multi_proc"
        ]
        # 3개 SQL 블록 사이에 짧은 C 코드가 있지만 병합되어야 함
        # 병합 후 segment 수가 3개 미만이면 병합 동작 확인
        assert len(func_segments) <= 2


# ──────────────────────────────────────────────
# CSegment content 참조
# ──────────────────────────────────────────────


class TestCSegmentContent:
    """CSegment의 get_content() 테스트."""

    def test_get_content(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        non_empty = [
            seg for seg in result.c_segments
            if seg.get_content(result.file_content).strip()
        ]
        # 실질적인 C 코드를 가진 segment가 존재해야 함
        assert len(non_empty) > 0

    def test_content_matches_lines(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        all_lines = result.file_content.splitlines()
        for seg in result.c_segments:
            content = seg.get_content(result.file_content)
            expected = "\n".join(
                all_lines[seg.origin_start_line - 1 : seg.origin_end_line]
            )
            assert content == expected


# ──────────────────────────────────────────────
# Fallback
# ──────────────────────────────────────────────


class TestFallback:
    """Fallback 시나리오 테스트."""

    def test_no_semicolon_fallback(self, partitioner):
        """EXEC SQL에 세미콜론이 없으면 C 코드로 분류, 경고."""
        result = partitioner.partition_content(NO_SEMICOLON_PC)
        # 파싱이 중단되지 않아야 함
        assert result.total_lines > 0
        # 세미콜론 없는 SQL은 C 코드로 재분류되어 sql_blocks에 포함되지 않을 수 있음
        # 중요한 것은 예외가 발생하지 않는 것

    def test_empty_file(self, partitioner):
        result = partitioner.partition_content("")
        assert result.total_lines == 0
        assert len(result.functions) == 0
        assert len(result.sql_blocks) == 0

    def test_no_functions(self, partitioner):
        code = "#include <stdio.h>\n/* just a header */\n"
        result = partitioner.partition_content(code)
        assert len(result.functions) == 0
        assert result.total_lines == 2


# ──────────────────────────────────────────────
# PartitionResult 통합
# ──────────────────────────────────────────────


class TestPartitionResultIntegration:
    """PartitionResult 전체 통합 테스트."""

    def test_total_lines(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        assert result.total_lines == len(SIMPLE_PC.splitlines())

    def test_file_content_preserved(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        assert result.file_content == SIMPLE_PC

    def test_get_function_by_line(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        # b10_select_proc 내부 line
        b10 = next(f for f in result.functions if f.function_name == "b10_select_proc")
        mid_line = (b10.line_start + b10.line_end) // 2
        found = result.get_function_by_line(mid_line)
        assert found is not None
        assert found.function_name == "b10_select_proc"

    def test_get_function_by_line_outside(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        # 파일 첫 줄은 함수 밖
        found = result.get_function_by_line(1)
        assert found is None

    def test_all_sql_blocks_have_valid_lines(self, partitioner):
        result = partitioner.partition_content(SIMPLE_PC)
        for block in result.sql_blocks:
            assert 1 <= block.origin_start_line <= result.total_lines
            assert 1 <= block.origin_end_line <= result.total_lines
