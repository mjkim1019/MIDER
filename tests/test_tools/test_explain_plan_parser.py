"""ExplainPlanParser 단위 테스트."""

from pathlib import Path

import pytest

from mider.tools.base_tool import ToolExecutionError
from mider.tools.utility.explain_plan_parser import ExplainPlanParser


# ── 샘플 데이터 ───────────────────────────────

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

# 텍스트 덤프 형식 (줄 단위 컬럼 나열, 헤더 + Operation 데이터)
_TEXT_DUMP_SIMPLE = """\
Operation

Object Instance

Access Pred

Filter Pred

SELECT STATEMENT (Cost=500 Card=10 Bytes=480)

TABLE ACCESS (FULL) OF 'MYSCHEMA.ORDERS' (TABLE) (Cost=500 Card=10 Bytes=480)

1

"O"."STATUS"='ACTIVE'

INDEX (RANGE SCAN) OF 'ORDERS_IDX1.ORDERS_IDX1' (INDEX) (Cost=2 Card=5)

"O"."ORDER_DATE">:B1

NESTED LOOPS (Cost=50 Card=3 Bytes=120)

SORT (ORDER BY) (Cost=100 Card=10 Bytes=480)
"""

# 텍스트 덤프: Filter Pred 헤더가 있지만 Operation으로 오인하면 안 됨
_TEXT_DUMP_WITH_FILTER_PRED_HEADER = """\
Operation

Object Instance

Object Node

IN-OUT

PQ Distribution

Part-Start

Part-Stop

Access Pred

Filter Pred

SELECT STATEMENT (Cost=100 Card=5 Bytes=200)

FILTER

:B1='Y'

TABLE ACCESS (BY INDEX ROWID) OF 'HR.EMPLOYEES' (TABLE) (Cost=3 Card=1 Bytes=40)

INDEX (UNIQUE SCAN) OF 'EMP_PK.EMP_PK' (INDEX (UNIQUE)) (Cost=1 Card=1)

"E"."EMPLOYEE_ID"=:B1
"""

# DBMS_XPLAN이 아닌 일반 텍스트 (표 구분선 없음, Access Pred 없음)
_PLAIN_TEXT = """\
Execution Plan
0  SELECT STATEMENT  cost=500 rows=1000
1  TABLE ACCESS FULL  ORDERS  cost=500 rows=1000
"""


# ── 헬퍼 ──────────────────────────────────────

_SAMPLE_EXPLAIN_PLAN_PATH = Path(__file__).parent.parent / "fixtures" / "t18_sql" / "sample_explain_plan.txt"


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
        f = tmp_path / "plan.txt"
        f.write_text(_PLAIN_TEXT)
        result = self.parser.execute(file=str(f))

        steps = result.data["steps"]
        assert len(steps) >= 1


class TestIsTextDump:
    """_is_text_dump() 텍스트 덤프 형식 감지 테스트."""

    def setup_method(self):
        self.parser = ExplainPlanParser()

    def test_detect_text_dump_with_access_pred(self):
        """Operation + Access Pred 헤더가 있으면 텍스트 덤프로 감지한다."""
        content = "Operation\n\nObject Instance\n\nAccess Pred\n\nSELECT STATEMENT"
        assert self.parser._is_text_dump(content) is True

    def test_detect_text_dump_with_filter_pred(self):
        """Operation + Filter Pred 헤더가 있으면 텍스트 덤프로 감지한다."""
        content = "Operation\n\nFilter Pred\n\nSELECT STATEMENT (Cost=100)"
        assert self.parser._is_text_dump(content) is True

    def test_detect_text_dump_with_both_preds(self):
        """Operation + Access Pred + Filter Pred 모두 있으면 텍스트 덤프."""
        assert self.parser._is_text_dump(_TEXT_DUMP_SIMPLE) is True

    def test_reject_xplan_table_format(self):
        """DBMS_XPLAN 표 형식은 텍스트 덤프가 아니다."""
        assert self.parser._is_text_dump(_SAMPLE_XPLAN) is False

    def test_reject_plain_text_format(self):
        """단순 텍스트 형식은 텍스트 덤프가 아니다 (Access/Filter Pred 없음)."""
        assert self.parser._is_text_dump(_PLAIN_TEXT) is False

    def test_case_insensitive_detection(self):
        """대소문자 구분 없이 감지한다."""
        content = "OPERATION\n\nACCESS PRED\n\nSELECT STATEMENT"
        assert self.parser._is_text_dump(content) is True

    def test_partial_header_not_enough(self):
        """Operation만 있고 Access/Filter Pred가 없으면 텍스트 덤프가 아니다."""
        content = "Operation\n\nSELECT STATEMENT (Cost=100)"
        assert self.parser._is_text_dump(content) is False


