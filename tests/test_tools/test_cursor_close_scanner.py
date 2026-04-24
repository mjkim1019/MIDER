"""cursor_close_scanner 단위 테스트.

같은 함수 내 같은 cursor를 2회 이상 close하는 패턴을 탐지하는지 검증한다.
"""

from mider.tools.static_analysis.cursor_close_scanner import (
    scan_cursor_duplicate_close,
)


# ─ 단일 함수 안에 같은 커서 3회 close (실사례 재현) ─
TRIPLE_CLOSE_C = """\
static long
b200_suces_pen_mth(io_t *itf)
{
    long rc = RC_NRM;
    rc = mpfmdbio_copen_ar("zord_x_f0003", &in);
    for (;;) {
        rc = mpfmdbio_fetch_ar("zord_x_f0003", &out);
        if (rc == RC_NFD) {
            rc = mpfmdbio_cclose_ar("zord_x_f0003");
            break;
        }
        if (rc == RC_ERR) {
            rc = mpfmdbio_cclose_ar("zord_x_f0003");
            return RC_ERR;
        }
    }
    rc = mpfmdbio_cclose_ar("zord_x_f0003");
    return RC_NRM;
}
"""

# ─ 정상: 한 번만 close ─
SINGLE_CLOSE_C = """\
static long
process(io_t *itf)
{
    mpfmdbio_copen_ar("zord_y_f0001", &in);
    mpfmdbio_fetch_ar("zord_y_f0001", &out);
    mpfmdbio_cclose_ar("zord_y_f0001");
    return 0;
}
"""

# ─ 서로 다른 커서들 (각 1회) ─
MULTIPLE_CURSORS_SINGLE_CLOSE_C = """\
static long
process(io_t *itf)
{
    mpfmdbio_cclose_ar("cur_a");
    mpfmdbio_cclose_ar("cur_b");
    mpfmdbio_cclose_ar("cur_c");
    return 0;
}
"""

# ─ 서로 다른 함수에서 같은 커서 close (각 함수 내 1회) ─
TWO_FUNCTIONS_SAME_CURSOR = """\
static long
func_a(io_t *itf)
{
    mpfmdbio_cclose_ar("shared_cursor");
    return 0;
}

static long
func_b(io_t *itf)
{
    mpfmdbio_cclose_ar("shared_cursor");
    return 0;
}
"""

# ─ EXEC SQL 방식 ─
EXEC_SQL_DOUBLE_CLOSE_PC = """\
long
process()
{
    EXEC SQL OPEN my_cursor;
    EXEC SQL FETCH my_cursor INTO :x;
    EXEC SQL CLOSE my_cursor;
    EXEC SQL CLOSE my_cursor;
    return 0;
}
"""

# ─ 주석 안에 들어있는 close는 카운트 제외 ─
COMMENTED_CLOSE_C = """\
static long
process()
{
    mpfmdbio_cclose_ar("zord_z");
    /* mpfmdbio_cclose_ar("zord_z");  -- 예전 코드 */
    // mpfmdbio_cclose_ar("zord_z"); -- 주석 처리된 버전
    return 0;
}
"""


class TestCursorDuplicateClose:
    def test_detects_triple_close_same_function(self):
        """같은 함수에서 같은 커서를 3회 close하면 1건 finding."""
        findings = scan_cursor_duplicate_close(TRIPLE_CLOSE_C, language="c")
        assert len(findings) == 1
        f = findings[0]
        assert f["pattern_id"] == "CURSOR_DUPLICATE_CLOSE"
        assert f["variable"] == "zord_x_f0003"
        assert f["function"] == "b200_suces_pen_mth"
        assert f["severity"] == "high"
        assert len(f["all_lines"]) == 3

    def test_no_detect_single_close(self):
        findings = scan_cursor_duplicate_close(SINGLE_CLOSE_C, language="c")
        assert findings == []

    def test_no_detect_multiple_cursors_single_close_each(self):
        """서로 다른 커서를 각 1회 close하는 것은 정상."""
        findings = scan_cursor_duplicate_close(
            MULTIPLE_CURSORS_SINGLE_CLOSE_C, language="c",
        )
        assert findings == []

    def test_no_detect_same_cursor_across_functions(self):
        """서로 다른 함수에서 같은 이름 커서 close는 이슈 아님."""
        findings = scan_cursor_duplicate_close(
            TWO_FUNCTIONS_SAME_CURSOR, language="c",
        )
        assert findings == []

    def test_detects_exec_sql_double_close(self):
        findings = scan_cursor_duplicate_close(
            EXEC_SQL_DOUBLE_CLOSE_PC, language="proc",
        )
        assert len(findings) == 1
        assert findings[0]["variable"] == "my_cursor"

    def test_ignores_commented_close(self):
        """주석 내의 close 호출은 카운트하지 않는다."""
        findings = scan_cursor_duplicate_close(COMMENTED_CLOSE_C, language="c")
        assert findings == []
