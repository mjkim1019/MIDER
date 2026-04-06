"""EmbeddedSQLStaticAnalyzer 단위 테스트.

8개 규칙을 각각 검증한다.
"""

from __future__ import annotations

import pytest

from mider.models.proc_partition import (
    CursorLifecycleEvent,
    CursorUnit,
    EmbeddedSQLUnit,
    GlobalContext,
    HostVarUnit,
    SQLKind,
    TransactionPoint,
)
from mider.tools.static_analysis.embedded_sql_analyzer import EmbeddedSQLStaticAnalyzer


@pytest.fixture
def analyzer() -> EmbeddedSQLStaticAnalyzer:
    return EmbeddedSQLStaticAnalyzer()


@pytest.fixture
def global_ctx() -> GlobalContext:
    return GlobalContext()


# ──────────────────────────────────────────
# 규칙 1: SQL_SQLCA_MISSING
# ──────────────────────────────────────────


class TestSqlcaMissing:
    """DML 후 SQLCA 체크 누락 규칙."""

    def _make_dml_block(
        self,
        kind: SQLKind = SQLKind.SELECT,
        has_check: bool = False,
        active_whenever: str | None = None,
    ) -> EmbeddedSQLUnit:
        return EmbeddedSQLUnit(
            block_id="sql_001",
            function_name="do_query",
            sql_kind=kind,
            raw_content="EXEC SQL SELECT ...",
            sql_text="SELECT col INTO :var FROM tab",
            origin_start_line=10,
            origin_end_line=12,
            line_count=3,
            has_sqlca_check=has_check,
            active_whenever=active_whenever,
        )

    def test_detects_missing_sqlca(self, analyzer, global_ctx):
        block = self._make_dml_block(SQLKind.INSERT)
        findings = analyzer.analyze([block], [], [], [], global_ctx)
        assert any(f.rule_id == "SQL_SQLCA_MISSING" for f in findings)

    def test_no_finding_when_sqlca_present(self, analyzer, global_ctx):
        block = self._make_dml_block(SQLKind.UPDATE, has_check=True)
        findings = analyzer.analyze([block], [], [], [], global_ctx)
        assert not any(f.rule_id == "SQL_SQLCA_MISSING" for f in findings)

    def test_whenever_exempts_sqlca(self, analyzer, global_ctx):
        block = self._make_dml_block(
            SQLKind.DELETE, active_whenever="WHENEVER SQLERROR GOTO err_handler",
        )
        findings = analyzer.analyze([block], [], [], [], global_ctx)
        assert not any(f.rule_id == "SQL_SQLCA_MISSING" for f in findings)

    def test_non_dml_skipped(self, analyzer, global_ctx):
        block = self._make_dml_block(SQLKind.CURSOR_DECLARE)
        findings = analyzer.analyze([block], [], [], [], global_ctx)
        assert not any(f.rule_id == "SQL_SQLCA_MISSING" for f in findings)


# ──────────────────────────────────────────
# 규칙 2: SQL_SELECT_INTO_MISMATCH
# ──────────────────────────────────────────


class TestSelectIntoMismatch:
    """SELECT INTO 컬럼/변수 수 불일치."""

    def test_detects_mismatch(self, analyzer, global_ctx):
        block = EmbeddedSQLUnit(
            block_id="sql_002",
            function_name="fn",
            sql_kind=SQLKind.SELECT,
            raw_content="EXEC SQL SELECT a, b, c INTO :x, :y FROM tab ;",
            sql_text="SELECT a, b, c INTO :x, :y FROM tab",
            origin_start_line=20,
            origin_end_line=22,
            line_count=3,
        )
        findings = analyzer.analyze([block], [], [], [], global_ctx)
        matched = [f for f in findings if f.rule_id == "SQL_SELECT_INTO_MISMATCH"]
        assert len(matched) == 1
        assert "3" in matched[0].title and "2" in matched[0].title

    def test_no_finding_when_matching(self, analyzer, global_ctx):
        block = EmbeddedSQLUnit(
            block_id="sql_003",
            function_name="fn",
            sql_kind=SQLKind.SELECT,
            raw_content="EXEC SQL SELECT a, b INTO :x, :y FROM tab ;",
            sql_text="SELECT a, b INTO :x, :y FROM tab",
            origin_start_line=20,
            origin_end_line=22,
            line_count=3,
        )
        findings = analyzer.analyze([block], [], [], [], global_ctx)
        assert not any(f.rule_id == "SQL_SELECT_INTO_MISMATCH" for f in findings)

    def test_subquery_column_count(self, analyzer, global_ctx):
        """서브쿼리/함수 호출 포함 시 괄호 안의 쉼표 무시."""
        block = EmbeddedSQLUnit(
            block_id="sql_004",
            function_name="fn",
            sql_kind=SQLKind.SELECT,
            raw_content="EXEC SQL SELECT NVL(a, 0), b INTO :x, :y FROM tab ;",
            sql_text="SELECT NVL(a, 0), b INTO :x, :y FROM tab",
            origin_start_line=30,
            origin_end_line=32,
            line_count=3,
        )
        findings = analyzer.analyze([block], [], [], [], global_ctx)
        assert not any(f.rule_id == "SQL_SELECT_INTO_MISMATCH" for f in findings)