class TestIsOperationLine:
    """_is_operation_line() Operation 라인 판별 테스트."""

    def setup_method(self):
        self.parser = ExplainPlanParser()

    def test_select_statement(self):
        """SELECT STATEMENT은 Operation 라인이다."""
        assert self.parser._is_operation_line("SELECT STATEMENT (Cost=100)") is True

    def test_table_access_full(self):
        """TABLE ACCESS (FULL)은 Operation 라인이다."""
        assert self.parser._is_operation_line("TABLE ACCESS (FULL) OF 'T1' (TABLE)") is True

    def test_index_range_scan(self):
        """INDEX (RANGE SCAN)은 Operation 라인이다."""
        line = "INDEX (RANGE SCAN) OF 'IDX1.IDX1' (INDEX) (Cost=1 Card=1)"
        assert self.parser._is_operation_line(line) is True

    def test_nested_loops(self):
        """NESTED LOOPS는 Operation 라인이다."""
        assert self.parser._is_operation_line("NESTED LOOPS (Cost=50)") is True

    def test_sort_aggregate(self):
        """SORT (AGGREGATE)은 Operation 라인이다."""
        assert self.parser._is_operation_line("SORT (AGGREGATE)") is True

    def test_filter_operation(self):
        """FILTER는 Operation 라인이다 (헤더 필드 아님)."""
        assert self.parser._is_operation_line("FILTER") is True

    def test_reject_filter_pred_header(self):
        """Filter Pred 헤더 필드는 Operation이 아니다."""
        assert self.parser._is_operation_line("Filter Pred") is False

    def test_reject_filter_pred_uppercase(self):
        """FILTER PRED (대문자)도 Operation이 아니다."""
        assert self.parser._is_operation_line("FILTER PRED") is False

    def test_reject_predicate_line(self):
        """Predicate 조건 라인은 Operation이 아니다."""
        assert self.parser._is_operation_line('"T"."ID"=:B1') is False

    def test_reject_object_instance_number(self):
        """Object Instance 번호(예: 76)는 Operation이 아니다."""
        assert self.parser._is_operation_line("76") is False

    def test_hash_join(self):
        """HASH JOIN은 Operation 라인이다."""
        assert self.parser._is_operation_line("HASH JOIN (Cost=200)") is True

    def test_partition_range(self):
        """PARTITION RANGE은 Operation 라인이다."""
        assert self.parser._is_operation_line("PARTITION RANGE (ALL) (Cost=153)") is True

    def test_view(self):
        """VIEW는 Operation 라인이다."""
        assert self.parser._is_operation_line("VIEW (Cost=100 Card=5)") is True

    def test_count_stopkey(self):
        """COUNT (STOPKEY)는 Operation 라인이다."""
        assert self.parser._is_operation_line("COUNT (STOPKEY)") is True

    def test_window_sort(self):
        """WINDOW (SORT)는 Operation 라인이다."""
        assert self.parser._is_operation_line("WINDOW (SORT) (Cost=1608)") is True

    def test_bitmap_operation(self):
        """BITMAP은 Operation 라인이다."""
        assert self.parser._is_operation_line("BITMAP INDEX SINGLE VALUE") is True


