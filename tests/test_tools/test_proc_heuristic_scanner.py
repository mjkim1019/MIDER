"""ProCHeuristicScanner 단위 테스트."""

import pytest

from mider.tools.base_tool import ToolExecutionError
from mider.tools.static_analysis.proc_heuristic_scanner import (
    ProCHeuristicScanner,
    _extract_core_name,
)


@pytest.fixture
def scanner():
    return ProCHeuristicScanner()


class TestExtractCoreName:
    """핵심 이름 추출 테스트."""

    def test_variable_name(self):
        assert _extract_core_name("zord_abn_sale_spc_u0010_in") == "zord_abn_sale_spc_u0010"

    def test_type_name(self):
        assert _extract_core_name("zord_abn_sale_spc_s0009_in_t") == "zord_abn_sale_spc_s0009"

    def test_matching_names(self):
        """동일 DBIO이면 매칭."""
        var = _extract_core_name("zord_wire_svc_rcv_s0047_in")
        typ = _extract_core_name("zord_wire_svc_rcv_s0047_in_t")
        assert var == typ

    def test_mismatching_names(self):
        """다른 DBIO이면 불일치 (u0010 vs s0009)."""
        var = _extract_core_name("zord_abn_sale_spc_u0010_in")
        typ = _extract_core_name("zord_abn_sale_spc_s0009_in_t")
        assert var != typ


class TestFormatStruct:
    """Pattern 1: %s에 구조체 전달 탐지."""

    def test_detect_struct_to_format_string(self, scanner, tmp_path):
        """구조체 배열 원소가 %s에 전달되면 탐지."""
        f = tmp_path / "test.pc"
        f.write_text(
            'PFM_DSP("value = [%s]", io.in.phon_num[0]);\n'
        )
        result = scanner.execute(file=str(f))
        findings = [x for x in result.data["findings"]
                    if x["pattern_id"] == "FORMAT_STRUCT"]
        assert len(findings) == 1
        assert findings[0]["severity"] == "critical"

    def test_no_detect_with_member_access(self, scanner, tmp_path):
        """구조체 멤버까지 접근하면 정상 — 미탐지."""
        f = tmp_path / "test.pc"
        f.write_text(
            'PFM_DSP("value = [%s]", io.in.phon_num[0].rcv_phon_num);\n'
        )
        result = scanner.execute(file=str(f))
        findings = [x for x in result.data["findings"]
                    if x["pattern_id"] == "FORMAT_STRUCT"]
        assert len(findings) == 0


