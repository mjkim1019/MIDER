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