class TestParseOperationDetail:
    """_parse_operation_detail() Operation 라인 상세 파싱 테스트."""

    def setup_method(self):
        self.parser = ExplainPlanParser()

    def test_normalize_parentheses_table_access_full(self):
        """'TABLE ACCESS (FULL)' 괄호를 정규화 → 'TABLE ACCESS FULL'."""
        step = self.parser._parse_operation_detail(
            "TABLE ACCESS (FULL) OF 'MYSCHEMA.ORDERS' (TABLE) (Cost=500 Card=10 Bytes=480)",
            step_id=0,
        )
        # OF 이전 부분만 operation에 포함되고 괄호가 정규화됨
        assert step["operation"] == "TABLE ACCESS FULL"
        # 괄호 문자가 operation 문자열에 남아있지 않아야 함
        assert "(" not in step["operation"]
        assert ")" not in step["operation"]

    def test_normalize_parentheses_index_range_scan(self):
        """'INDEX (RANGE SCAN)' 괄호를 정규화 → 'INDEX RANGE SCAN'."""
        step = self.parser._parse_operation_detail(
            "INDEX (RANGE SCAN) OF 'IDX1.IDX1' (INDEX) (Cost=1 Card=1)",
            step_id=1,
        )
        assert "INDEX RANGE SCAN" in step["operation"]
        assert "(" not in step["operation"]

    def test_extract_object_name_with_schema(self):
        """OF 'SCHEMA.TABLE'에서 테이블명만 추출한다."""
        step = self.parser._parse_operation_detail(
            "TABLE ACCESS (BY INDEX ROWID) OF 'HR.EMPLOYEES' (TABLE) (Cost=3 Card=1)",
            step_id=0,
        )
        assert step["name"] == "EMPLOYEES"

    def test_extract_object_name_without_schema(self):
        """OF 'TABLE'에서 스키마 없이도 테이블명을 추출한다."""
        step = self.parser._parse_operation_detail(
            "TABLE ACCESS (FULL) OF 'ORDERS' (TABLE) (Cost=100)",
            step_id=0,
        )
        assert step["name"] == "ORDERS"

    def test_extract_cost_card_bytes(self):
        """Cost, Card(=rows), Bytes를 정수로 추출한다."""
        step = self.parser._parse_operation_detail(
            "SELECT STATEMENT (Cost=1608 Card=15 Bytes=8220)",
            step_id=0,
        )
        assert step["cost"] == 1608
        assert step["rows"] == 15
        assert step["bytes"] == 8220

    def test_partial_cost_only(self):
        """Cost만 있고 Card/Bytes가 없는 경우."""
        step = self.parser._parse_operation_detail(
            "SORT (AGGREGATE) (Cost=50)",
            step_id=0,
        )
        assert step["cost"] == 50
        assert "rows" not in step
        assert "bytes" not in step

    def test_no_cost_info(self):
        """Cost 정보가 없는 Operation (예: FILTER)."""
        step = self.parser._parse_operation_detail("FILTER", step_id=0)
        assert step["operation"] == "FILTER"
        assert "cost" not in step

    def test_step_id_assignment(self):
        """step_id가 올바르게 할당된다."""
        step = self.parser._parse_operation_detail(
            "SELECT STATEMENT (Cost=100)", step_id=42
        )
        assert step["id"] == 42

    def test_index_with_schema_dot_name(self):
        """인덱스 OF 'SCHEMA.INDEX_NAME'에서 인덱스명만 추출."""
        step = self.parser._parse_operation_detail(
            "INDEX (RANGE SCAN) OF 'ZORD_SVC_PROD_GRP_MEMB_N2.ZORD_SVC_PROD_GRP_MEMB_N2' (INDEX) (Cost=1 Card=1)",
            step_id=0,
        )
        assert step["name"] == "ZORD_SVC_PROD_GRP_MEMB_N2"

    def test_nested_loops_outer(self):
        """NESTED LOOPS (OUTER) 괄호 정규화."""
        step = self.parser._parse_operation_detail(
            "NESTED LOOPS (OUTER) (Cost=1606 Card=15 Bytes=8220)",
            step_id=0,
        )
        assert "NESTED LOOP" in step["operation"]
        assert step["cost"] == 1606


