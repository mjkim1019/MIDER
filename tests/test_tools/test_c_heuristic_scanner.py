"""CHeuristicScanner 단위 테스트."""

import pytest

from mider.tools.base_tool import ToolExecutionError
from mider.tools.static_analysis.c_heuristic_scanner import CHeuristicScanner


class TestCHeuristicScanner:
    def setup_method(self):
        self.scanner = CHeuristicScanner()

    # ── 기본 동작 ────────────────────────────────

    def test_empty_file(self, tmp_path):
        """빈 파일은 findings 0건."""
        f = tmp_path / "empty.c"
        f.write_text("")
        result = self.scanner.execute(file=str(f))
        assert result.success is True
        assert result.data["findings"] == []
        assert result.data["functions_at_risk"] == []

    def test_safe_file(self, tmp_path):
        """안전한 코드는 UNINIT_VAR 없음."""
        f = tmp_path / "safe.c"
        f.write_text("int main() {\n    int x = 0;\n    return x;\n}\n")
        result = self.scanner.execute(file=str(f))
        uninit = [f for f in result.data["findings"] if f["pattern_id"] == "UNINIT_VAR"]
        assert uninit == []

    def test_file_not_found(self):
        """존재하지 않는 파일."""
        with pytest.raises(ToolExecutionError, match="file not found"):
            self.scanner.execute(file="/nonexistent/test.c")

    # ── UNINIT_VAR 패턴 ──────────────────────────

    def test_uninit_var_detected(self, tmp_path):
        """함수 내 초기화 없는 변수 탐지."""
        f = tmp_path / "uninit.c"
        f.write_text(
            "void foo() {\n"
            "    int count;\n"
            "    count++;\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        uninit = [f for f in result.data["findings"] if f["pattern_id"] == "UNINIT_VAR"]
        assert len(uninit) == 1
        assert uninit[0]["line"] == 2
        assert uninit[0]["function"] == "foo"

    def test_uninit_var_global_ignored(self, tmp_path):
        """전역 변수 선언은 UNINIT_VAR로 잡지 않음."""
        f = tmp_path / "global.c"
        f.write_text("int global_count;\n\nint main() { return 0; }\n")
        result = self.scanner.execute(file=str(f))
        uninit = [f for f in result.data["findings"] if f["pattern_id"] == "UNINIT_VAR"]
        assert uninit == []

    def test_initialized_var_not_flagged(self, tmp_path):
        """초기화된 변수는 UNINIT_VAR 아님."""
        f = tmp_path / "init.c"
        f.write_text("void bar() {\n    int x = 10;\n}\n")
        result = self.scanner.execute(file=str(f))
        uninit = [f for f in result.data["findings"] if f["pattern_id"] == "UNINIT_VAR"]
        assert uninit == []

    def test_for_init_assignment_recognized(self, tmp_path):
        """`for (var = ...)` 패턴은 안전한 초기화로 인정."""
        f = tmp_path / "forinit.c"
        f.write_text(
            "void f() {\n"
            "    long i;\n"
            "    for (i = 0; i < 10; i++) {\n"
            "        printf(\"%ld\\n\", i);\n"
            "    }\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        uninit = [f for f in result.data["findings"] if f["pattern_id"] == "UNINIT_VAR"]
        assert uninit == []

    def test_separate_assignment_recognized(self, tmp_path):
        """선언 후 별도 라인에서 할당하는 ProFrame 표준 스타일."""
        f = tmp_path / "sep.c"
        f.write_text(
            "void f() {\n"
            "    long total;\n"
            "    total = 0;\n"
            "    total += compute();\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        uninit = [f for f in result.data["findings"] if f["pattern_id"] == "UNINIT_VAR"]
        assert uninit == []

    def test_init2vchar_macro_recognized(self, tmp_path):
        """ProFrame INIT2VCHAR/INIT2STR 매크로는 안전 초기화."""
        f = tmp_path / "init2vchar.c"
        f.write_text(
            "void f() {\n"
            "    long buf;\n"
            "    INIT2VCHAR(buf);\n"
            "    process(buf);\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        uninit = [f for f in result.data["findings"] if f["pattern_id"] == "UNINIT_VAR"]
        assert uninit == []

    def test_address_pass_recognized(self, tmp_path):
        """`&var` 주소 전달 (memset/scanf/fread)은 안전 초기화."""
        f = tmp_path / "addr.c"
        f.write_text(
            "void f() {\n"
            "    long buf;\n"
            "    memset(&buf, 0, sizeof(buf));\n"
            "    process(buf);\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        uninit = [f for f in result.data["findings"] if f["pattern_id"] == "UNINIT_VAR"]
        assert uninit == []

    def test_unused_var_not_flagged(self, tmp_path):
        """함수 끝까지 사용 안 한 변수는 issue 아님."""
        f = tmp_path / "unused.c"
        f.write_text(
            "void f() {\n"
            "    long unused_var;\n"
            "    return;\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        uninit = [f for f in result.data["findings"] if f["pattern_id"] == "UNINIT_VAR"]
        assert uninit == []

    def test_use_before_assignment_still_detected(self, tmp_path):
        """사용 전에 할당 없으면 정탐 보존."""
        f = tmp_path / "real_uninit.c"
        f.write_text(
            "void f() {\n"
            "    long count;\n"
            "    long total = count + 1;\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        uninit = [f for f in result.data["findings"] if f["pattern_id"] == "UNINIT_VAR"]
        assert len(uninit) == 1
        assert uninit[0]["match"] == "    long count;"

    def test_compound_assignment_does_not_count_as_init(self, tmp_path):
        """`+=` compound는 prior value 의존 — init으로 인정 안 함."""
        f = tmp_path / "compound.c"
        f.write_text(
            "void f() {\n"
            "    long counter;\n"
            "    counter += 1;\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        uninit = [f for f in result.data["findings"] if f["pattern_id"] == "UNINIT_VAR"]
        assert len(uninit) == 1

    # ── UNSAFE_FUNC 패턴 ─────────────────────────

    def test_unsafe_func_detected(self, tmp_path):
        """strcpy 사용 탐지."""
        f = tmp_path / "unsafe.c"
        f.write_text(
            "void copy() {\n"
            '    strcpy(dst, src);\n'
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        unsafe = [f for f in result.data["findings"] if f["pattern_id"] == "UNSAFE_FUNC"]
        assert len(unsafe) == 1
        assert "strcpy" in unsafe[0]["match"]

    def test_sprintf_detected(self, tmp_path):
        """sprintf 탐지."""
        f = tmp_path / "sprintf.c"
        f.write_text('void fmt() {\n    sprintf(buf, "%s", str);\n}\n')
        result = self.scanner.execute(file=str(f))
        unsafe = [f for f in result.data["findings"] if f["pattern_id"] == "UNSAFE_FUNC"]
        assert len(unsafe) == 1

    # ── BOUNDED_FUNC 패턴 ────────────────────────

    def test_bounded_func_detected(self, tmp_path):
        """memset 사용 탐지."""
        f = tmp_path / "bounded.c"
        f.write_text(
            "void init() {\n"
            "    memset(&ctx, 0, sizeof(ctx));\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        bounded = [f for f in result.data["findings"] if f["pattern_id"] == "BOUNDED_FUNC"]
        assert len(bounded) == 1

    # ── MALLOC_NO_CHECK 패턴 ─────────────────────

    def test_malloc_detected(self, tmp_path):
        """malloc 사용 탐지."""
        f = tmp_path / "malloc.c"
        f.write_text(
            "void alloc() {\n"
            "    char *p = malloc(100);\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        malloc = [f for f in result.data["findings"] if f["pattern_id"] == "MALLOC_NO_CHECK"]
        assert len(malloc) == 1

    # ── BUFFER_INDEX 패턴 ────────────────────────

    def test_buffer_index_detected(self, tmp_path):
        """변수 인덱스 배열 접근 탐지."""
        f = tmp_path / "buffer.c"
        f.write_text(
            "void access() {\n"
            "    arr[idx] = 1;\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        buf = [f for f in result.data["findings"] if f["pattern_id"] == "BUFFER_INDEX"]
        assert len(buf) == 1

    # ── 주석/문자열 무시 ─────────────────────────

    def test_comment_ignored(self, tmp_path):
        """주석 내 패턴 무시."""
        f = tmp_path / "comment.c"
        f.write_text(
            "void safe() {\n"
            "    // strcpy(dst, src);\n"
            "    int x = 0;\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        unsafe = [f for f in result.data["findings"] if f["pattern_id"] == "UNSAFE_FUNC"]
        assert unsafe == []

    def test_block_comment_ignored(self, tmp_path):
        """블록 주석 내 패턴 무시."""
        f = tmp_path / "block.c"
        f.write_text(
            "void safe() {\n"
            "    /* strcpy(dst, src); */\n"
            "    int x = 0;\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        unsafe = [f for f in result.data["findings"] if f["pattern_id"] == "UNSAFE_FUNC"]
        assert unsafe == []

    def test_string_literal_ignored(self, tmp_path):
        """문자열 리터럴 내 패턴 무시."""
        f = tmp_path / "str.c"
        f.write_text(
            'void safe() {\n'
            '    printf("strcpy is bad");\n'
            '}\n'
        )
        result = self.scanner.execute(file=str(f))
        unsafe = [f for f in result.data["findings"] if f["pattern_id"] == "UNSAFE_FUNC"]
        assert unsafe == []

    # ── 함수 매핑 ────────────────────────────────

    def test_function_mapping(self, tmp_path):
        """패턴이 올바른 함수에 매핑됨 (사용 전 미할당 → 정탐 보존)."""
        f = tmp_path / "multi.c"
        f.write_text(
            "void func_a() {\n"
            "    int a;\n"
            "    process(a);\n"  # a를 초기화 없이 사용
            "}\n"
            "\n"
            "void func_b() {\n"
            "    int b;\n"
            "    handle(b);\n"  # b를 초기화 없이 사용
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        uninit = [f for f in result.data["findings"] if f["pattern_id"] == "UNINIT_VAR"]
        assert len(uninit) == 2
        func_names = {f["function"] for f in uninit}
        assert func_names == {"func_a", "func_b"}

    def test_functions_at_risk(self, tmp_path):
        """functions_at_risk에 위험 함수 목록 반환."""
        f = tmp_path / "risk.c"
        f.write_text(
            "void risky() {\n"
            "    strcpy(a, b);\n"
            "}\n"
            "void safe() {\n"
            "    int x = 0;\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        assert "risky" in result.data["functions_at_risk"]

    # ── 2줄 함수 선언 ───────────────────────────

    def test_two_line_func_declaration(self, tmp_path):
        """반환형과 함수명이 다른 줄에 있는 경우 (사용 전 미할당 → 정탐)."""
        f = tmp_path / "twoline.c"
        f.write_text(
            "static long\n"
            "my_function(int *ctx)\n"
            "{\n"
            "    long count;\n"
            "    return count + 1;\n"  # 초기화 없이 count 사용
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        uninit = [f for f in result.data["findings"] if f["pattern_id"] == "UNINIT_VAR"]
        assert len(uninit) == 1
        assert uninit[0]["function"] == "my_function"

    # ── 복합 시나리오 ───────────────────────────

    def test_multiple_patterns_same_function(self, tmp_path):
        """한 함수에서 여러 패턴 탐지."""
        f = tmp_path / "complex.c"
        f.write_text(
            "void dangerous() {\n"
            "    int count;\n"
            "    char *buf = malloc(100);\n"
            "    strcpy(buf, src);\n"
            "    arr[count] = 1;\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        patterns = {f["pattern_id"] for f in result.data["findings"]}
        assert "UNINIT_VAR" in patterns
        assert "MALLOC_NO_CHECK" in patterns
        assert "UNSAFE_FUNC" in patterns
        assert "BUFFER_INDEX" in patterns
        # 함수는 1개
        assert result.data["functions_at_risk"] == ["dangerous"]


class TestMemsetSizeMismatch:
    """memset sizeof 타입 불일치 탐지 테스트."""

    def setup_method(self):
        self.scanner = CHeuristicScanner()

    def test_mismatch_detected(self, tmp_path):
        """변수 타입 ≠ sizeof 타입 → MEMSET_SIZE_MISMATCH 탐지."""
        f = tmp_path / "mismatch.c"
        f.write_text(
            "#include <string.h>\n"
            "void c200_update(void) {\n"
            "    zord_abn_sale_spc_u0010_in_t zord_abn_sale_spc_u0010_in;\n"
            "    memset(&zord_abn_sale_spc_u0010_in, 0x00, sizeof(zord_abn_sale_spc_s0009_in_t));\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        mismatch = [
            f for f in result.data["findings"]
            if f["pattern_id"] == "MEMSET_SIZE_MISMATCH"
        ]
        assert len(mismatch) == 1
        assert "u0010" in mismatch[0]["description"]
        assert "s0009" in mismatch[0]["description"]
        assert mismatch[0]["severity"] == "high"
        assert mismatch[0]["function"] == "c200_update"

    def test_matching_type_not_detected(self, tmp_path):
        """변수 타입 == sizeof 타입 → 미탐지."""
        f = tmp_path / "match.c"
        f.write_text(
            "#include <string.h>\n"
            "void c100_query(void) {\n"
            "    zord_abn_sale_spc_s0009_in_t zord_abn_sale_spc_s0009_in;\n"
            "    memset(&zord_abn_sale_spc_s0009_in, 0x00, sizeof(zord_abn_sale_spc_s0009_in_t));\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        mismatch = [
            f for f in result.data["findings"]
            if f["pattern_id"] == "MEMSET_SIZE_MISMATCH"
        ]
        assert len(mismatch) == 0

    def test_struct_member_excluded(self, tmp_path):
        """구조체 멤버 접근(ctx->var)은 Scanner에서 제외."""
        f = tmp_path / "member.c"
        f.write_text(
            "#include <string.h>\n"
            "void c300_init(void *ctx) {\n"
            "    memset(&ctx->zord_out, 0x00, sizeof(zord_other_t));\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        mismatch = [
            f for f in result.data["findings"]
            if f["pattern_id"] == "MEMSET_SIZE_MISMATCH"
        ]
        # ctx->var는 regex에서 &\w+만 매칭하므로 제외됨
        assert len(mismatch) == 0

    def test_local_prefix_stripped(self, tmp_path):
        """로컬 변수 접두사(ll_, lc_)는 제거 후 비교."""
        f = tmp_path / "local_prefix.c"
        f.write_text(
            "#include <string.h>\n"
            "void d400_send(void) {\n"
            "    zngmmmsg12310_io_t ll_zngmmmsg12310_io;\n"
            "    memset(&ll_zngmmmsg12310_io, 0x00, sizeof(zngmmmsg12310_io_t));\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        mismatch = [
            f for f in result.data["findings"]
            if f["pattern_id"] == "MEMSET_SIZE_MISMATCH"
        ]
        # ll_ 접두사 제거 후 일치 → 미탐지
        assert len(mismatch) == 0

    def test_sizeof_same_var_not_detected(self, tmp_path):
        """sizeof(변수명) — 타입이 아닌 변수명 자체 → 미탐지."""
        f = tmp_path / "sizeof_var.c"
        f.write_text(
            "#include <string.h>\n"
            "void init(void) {\n"
            "    char lc_param_nm[64];\n"
            "    memset(lc_param_nm, 0x00, sizeof(lc_param_nm));\n"
            "}\n"
        )
        result = self.scanner.execute(file=str(f))
        mismatch = [
            f for f in result.data["findings"]
            if f["pattern_id"] == "MEMSET_SIZE_MISMATCH"
        ]
        # &가 없으므로 regex 매칭 안 됨
        assert len(mismatch) == 0