class TestMemsetMismatch:
    """Pattern 2: memset sizeof 불일치 탐지."""

    def test_detect_mismatch(self, scanner, tmp_path):
        """변수명과 sizeof 타입명이 다르면 탐지."""
        f = tmp_path / "test.pc"
        f.write_text(
            'memset(&zord_abn_sale_spc_u0010_in, 0x00, '
            'sizeof(zord_abn_sale_spc_s0009_in_t));\n'
        )
        result = scanner.execute(file=str(f))
        findings = [x for x in result.data["findings"]
                    if x["pattern_id"] == "MEMSET_SIZEOF_MISMATCH"]
        assert len(findings) == 1
        assert "불일치" in findings[0]["description"]

    def test_no_detect_matching(self, scanner, tmp_path):
        """변수명과 sizeof 타입명이 일치하면 미탐지."""
        f = tmp_path / "test.pc"
        f.write_text(
            'memset(&zord_wire_svc_rcv_s0047_in, 0x00, '
            'sizeof(zord_wire_svc_rcv_s0047_in_t));\n'
        )
        result = scanner.execute(file=str(f))
        findings = [x for x in result.data["findings"]
                    if x["pattern_id"] == "MEMSET_SIZEOF_MISMATCH"]
        assert len(findings) == 0

    def test_proframe_prefix_with_declaration(self, scanner, tmp_path):
        """ProFrame 명명규약 (`l_ctx: bat_ctx_t`) — 실 선언 lookup으로 false positive 제거."""
        f = tmp_path / "test.pc"
        f.write_text(
            "void main() {\n"
            "    bat_ctx_t l_ctx;\n"
            "    memset(&l_ctx, 0x00, sizeof(bat_ctx_t));\n"
            "}\n"
        )
        result = scanner.execute(file=str(f))
        findings = [x for x in result.data["findings"]
                    if x["pattern_id"] == "MEMSET_SIZEOF_MISMATCH"]
        assert findings == []

    def test_global_struct_prefix_with_declaration(self, scanner, tmp_path):
        """전역 struct prefix 케이스 (`gst_hd: sms_file_header_bo_t`)."""
        f = tmp_path / "test.pc"
        f.write_text(
            "sms_file_header_bo_t gst_hd;\n"
            "void f() {\n"
            "    memset(&gst_hd, 0x20, sizeof(sms_file_header_bo_t));\n"
            "}\n"
        )
        result = scanner.execute(file=str(f))
        findings = [x for x in result.data["findings"]
                    if x["pattern_id"] == "MEMSET_SIZEOF_MISMATCH"]
        assert findings == []

    def test_typedef_without_t_suffix(self, scanner, tmp_path):
        """`_t` 접미사 없는 ProFrame 타입(`st_result_set gst_rpset;`)."""
        f = tmp_path / "test.pc"
        f.write_text(
            "st_result_set gst_rpset;\n"
            "void f() {\n"
            "    memset(&gst_rpset, 0x00, sizeof(st_result_set));\n"
            "}\n"
        )
        result = scanner.execute(file=str(f))
        findings = [x for x in result.data["findings"]
                    if x["pattern_id"] == "MEMSET_SIZEOF_MISMATCH"]
        assert findings == []

    def test_sizeof_self_pattern_safe(self, scanner, tmp_path):
        """`memset(buf, 0, sizeof(buf))` 자기 자신 크기 패턴은 안전."""
        f = tmp_path / "test.pc"
        f.write_text(
            "void f() {\n"
            "    char lc_file_name[256];\n"
            "    memset(lc_file_name, 0x00, sizeof(lc_file_name));\n"
            "}\n"
        )
        result = scanner.execute(file=str(f))
        findings = [x for x in result.data["findings"]
                    if x["pattern_id"] == "MEMSET_SIZEOF_MISMATCH"]
        assert findings == []

    def test_real_mismatch_via_declaration_still_detected(self, scanner, tmp_path):
        """선언 타입이 sizeof 타입과 진짜 다르면 정탐 보존."""
        f = tmp_path / "test.pc"
        f.write_text(
            "zord_u0010_in_t var;\n"
            "void f() {\n"
            "    memset(&var, 0x00, sizeof(zord_s0009_in_t));\n"
            "}\n"
        )
        result = scanner.execute(file=str(f))
        findings = [x for x in result.data["findings"]
                    if x["pattern_id"] == "MEMSET_SIZEOF_MISMATCH"]
        assert len(findings) == 1

    def test_commented_old_declaration_ignored(self, scanner, tmp_path):
        """주석 처리된 옛 선언이 활성 선언보다 위에 있어도 활성 선언이 매칭됨."""
        f = tmp_path / "test.pc"
        f.write_text(
            "// sms_file_header_t gst_hd;  /* 옛 정의 */\n"
            "sms_file_header_bo_t gst_hd;  /* 현재 정의 */\n"
            "void f() {\n"
            "    memset(&gst_hd, 0x20, sizeof(sms_file_header_bo_t));\n"
            "}\n"
        )
        result = scanner.execute(file=str(f))
        findings = [x for x in result.data["findings"]
                    if x["pattern_id"] == "MEMSET_SIZEOF_MISMATCH"]
        assert findings == []


