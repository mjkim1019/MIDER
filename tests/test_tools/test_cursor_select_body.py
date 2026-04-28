"""Cursor select_body 추출 + INDICATOR 룰 강화 단위 테스트.

함수 분리 표준(DECLARE/PREPARE 함수 ≠ FETCH 함수)에서 cursor가 실행하는
SELECT 본문을 정확히 추적하는지, INDICATOR 룰이 SELECT의 NULL 보호를
인식해 false positive를 줄이는지 검증.
"""

from mider.tools.static_analysis.embedded_sql_analyzer import EmbeddedSQLStaticAnalyzer
from mider.tools.utility.proc_partitioner import ProCPartitioner


# 직접 SELECT를 가진 static cursor
STATIC_SELECT_CURSOR = """\
EXEC SQL BEGIN DECLARE SECTION;
    char gc_id[32];
EXEC SQL END DECLARE SECTION;

void f1() {
    EXEC SQL DECLARE C_static CURSOR FOR
        SELECT NVL(name, 'X'), NVL(addr, '') FROM tbl WHERE id = :gc_id;
    EXEC SQL OPEN C_static;
    EXEC SQL FETCH C_static INTO :gc_id;
    EXEC SQL CLOSE C_static;
}
"""

# Dynamic cursor — DECLARE/PREPARE 같은 함수
DYNAMIC_SAME_FUNCTION = """\
EXEC SQL BEGIN DECLARE SECTION;
    char gc_id[32];
    varchar ls_query[2000];
EXEC SQL END DECLARE SECTION;

void f1() {
    snprintf((char *)ls_query.arr, 2000,
        "SELECT NVL(name, 'X') AS nm, COALESCE(addr, '') AS addr "
        "FROM users WHERE id = :id");
    ls_query.len = strlen((char *)ls_query.arr);
    EXEC SQL PREPARE P_dyn FROM :ls_query;
    EXEC SQL DECLARE C_dyn CURSOR FOR P_dyn;
    EXEC SQL OPEN C_dyn USING :gc_id;
    EXEC SQL FETCH C_dyn INTO :gc_id;
    EXEC SQL CLOSE C_dyn;
}
"""

# Dynamic cursor — DECLARE/PREPARE 다른 함수 (함수 분리 표준)
DYNAMIC_SPLIT_FUNCTIONS = """\
EXEC SQL BEGIN DECLARE SECTION;
    char gc_id[32];
    varchar ls_query[2000];
EXEC SQL END DECLARE SECTION;

void prepare_proc() {
    snprintf((char *)ls_query.arr, 2000,
        "SELECT NVL(col_a, 0) AS a, NVL(col_b, '') AS b FROM t WHERE id = :id");
    EXEC SQL PREPARE P_split FROM :ls_query;
    EXEC SQL DECLARE C_split CURSOR FOR P_split;
    EXEC SQL OPEN C_split USING :gc_id;
}

void fetch_proc() {
    EXEC SQL FETCH C_split INTO :gc_id;
}

void close_proc() {
    EXEC SQL CLOSE C_split;
}
"""

# Dynamic cursor — SELECT 본문에 NULL 보호 없음 (정탐 케이스)
DYNAMIC_NO_NVL = """\
EXEC SQL BEGIN DECLARE SECTION;
    char gc_id[32];
    varchar ls_q[2000];
EXEC SQL END DECLARE SECTION;

void f1() {
    snprintf((char *)ls_q.arr, 2000,
        "SELECT col_a, col_b FROM t WHERE id = :id");
    EXEC SQL PREPARE P_naked FROM :ls_q;
    EXEC SQL DECLARE C_naked CURSOR FOR P_naked;
    EXEC SQL OPEN C_naked USING :gc_id;
    EXEC SQL FETCH C_naked INTO :gc_id;
    EXEC SQL CLOSE C_naked;
}
"""


def _partition(text: str):
    return ProCPartitioner().partition_content(text)


class TestSelectBodyExtraction:
    def test_static_cursor_extracts_select(self):
        result = _partition(STATIC_SELECT_CURSOR)
        cur = next(c for c in result.cursor_map if c.cursor_name == "C_static")
        assert cur.select_body is not None
        assert "SELECT" in cur.select_body.upper()
        assert "NVL" in cur.select_body.upper()

    def test_dynamic_same_function_extracts_select(self):
        result = _partition(DYNAMIC_SAME_FUNCTION)
        cur = next(c for c in result.cursor_map if c.cursor_name == "C_dyn")
        assert cur.select_body is not None
        assert "NVL" in cur.select_body.upper()
        assert "COALESCE" in cur.select_body.upper()
        assert cur.prepare_var == "ls_query"

    def test_dynamic_split_functions_extracts_select(self):
        """함수 분리 표준 — DECLARE는 prepare_proc, FETCH는 fetch_proc."""
        result = _partition(DYNAMIC_SPLIT_FUNCTIONS)
        cur = next(c for c in result.cursor_map if c.cursor_name == "C_split")
        # DECLARE/OPEN은 prepare_proc에서, FETCH는 fetch_proc에서, CLOSE는 close_proc에서
        event_funcs = {(e.event_type, e.function_name) for e in cur.events}
        assert ("DECLARE", "prepare_proc") in event_funcs
        assert ("FETCH", "fetch_proc") in event_funcs
        assert ("CLOSE", "close_proc") in event_funcs
        # SELECT 본문은 prepare_proc 함수에서 추적되어야 함
        assert cur.select_body is not None
        assert "NVL" in cur.select_body.upper()
        assert cur.select_origin_function == "prepare_proc"


class TestIndicatorRuleRespectsSelectBody:
    def _run_indicator(self, text: str):
        result = _partition(text)
        a = EmbeddedSQLStaticAnalyzer()
        findings = a.analyze(
            sql_blocks=result.sql_blocks,
            host_variables=result.host_variables,
            cursor_map=result.cursor_map,
            transaction_points=result.transaction_points,
            global_context=result.global_context,
        )
        return [f for f in findings if f.rule_id == "SQL_INDICATOR_MISSING"]

    def test_full_null_protection_suppresses_finding(self):
        """모든 INTO 변수에 대응할 만한 NULL 보호 → finding 생략.

        DYNAMIC_SAME_FUNCTION: FETCH INTO :gc_id (1개), SELECT에 NVL+COALESCE (2건)
        """
        findings = self._run_indicator(DYNAMIC_SAME_FUNCTION)
        # FETCH 1개 INTO var, SELECT에 NULL 보호 2건 → 모두 보호로 판정 → skip
        assert findings == []

    def test_split_function_full_protection_suppresses(self):
        """함수 분리에서도 SELECT 본문 추적되어 NULL 보호 인식 → finding 생략."""
        findings = self._run_indicator(DYNAMIC_SPLIT_FUNCTIONS)
        # FETCH 1개 INTO var, SELECT에 NVL 2건 → skip
        assert findings == []

    def test_no_null_protection_keeps_finding(self):
        """SELECT 본문에 NULL 보호 없으면 기존대로 finding 보고."""
        findings = self._run_indicator(DYNAMIC_NO_NVL)
        # FETCH에 INDICATOR 없음 + SELECT에 NVL 없음 → 정탐
        assert len(findings) == 1
        assert findings[0].severity == "medium"