class TestParseTextDump:
    """_parse_text_dump() 텍스트 덤프 파싱 테스트."""

    def setup_method(self):
        self.parser = ExplainPlanParser()

    def test_extract_operations(self):
        """텍스트 덤프에서 Operation 단계를 추출한다."""
        steps = self.parser._parse_text_dump(_TEXT_DUMP_SIMPLE)
        assert len(steps) >= 4  # SELECT STATEMENT, TABLE ACCESS, INDEX, NESTED LOOPS ...
        operations = [s["operation"] for s in steps]
        assert any("SELECT STATEMENT" in op for op in operations)
        assert any("TABLE ACCESS" in op for op in operations)
        assert any("INDEX" in op for op in operations)

    def test_extract_object_names(self):
        """텍스트 덤프에서 OF 절의 객체명을 추출한다."""
        steps = self.parser._parse_text_dump(_TEXT_DUMP_SIMPLE)
        named_steps = [s for s in steps if "name" in s]
        assert len(named_steps) >= 1
        # MYSCHEMA.ORDERS → ORDERS
        assert any(s["name"] == "ORDERS" for s in named_steps)

    def test_extract_predicates(self):
        """텍스트 덤프에서 Predicate 라인을 Operation에 연결한다."""
        steps = self.parser._parse_text_dump(_TEXT_DUMP_SIMPLE)
        pred_steps = [s for s in steps if s.get("predicates")]
        assert len(pred_steps) >= 1
        # TABLE ACCESS의 predicate에 STATUS 조건 포함
        all_preds = []
        for s in pred_steps:
            all_preds.extend(s["predicates"])
        assert any("STATUS" in p for p in all_preds)

    def test_step_ids_sequential(self):
        """step_id가 0부터 순차적으로 부여된다."""
        steps = self.parser._parse_text_dump(_TEXT_DUMP_SIMPLE)
        ids = [s["id"] for s in steps]
        assert ids == list(range(len(steps)))

    def test_cost_extracted_from_text_dump(self):
        """텍스트 덤프 Operation에서 Cost 값을 추출한다."""
        steps = self.parser._parse_text_dump(_TEXT_DUMP_SIMPLE)
        # SELECT STATEMENT (Cost=500)
        select_step = [s for s in steps if "SELECT STATEMENT" in s.get("operation", "")]
        assert len(select_step) >= 1
        assert select_step[0]["cost"] == 500

    def test_filter_pred_header_not_parsed_as_operation(self):
        """헤더의 'Filter Pred'가 FILTER Operation으로 오인되지 않는다."""
        steps = self.parser._parse_text_dump(_TEXT_DUMP_WITH_FILTER_PRED_HEADER)
        # 첫 번째 Operation은 SELECT STATEMENT여야 함 (Filter Pred 헤더 아님)
        assert "SELECT STATEMENT" in steps[0]["operation"]

    def test_object_instance_number_ignored(self):
        """Object Instance 번호(숫자만 있는 줄)는 step이 아닌 메타데이터로 처리된다."""
        steps = self.parser._parse_text_dump(_TEXT_DUMP_SIMPLE)
        # "1" (Object Instance)이 별도 Operation으로 파싱되면 안 됨
        for s in steps:
            assert s["operation"] != "1"

    def test_filter_operation_with_predicate(self):
        """FILTER Operation 뒤의 Predicate가 올바르게 연결된다."""
        steps = self.parser._parse_text_dump(_TEXT_DUMP_WITH_FILTER_PRED_HEADER)
        filter_steps = [s for s in steps if s.get("operation", "").strip() == "FILTER"]
        assert len(filter_steps) >= 1
        # FILTER 뒤에 :B1='Y' predicate가 연결되어야 함
        assert any(
            any(":B1" in p for p in s.get("predicates", []))
            for s in filter_steps
        )


class TestFormatAsXplanTable:
    """_format_as_xplan_table() 테이블 형식 출력 테스트."""

    def setup_method(self):
        self.parser = ExplainPlanParser()

    def test_empty_steps_returns_empty(self):
        """빈 steps 리스트는 빈 문자열을 반환한다."""
        result = self.parser._format_as_xplan_table([])
        assert result == ""

    def test_header_row_present(self):
        """출력에 헤더 행이 포함된다."""
        steps = [{"id": 0, "operation": "SELECT STATEMENT", "cost": 100}]
        result = self.parser._format_as_xplan_table(steps)
        lines = result.split("\n")
        assert "Id" in lines[0]
        assert "Operation" in lines[0]
        assert "Object" in lines[0]
        assert "Cost" in lines[0]
        assert "Rows" in lines[0]

    def test_separator_row_present(self):
        """헤더 아래 구분선이 포함된다."""
        steps = [{"id": 0, "operation": "SELECT STATEMENT"}]
        result = self.parser._format_as_xplan_table(steps)
        lines = result.split("\n")
        assert "---" in lines[1]

    def test_data_rows_present(self):
        """데이터 행이 올바르게 출력된다."""
        steps = [
            {"id": 0, "operation": "SELECT STATEMENT", "cost": 100, "rows": 50},
            {"id": 1, "operation": "TABLE ACCESS FULL", "name": "ORDERS", "cost": 100, "rows": 50},
        ]
        result = self.parser._format_as_xplan_table(steps)
        assert "SELECT STATEMENT" in result
        assert "TABLE ACCESS FULL" in result
        assert "ORDERS" in result

    def test_predicate_section_present(self):
        """Predicate가 있는 step은 Predicate Information 섹션에 출력된다."""
        steps = [
            {
                "id": 0,
                "operation": "TABLE ACCESS FULL",
                "name": "ORDERS",
                "predicates": ['"O"."STATUS"=\'ACTIVE\''],
            },
        ]
        result = self.parser._format_as_xplan_table(steps)
        assert "Predicate Information:" in result
        assert "STATUS" in result

    def test_no_predicate_section_when_empty(self):
        """Predicate가 없으면 Predicate Information 섹션이 없다."""
        steps = [{"id": 0, "operation": "SELECT STATEMENT"}]
        result = self.parser._format_as_xplan_table(steps)
        assert "Predicate Information:" not in result

    def test_predicate_truncation(self):
        """200자 초과 Predicate는 잘린다."""
        long_pred = "A" * 250
        steps = [
            {
                "id": 0,
                "operation": "FILTER",
                "predicates": [long_pred],
            },
        ]
        result = self.parser._format_as_xplan_table(steps)
        assert "..." in result
        # 원본 250자가 아닌 200자+...로 잘림
        pred_section = result.split("Predicate Information:")[1]
        # 잘린 predicate 라인에 원본 전체가 포함되면 안 됨
        assert long_pred not in pred_section

    def test_multiple_predicates_per_step(self):
        """하나의 step에 여러 Predicate가 있으면 모두 출력된다."""
        steps = [
            {
                "id": 1,
                "operation": "INDEX RANGE SCAN",
                "predicates": ['"T"."ID"=:B1', '"T"."STATUS"=\'Y\''],
            },
        ]
        result = self.parser._format_as_xplan_table(steps)
        assert '"T"."ID"=:B1' in result
        assert "STATUS" in result


