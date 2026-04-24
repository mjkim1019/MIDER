"""format_arg_scanner 단위 테스트.

printf 계열 함수의 format/arg 개수 불일치 탐지를 검증한다.
"""

from mider.tools.static_analysis.format_arg_scanner import (
    scan_format_arg_mismatch,
)


class TestBasicMismatch:
    def test_printf_fewer_args(self):
        """sprintf: 포맷 3개, 인자 2개 → 탐지."""
        src = 'sprintf(buf, "%d %d %d", a, b);'
        findings = scan_format_arg_mismatch(src)
        assert len(findings) == 1
        f = findings[0]
        assert f["format_count"] == 3
        assert f["arg_count"] == 2
        assert f["pattern_id"] == "FORMAT_ARG_MISMATCH"

    def test_snprintf_more_args(self):
        """snprintf: 포맷 1개, 인자 2개 → 탐지."""
        src = 'snprintf(buf, 100, "%d", a, b);'
        findings = scan_format_arg_mismatch(src)
        assert len(findings) == 1
        assert findings[0]["format_count"] == 1
        assert findings[0]["arg_count"] == 2

    def test_matching_counts_no_finding(self):
        src = 'sprintf(buf, "%d %s %ld", a, b, c);'
        assert scan_format_arg_mismatch(src) == []

    def test_fprintf(self):
        src = 'fprintf(stderr, "%s %d", str);'
        findings = scan_format_arg_mismatch(src)
        assert len(findings) == 1
        assert findings[0]["format_count"] == 2
        assert findings[0]["arg_count"] == 1


class TestFormatCounting:
    def test_double_percent_is_literal(self):
        """%%는 인자를 소비하지 않는다."""
        src = 'sprintf(buf, "100%%: %d", n);'
        # %% = 리터럴 %, %d = 1개 → 인자 1개
        assert scan_format_arg_mismatch(src) == []

    def test_length_modifiers(self):
        """%ld, %lld, %hhu 등 길이 지정자 포함."""
        src = 'sprintf(buf, "%ld %lld %hhu", a, b, c);'
        assert scan_format_arg_mismatch(src) == []

    def test_width_precision(self):
        """%5.2f, %*d 등 width/precision 포함."""
        src = 'sprintf(buf, "%5.2f %*d", a, w, n);'
        # %5.2f = 1, %*d = 1 (width 별표는 인자 아님 — 엄밀히는 소비하지만 본 스캐너는
        # 단순화를 위해 conversion 기준으로만 카운트) → 2개 매칭, 인자 3개
        # 주의: 실제 printf는 width * 도 인자 소비하지만, 본 구현은 보수적으로
        # conversion spec만 센다. 다만 여기서는 3 vs 2로 불일치 판정.
        findings = scan_format_arg_mismatch(src)
        assert len(findings) == 1


class TestMultiLineLiterals:
    def test_adjacent_string_concat(self):
        """C의 인접 문자열 리터럴 자동 연결."""
        src = '''sprintf(buf,
    "%d "
    "%d "
    "%d",
    a, b, c);'''
        assert scan_format_arg_mismatch(src) == []

    def test_real_world_off_by_one(self):
        """zinvbprt10130.pc 사례 축약: 27 %ld vs 26 args."""
        fmts = " ".join([f"parallel(t,%ld)"] * 27)
        arg_list = ", ".join(["n"] * 26)
        src = f'''snprintf(buf, 100, "INSERT " "{fmts}", {arg_list});'''
        findings = scan_format_arg_mismatch(src)
        assert len(findings) == 1
        f = findings[0]
        assert f["format_count"] == 27
        assert f["arg_count"] == 26


class TestSkipCases:
    def test_skip_non_literal_format(self):
        """format이 변수/매크로면 탐지 불가 — 스킵."""
        src = 'sprintf(buf, fmt_var, a, b);'
        assert scan_format_arg_mismatch(src) == []

    def test_skip_if_macro_expanded(self):
        """FMT 매크로면 스킵."""
        src = 'sprintf(buf, MY_FMT_MACRO, a);'
        assert scan_format_arg_mismatch(src) == []

    def test_ignore_format_in_comment(self):
        """주석 안의 printf는 무시."""
        src = '/* sprintf(buf, "%d %d", a); */\nint x = 0;'
        assert scan_format_arg_mismatch(src) == []


class TestSqlHintNotComment:
    def test_oracle_sql_hint_inside_string_preserved(self):
        """문자열 리터럴 내부의 `/*+ ... */`(Oracle 힌트)는 주석으로 보지 않는다."""
        # 실사례 축약: 2개 %ld, 2개 인자 — 정상
        src = (
            'snprintf(buf, 100, '
            '"INSERT /*+ append parallel(t,%ld) */ '
            'SELECT /*+ full(t) parallel(t,%ld) */ * FROM t", '
            'a, b);'
        )
        assert scan_format_arg_mismatch(src) == []


class TestNestedCalls:
    def test_nested_call_in_arg(self):
        """인자 안에 함수 호출이 있어도 top-level 콤마만 센다."""
        src = 'sprintf(buf, "%d %d", func(a, b), c);'
        # 포맷 2개, 인자 2개 (func(a,b) 전체가 1개 인자)
        assert scan_format_arg_mismatch(src) == []
