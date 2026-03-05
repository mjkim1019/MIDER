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
        """패턴이 올바른 함수에 매핑됨."""
        f = tmp_path / "multi.c"
        f.write_text(
            "void func_a() {\n"
            "    int a;\n"
            "}\n"
            "\n"
            "void func_b() {\n"
            "    int b;\n"
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
        """반환형과 함수명이 다른 줄에 있는 경우."""
        f = tmp_path / "twoline.c"
        f.write_text(
            "static long\n"
            "my_function(int *ctx)\n"
            "{\n"
            "    long count;\n"
            "    return 0;\n"
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