# ──────────────────────────────────────────
# 규칙 3: SQL_HOST_VAR_COUNT
# ──────────────────────────────────────────


class TestHostVarCount:
    """INSERT VALUES 컬럼/변수 수 불일치."""

    def test_detects_insert_mismatch(self, analyzer, global_ctx):
        block = EmbeddedSQLUnit(
            block_id="sql_005",
            function_name="fn",
            sql_kind=SQLKind.INSERT,
            raw_content="EXEC SQL INSERT INTO tab (a, b, c) VALUES (:x, :y) ;",
            sql_text="INSERT INTO tab (a, b, c) VALUES (:x, :y)",
            origin_start_line=40,
            origin_end_line=42,
            line_count=3,
        )
        findings = analyzer.analyze([block], [], [], [], global_ctx)
        assert any(f.rule_id == "SQL_HOST_VAR_COUNT" for f in findings)

    def test_no_finding_when_matching(self, analyzer, global_ctx):
        block = EmbeddedSQLUnit(
            block_id="sql_006",
            function_name="fn",
            sql_kind=SQLKind.INSERT,
            raw_content="EXEC SQL INSERT INTO tab (a, b) VALUES (:x, :y) ;",
            sql_text="INSERT INTO tab (a, b) VALUES (:x, :y)",
            origin_start_line=50,
            origin_end_line=52,
            line_count=3,
        )
        findings = analyzer.analyze([block], [], [], [], global_ctx)
        assert not any(f.rule_id == "SQL_HOST_VAR_COUNT" for f in findings)


# ──────────────────────────────────────────
# 규칙 4: SQL_INDICATOR_MISSING
# ──────────────────────────────────────────


class TestIndicatorMissing:
    """SELECT/FETCH에서 indicator 변수 누락."""

    def test_detects_missing_indicator(self, analyzer, global_ctx):
        block = EmbeddedSQLUnit(
            block_id="sql_007",
            function_name="fn",
            sql_kind=SQLKind.SELECT,
            raw_content="EXEC SQL SELECT a INTO :x FROM tab ;",
            sql_text="SELECT a INTO :x FROM tab",
            origin_start_line=60,
            origin_end_line=62,
            line_count=3,
            host_variables=["x"],
            indicator_variables=[],
        )
        findings = analyzer.analyze([block], [], [], [], global_ctx)
        assert any(f.rule_id == "SQL_INDICATOR_MISSING" for f in findings)

    def test_nvl_exempts(self, analyzer, global_ctx):
        """NVL 사용 시 indicator 면제."""
        block = EmbeddedSQLUnit(
            block_id="sql_008",
            function_name="fn",
            sql_kind=SQLKind.SELECT,
            raw_content="EXEC SQL SELECT NVL(a, 0) INTO :x FROM tab ;",
            sql_text="SELECT NVL(a, 0) INTO :x FROM tab",
            origin_start_line=70,
            origin_end_line=72,
            line_count=3,
            host_variables=["x"],
            indicator_variables=[],
        )
        findings = analyzer.analyze([block], [], [], [], global_ctx)
        assert not any(f.rule_id == "SQL_INDICATOR_MISSING" for f in findings)

    def test_with_indicator_ok(self, analyzer, global_ctx):
        block = EmbeddedSQLUnit(
            block_id="sql_009",
            function_name="fn",
            sql_kind=SQLKind.SELECT,
            raw_content="EXEC SQL SELECT a INTO :x :x_ind FROM tab ;",
            sql_text="SELECT a INTO :x :x_ind FROM tab",
            origin_start_line=80,
            origin_end_line=82,
            line_count=3,
            host_variables=["x"],
            indicator_variables=["x_ind"],
        )
        findings = analyzer.analyze([block], [], [], [], global_ctx)
        assert not any(f.rule_id == "SQL_INDICATOR_MISSING" for f in findings)


# ──────────────────────────────────────────
# 규칙 5~7: 커서 lifecycle
# ──────────────────────────────────────────