class TestTextDumpIntegration:
    """sample_explain_plan.txt 통합 테스트."""

    def setup_method(self):
        self.parser = ExplainPlanParser()

    @pytest.mark.skipif(
        not _SAMPLE_EXPLAIN_PLAN_PATH.exists(),
        reason="sample_explain_plan.txt 파일 없음",
    )
    def test_sample_explain_plan_parses_steps(self):
        """실제 sample_explain_plan.txt에서 step이 1개 이상 추출된다."""
        result = self.parser.execute(file=str(_SAMPLE_EXPLAIN_PLAN_PATH))
        assert result.success is True
        steps = result.data["steps"]
        assert len(steps) > 0, "파싱된 step이 없습니다"

    @pytest.mark.skipif(
        not _SAMPLE_EXPLAIN_PLAN_PATH.exists(),
        reason="sample_explain_plan.txt 파일 없음",
    )
    def test_sample_explain_plan_detects_tuning_points(self):
        """실제 sample_explain_plan.txt에서 tuning_point가 1개 이상 탐지된다."""
        result = self.parser.execute(file=str(_SAMPLE_EXPLAIN_PLAN_PATH))
        tps = result.data["tuning_points"]
        assert len(tps) > 0, "탐지된 튜닝 포인트가 없습니다"

    @pytest.mark.skipif(
        not _SAMPLE_EXPLAIN_PLAN_PATH.exists(),
        reason="sample_explain_plan.txt 파일 없음",
    )
    def test_sample_explain_plan_is_text_dump(self):
        """sample_explain_plan.txt는 텍스트 덤프 형식으로 감지된다."""
        content = _SAMPLE_EXPLAIN_PLAN_PATH.read_text(encoding="utf-8")
        assert self.parser._is_text_dump(content) is True

    @pytest.mark.skipif(
        not _SAMPLE_EXPLAIN_PLAN_PATH.exists(),
        reason="sample_explain_plan.txt 파일 없음",
    )
    def test_sample_explain_plan_formatted_table_nonempty(self):
        """실제 파일 파싱 결과의 formatted_table이 비어있지 않다."""
        result = self.parser.execute(file=str(_SAMPLE_EXPLAIN_PLAN_PATH))
        table = result.data["formatted_table"]
        assert len(table) > 0
        assert "Id" in table
        assert "Operation" in table

    @pytest.mark.skipif(
        not _SAMPLE_EXPLAIN_PLAN_PATH.exists(),
        reason="sample_explain_plan.txt 파일 없음",
    )
    def test_sample_explain_plan_has_sort_order_by(self):
        """sample_explain_plan.txt에 SORT ORDER BY 단계가 포함된다."""
        result = self.parser.execute(file=str(_SAMPLE_EXPLAIN_PLAN_PATH))
        steps = result.data["steps"]
        sort_steps = [s for s in steps if "SORT" in s.get("operation", "") and "ORDER BY" in s.get("operation", "")]
        assert len(sort_steps) >= 1

    @pytest.mark.skipif(
        not _SAMPLE_EXPLAIN_PLAN_PATH.exists(),
        reason="sample_explain_plan.txt 파일 없음",
    )
    def test_sample_explain_plan_cost_values_are_int(self):
        """sample_explain_plan.txt 파싱 결과에서 cost 값은 정수형이다."""
        result = self.parser.execute(file=str(_SAMPLE_EXPLAIN_PLAN_PATH))
        steps = result.data["steps"]
        cost_steps = [s for s in steps if "cost" in s]
        assert len(cost_steps) > 0
        for s in cost_steps:
            assert isinstance(s["cost"], int), f"cost가 정수가 아님: {s}"