class TestLoopInitMissing:
    """Pattern 3: 루프 내 초기화 누락 탐지."""

    def test_detect_missing_init(self, scanner, tmp_path):
        """루프 내 쓰기 있고 초기화 없으면 탐지."""
        f = tmp_path / "test.pc"
        f.write_text(
            'while (SQLCODE == SQL_OK) {\n'
            '    strncpy(gst_sec.field, src, 10);\n'
            '}\n'
        )
        result = scanner.execute(file=str(f))
        findings = [x for x in result.data["findings"]
                    if x["pattern_id"] == "LOOP_INIT_MISSING"]
        assert len(findings) == 1

    def test_detect_commented_init(self, scanner, tmp_path):
        """초기화가 주석 처리되어 있으면 탐지."""
        f = tmp_path / "test.pc"
        f.write_text(
            'while (SQLCODE == SQL_OK) {\n'
            '    /* INIT2VCHAR(gst_sec); */\n'
            '    strncpy(gst_sec.field, src, 10);\n'
            '}\n'
        )
        result = scanner.execute(file=str(f))
        findings = [x for x in result.data["findings"]
                    if x["pattern_id"] == "LOOP_INIT_MISSING"]
        assert len(findings) == 1
        assert "주석 처리" in findings[0]["description"]

    def test_no_detect_with_init(self, scanner, tmp_path):
        """초기화가 있으면 미탐지."""
        f = tmp_path / "test.pc"
        f.write_text(
            'while (SQLCODE == SQL_OK) {\n'
            '    INIT2VCHAR(gst_sec);\n'
            '    strncpy(gst_sec.field, src, 10);\n'
            '}\n'
        )
        result = scanner.execute(file=str(f))
        findings = [x for x in result.data["findings"]
                    if x["pattern_id"] == "LOOP_INIT_MISSING"]
        assert len(findings) == 0

    def test_detect_partial_init(self, scanner, tmp_path):
        """일부 구조체만 초기화되고 다른 구조체가 누락된 케이스.

        zinvbreps8030.pc L5951 실사례: gst_aia/gst_reisu는 초기화하지만
        gst_sec_06에 쓰는 strncpy가 초기화 없이 수행됨.
        """
        f = tmp_path / "test.pc"
        f.write_text(
            'while (li_flag == TRUE) {\n'
            '    INIT2VCHAR(gst_aia);\n'
            '    INIT2VCHAR(gst_reisu);\n'
            '    EXEC SQL FETCH C1 INTO :gst_aia;\n'
            '    for (i = 0; i < n; i++) {\n'
            '        strncpy(gst_reisu.use_dt, src1, 10);\n'
            '        strncpy(gst_sec_06.lcl_cd[0], src2, 10);\n'
            '        strncpy(gst_sec_06.amt[0], src3, 20);\n'
            '    }\n'
            '}\n'
        )
        result = scanner.execute(file=str(f))
        findings = [x for x in result.data["findings"]
                    if x["pattern_id"] == "LOOP_INIT_MISSING"]
        # gst_sec_06 만 누락으로 보고되어야 한다
        variables = {x.get("variable") for x in findings}
        assert "gst_sec_06" in variables
        assert "gst_aia" not in variables
        assert "gst_reisu" not in variables

    def test_detect_multiple_missing_structs(self, scanner, tmp_path):
        """루프 안 여러 구조체 모두 초기화 누락 — 각각 보고."""
        f = tmp_path / "test.pc"
        f.write_text(
            'while (cond) {\n'
            '    strncpy(gst_a.field, src, 10);\n'
            '    strncpy(gst_b.field, src, 10);\n'
            '}\n'
        )
        result = scanner.execute(file=str(f))
        findings = [x for x in result.data["findings"]
                    if x["pattern_id"] == "LOOP_INIT_MISSING"]
        variables = {x.get("variable") for x in findings}
        assert variables == {"gst_a", "gst_b"}

    def test_no_detect_plain_buffer_copy(self, scanner, tmp_path):
        """구조체 멤버가 아닌 단순 문자열 복사는 미탐지 (false-positive 방지)."""
        f = tmp_path / "test.pc"
        f.write_text(
            'while (n-- > 0) {\n'
            '    strncpy(buf, src, 10);\n'
            '}\n'
        )
        result = scanner.execute(file=str(f))
        findings = [x for x in result.data["findings"]
                    if x["pattern_id"] == "LOOP_INIT_MISSING"]
        assert len(findings) == 0


class TestFcloseMissing:
    """Pattern 4: fopen/fclose 짝 불일치 탐지."""

    def test_detect_missing_fclose(self, scanner, tmp_path):
        """fopen 있고 fclose 없으면 탐지."""
        f = tmp_path / "test.pc"
        f.write_text(
            'FILE *fp = fopen("test.dat", "w");\n'
            'fwrite(buf, 1, n, fp);\n'
        )
        result = scanner.execute(file=str(f))
        findings = [x for x in result.data["findings"]
                    if x["pattern_id"] == "FCLOSE_MISSING"]
        assert len(findings) == 1

    def test_no_detect_with_fclose(self, scanner, tmp_path):
        """fopen/fclose 짝이 맞으면 미탐지."""
        f = tmp_path / "test.pc"
        f.write_text(
            'FILE *fp = fopen("test.dat", "w");\n'
            'fwrite(buf, 1, n, fp);\n'
            'fclose(fp);\n'
        )
        result = scanner.execute(file=str(f))
        findings = [x for x in result.data["findings"]
                    if x["pattern_id"] == "FCLOSE_MISSING"]
        assert len(findings) == 0


class TestFileNotFound:
    """파일 없음 테스트."""

    def test_file_not_found(self, scanner):
        with pytest.raises(ToolExecutionError, match="file not found"):
            scanner.execute(file="/nonexistent.pc")


class TestEmptyFile:
    """빈 파일 테스트."""

    def test_empty_file(self, scanner, tmp_path):
        f = tmp_path / "empty.pc"
        f.write_text("")
        result = scanner.execute(file=str(f))
        assert result.data["total_findings"] == 0
