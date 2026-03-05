"""ExplainPlanParser 단위 테스트."""

import pytest

from mider.tools.base_tool import ToolExecutionError
from mider.tools.utility.explain_plan_parser import ExplainPlanParser


# DBMS_XPLAN.DISPLAY 표준 형식
_SAMPLE_XPLAN = """\
Plan hash value: 1234567890

---------------------------------------------------------------------------
| Id  | Operation          | Name   | Rows  | Bytes | Cost (%CPU)| Time     |
---------------------------------------------------------------------------
|   0 | SELECT STATEMENT   |        |    50 |  2400 |   234  (1) | 00:00:01 |
|   1 |  TABLE ACCESS FULL | ORDERS |    50 |  2400 |   234  (1) | 00:00:01 |
---------------------------------------------------------------------------
"""

_CARTESIAN_PLAN = """\
---------------------------------------------------------------------------
| Id  | Operation                    | Name   | Rows  | Bytes | Cost (%CPU)| Time     |
---------------------------------------------------------------------------
|   0 | SELECT STATEMENT             |        |  5000 | 50000 |  5000  (1) | 00:00:10 |
|   1 |  MERGE JOIN CARTESIAN        |        |  5000 | 50000 |  5000  (1) | 00:00:10 |
|   2 |   TABLE ACCESS FULL          | ORDERS |    50 |  2400 |   234  (1) | 00:00:01 |
|   3 |   TABLE ACCESS FULL          | ITEMS  |   100 |  3200 |  1200  (1) | 00:00:02 |
---------------------------------------------------------------------------
"""


class TestExplainPlanParser:
    def setup_method(self):
        self.parser = ExplainPlanParser()

    # ── 기본 동작 ────────────────────────────────

    def test_empty_file(self, tmp_path):
        """빈 파일은 빈 결과."""
        f = tmp_path / "empty.txt"
        f.write_text("")
        result = self.parser.execute(file=str(f))
        assert result.success is True
        assert result.data["steps"] == []
        assert result.data["tuning_points"] == []

    def test_missing_file_param(self):
        """file 파라미터 누락 시 ToolExecutionError."""
        with pytest.raises(ToolExecutionError):
            self.parser.execute()

    def test_nonexistent_file(self):
        """존재하지 않는 파일 시 ToolExecutionError."""
        with pytest.raises(ToolExecutionError):
            self.parser.execute(file="/nonexistent/plan.txt")

    # ── DBMS_XPLAN 표 형식 파싱 ──────────────────

    def test_parse_xplan_table_format(self, tmp_path):
        """DBMS_XPLAN 표 형식을 파싱한다."""
        f = tmp_path / "plan.txt"
        f.write_text(_SAMPLE_XPLAN)
        result = self.parser.execute(file=str(f))

        steps = result.data["steps"]
        assert len(steps) == 2
        assert steps[0]["id"] == 0
        assert steps[0]["operation"] == "SELECT STATEMENT"
        assert steps[1]["id"] == 1
        assert "TABLE ACCESS FULL" in steps[1]["operation"]

    def test_parse_cost_and_rows(self, tmp_path):
        """Cost, Rows, Bytes 값이 정수로 파싱된다."""
        f = tmp_path / "plan.txt"
        f.write_text(_SAMPLE_XPLAN)
        result = self.parser.execute(file=str(f))

        step1 = result.data["steps"][1]
        assert step1["cost"] == 234
        assert step1["rows"] == 50
        assert step1["bytes"] == 2400

    def test_raw_text_included(self, tmp_path):
        """raw_text에 원본 텍스트가 포함된다."""
        f = tmp_path / "plan.txt"
        f.write_text(_SAMPLE_XPLAN)
        result = self.parser.execute(file=str(f))
        assert "TABLE ACCESS FULL" in result.data["raw_text"]

    # ── 튜닝 포인트 탐지 ────────────────────────

    def test_detect_full_table_scan(self, tmp_path):
        """TABLE ACCESS FULL 튜닝 포인트 탐지."""
        f = tmp_path / "plan.txt"
        f.write_text(_SAMPLE_XPLAN)
        result = self.parser.execute(file=str(f))

        tps = result.data["tuning_points"]
        assert len(tps) >= 1
        fts_tp = [tp for tp in tps if "TABLE ACCESS FULL" in tp["operation"]]
        assert len(fts_tp) == 1
        assert fts_tp[0]["severity"] == "medium"

    def test_detect_cartesian_join(self, tmp_path):
        """Cartesian Join은 critical로 탐지."""
        f = tmp_path / "plan.txt"
        f.write_text(_CARTESIAN_PLAN)
        result = self.parser.execute(file=str(f))

        tps = result.data["tuning_points"]
        cart_tp = [tp for tp in tps if "CARTESIAN" in tp["operation"]]
        assert len(cart_tp) >= 1
        assert cart_tp[0]["severity"] == "critical"

    def test_high_cost_tuning_point(self, tmp_path):
        """Cost >= 1000인 단계가 튜닝 포인트로 탐지."""
        f = tmp_path / "plan.txt"
        f.write_text(_CARTESIAN_PLAN)
        result = self.parser.execute(file=str(f))

        tps = result.data["tuning_points"]
        # ITEMS 테이블의 Cost=1200은 TABLE ACCESS FULL로 이미 잡히지만
        # high cost 단계가 하나 이상 있어야 함
        assert any(tp.get("cost", 0) >= 1000 for tp in tps)

    def test_no_tuning_points_for_simple_plan(self, tmp_path):
        """단순 인덱스 스캔 플랜은 튜닝 포인트 없음."""
        simple_plan = """\
---------------------------------------------------------------------------
| Id  | Operation          | Name       | Rows  | Bytes | Cost (%CPU)| Time     |
---------------------------------------------------------------------------
|   0 | SELECT STATEMENT   |            |     1 |    48 |     2  (0) | 00:00:01 |
|   1 |  INDEX UNIQUE SCAN | PK_ORDERS  |     1 |    48 |     2  (0) | 00:00:01 |
---------------------------------------------------------------------------
"""
        f = tmp_path / "plan.txt"
        f.write_text(simple_plan)
        result = self.parser.execute(file=str(f))
        assert result.data["tuning_points"] == []

    # ── 일반 텍스트 형식 ─────────────────────────

    def test_plain_text_format(self, tmp_path):
        """표 형식이 아닌 텍스트에서도 오퍼레이션 추출."""
        plain = """\
Execution Plan
0  SELECT STATEMENT  cost=500 rows=1000
1  TABLE ACCESS FULL  ORDERS  cost=500 rows=1000
"""
        f = tmp_path / "plan.txt"
        f.write_text(plain)
        result = self.parser.execute(file=str(f))

        steps = result.data["steps"]
        assert len(steps) >= 1