class TestCursorLifecycleRules:
    """커서 lifecycle 검증 (OPEN/CLOSE/FETCH 누락)."""

    def _make_cursor(self, events: list[str]) -> CursorUnit:
        return CursorUnit(
            cursor_name="c_test",
            events=[
                CursorLifecycleEvent(event_type=e, line=i * 10, function_name="fn")
                for i, e in enumerate(events, 1)
            ],
        )

    def test_open_missing(self, analyzer, global_ctx):
        cursor = self._make_cursor(["DECLARE"])
        findings = analyzer.analyze([], [], [cursor], [], global_ctx)
        assert any(f.rule_id == "SQL_CURSOR_OPEN_MISSING" for f in findings)

    def test_close_missing(self, analyzer, global_ctx):
        cursor = self._make_cursor(["DECLARE", "OPEN", "FETCH"])
        findings = analyzer.analyze([], [], [cursor], [], global_ctx)
        assert any(f.rule_id == "SQL_CURSOR_CLOSE_MISSING" for f in findings)

    def test_fetch_missing(self, analyzer, global_ctx):
        cursor = self._make_cursor(["DECLARE", "OPEN", "CLOSE"])
        findings = analyzer.analyze([], [], [cursor], [], global_ctx)
        assert any(f.rule_id == "SQL_CURSOR_FETCH_MISSING" for f in findings)

    def test_complete_lifecycle_no_finding(self, analyzer, global_ctx):
        cursor = self._make_cursor(["DECLARE", "OPEN", "FETCH", "CLOSE"])
        findings = analyzer.analyze([], [], [cursor], [], global_ctx)
        cursor_rules = {"SQL_CURSOR_OPEN_MISSING", "SQL_CURSOR_CLOSE_MISSING", "SQL_CURSOR_FETCH_MISSING"}
        assert not any(f.rule_id in cursor_rules for f in findings)


# ──────────────────────────────────────────
# 규칙 8: SQL_COMMIT_MISSING
# ──────────────────────────────────────────


class TestCommitMissing:
    """DML 존재하나 COMMIT/ROLLBACK 없음."""

    def test_detects_missing_commit(self, analyzer, global_ctx):
        block = EmbeddedSQLUnit(
            block_id="sql_010",
            function_name="fn",
            sql_kind=SQLKind.INSERT,
            raw_content="EXEC SQL INSERT INTO tab VALUES (:x) ;",
            sql_text="INSERT INTO tab VALUES (:x)",
            origin_start_line=90,
            origin_end_line=92,
            line_count=3,
            has_sqlca_check=True,
        )
        findings = analyzer.analyze([block], [], [], [], global_ctx)
        assert any(f.rule_id == "SQL_COMMIT_MISSING" for f in findings)

    def test_no_finding_with_commit(self, analyzer, global_ctx):
        block = EmbeddedSQLUnit(
            block_id="sql_011",
            function_name="fn",
            sql_kind=SQLKind.UPDATE,
            raw_content="EXEC SQL UPDATE tab SET a = :x ;",
            sql_text="UPDATE tab SET a = :x",
            origin_start_line=100,
            origin_end_line=102,
            line_count=3,
            has_sqlca_check=True,
        )
        tp = TransactionPoint(kind="COMMIT", function_name="fn", line=110)
        findings = analyzer.analyze([block], [], [], [tp], global_ctx)
        assert not any(f.rule_id == "SQL_COMMIT_MISSING" for f in findings)

    def test_no_dml_no_finding(self, analyzer, global_ctx):
        block = EmbeddedSQLUnit(
            block_id="sql_012",
            function_name="fn",
            sql_kind=SQLKind.CURSOR_DECLARE,
            raw_content="EXEC SQL DECLARE c CURSOR FOR ...",
            sql_text="DECLARE c CURSOR FOR SELECT ...",
            origin_start_line=120,
            origin_end_line=122,
            line_count=3,
        )
        findings = analyzer.analyze([block], [], [], [], global_ctx)
        assert not any(f.rule_id == "SQL_COMMIT_MISSING" for f in findings)


# ──────────────────────────────────────────
# Finding 형식 검증
# ──────────────────────────────────────────


class TestFindingFormat:
    """Finding ID 형식과 메타데이터."""

    def test_finding_id_format(self, analyzer, global_ctx):
        block = EmbeddedSQLUnit(
            block_id="sql_013",
            function_name="fn",
            sql_kind=SQLKind.DELETE,
            raw_content="EXEC SQL DELETE FROM tab ;",
            sql_text="DELETE FROM tab",
            origin_start_line=130,
            origin_end_line=130,
            line_count=1,
        )
        findings = analyzer.analyze([block], [], [], [], global_ctx)
        assert len(findings) > 0
        for f in findings:
            assert f.finding_id.startswith("SF-")
            assert f.source_layer == "static"
            assert f.tool == "embedded_sql_static"

    def test_counter_resets_per_analyze(self, analyzer, global_ctx):
        """analyze 호출마다 카운터 리셋."""
        block = EmbeddedSQLUnit(
            block_id="sql_014",
            function_name="fn",
            sql_kind=SQLKind.INSERT,
            raw_content="EXEC SQL INSERT INTO tab VALUES (:x) ;",
            sql_text="INSERT INTO tab VALUES (:x)",
            origin_start_line=140,
            origin_end_line=142,
            line_count=3,
        )
        findings1 = analyzer.analyze([block], [], [], [], global_ctx)
        findings2 = analyzer.analyze([block], [], [], [], global_ctx)
        # 둘 다 SF-001부터 시작해야 함
        if findings1 and findings2:
            assert findings1[0].finding_id == findings2[0].finding_id
