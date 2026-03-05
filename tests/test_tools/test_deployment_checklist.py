"""DeploymentChecklistGenerator 단위 테스트."""

from mider.tools.utility.deployment_checklist import (
    DeploymentChecklistGenerator,
    classify_c_file,
    map_file_to_section,
)


# --- classify_c_file 테스트 ---


class TestClassifyCFile:
    """C 파일 TP/Module 판별 테스트."""

    def test_first_line_service_is_tp(self):
        """첫 줄에 SERVICE가 있으면 TP."""
        result = classify_c_file("abc.c", "/* SERVICE: payment */")
        assert result == "tp"

    def test_first_line_service_case_insensitive(self):
        """SERVICE 대소문자 무시."""
        result = classify_c_file("abc.c", "// Service Handler")
        assert result == "tp"

    def test_first_line_module_is_module(self):
        """첫 줄에 module이 있으면 Module."""
        result = classify_c_file("abc.c", "/* module: common_util */")
        assert result == "module"

    def test_first_line_module_case_insensitive(self):
        """MODULE 대문자도 인식됨 (lower() 비교)."""
        result = classify_c_file("abc.c", "/* MODULE: UTIL */")
        assert result == "module"

    def test_filename_third_from_end_t_is_tp(self):
        """파일명 뒤에서 3번째 문자가 t이면 TP."""
        result = classify_c_file("paytms.c", "")
        assert result == "tp"

    def test_filename_third_from_end_not_t(self):
        """파일명 뒤에서 3번째 문자가 t가 아니면 기본값 TP."""
        result = classify_c_file("common.c", "")
        assert result == "tp"

    def test_first_line_priority_over_filename(self):
        """첫 줄 주석이 파일명보다 우선."""
        # 파일명은 TP 패턴이지만 주석은 module
        result = classify_c_file("paytms.c", "/* module: shared_lib */")
        assert result == "module"

    def test_short_filename(self):
        """짧은 파일명 (3글자 미만)은 기본값 TP."""
        result = classify_c_file("ab.c", "")
        assert result == "tp"

    def test_no_comment_marker(self):
        """첫 줄이 주석이 아닌 경우 파일명으로 판별."""
        result = classify_c_file("paytms.c", "#include <stdio.h>")
        assert result == "tp"  # 파일명 규칙: 뒤에서 3번째가 t


# --- map_file_to_section 테스트 ---


class TestMapFileToSection:
    """파일 확장자 → 섹션 매핑 테스트."""

    def test_js_to_screen(self):
        assert map_file_to_section("app/view.js") == "screen"

    def test_xml_returns_none(self):
        """xml은 Mider 분석 대상이 아니므로 None."""
        assert map_file_to_section("app/layout.xml") is None

    def test_c_to_tp_by_default(self):
        assert map_file_to_section("service.c") == "tp"

    def test_c_to_module_by_first_line(self):
        assert map_file_to_section("util.c", "/* module: lib */") == "module"

    def test_h_to_module(self):
        assert map_file_to_section("common.h") == "module"

    def test_pc_to_batch(self):
        assert map_file_to_section("daily_batch.pc") == "batch"

    def test_sql_to_dbio(self):
        assert map_file_to_section("create_table.sql") == "dbio"

    def test_unknown_extension_returns_none(self):
        assert map_file_to_section("readme.md") is None

    def test_case_insensitive_extension(self):
        assert map_file_to_section("test.SQL") == "dbio"
        assert map_file_to_section("test.JS") == "screen"


# --- DeploymentChecklistGenerator 테스트 ---


class TestDeploymentChecklistGenerator:
    """배포 체크리스트 생성 테스트."""

    def test_single_js_file(self):
        """JS 파일 하나 → 화면 섹션만 생성."""
        gen = DeploymentChecklistGenerator()
        result = gen.execute(file_paths=["app/main.js"])

        assert result.success
        sections = result.data["sections"]
        assert len(sections) == 1
        assert sections[0]["section_id"] == "screen"
        assert sections[0]["files"] == ["app/main.js"]
        assert len(sections[0]["items"]) == 5  # SCR-01 ~ SCR-05

    def test_multiple_file_types(self):
        """다양한 파일 타입 → 여러 섹션 생성."""
        gen = DeploymentChecklistGenerator()
        result = gen.execute(
            file_paths=["view.js", "service.c", "batch.pc", "ddl.sql"],
        )

        assert result.success
        sections = result.data["sections"]
        section_ids = [s["section_id"] for s in sections]

        assert "screen" in section_ids
        assert "tp" in section_ids
        assert "batch" in section_ids
        assert "dbio" in section_ids

    def test_c_file_with_module_first_line(self):
        """C 파일 + module 주석 → Module 섹션."""
        gen = DeploymentChecklistGenerator()
        result = gen.execute(
            file_paths=["util.c"],
            file_first_lines={"util.c": "/* module: common */"},
        )

        assert result.success
        sections = result.data["sections"]
        assert len(sections) == 1
        assert sections[0]["section_id"] == "module"

    def test_h_file_creates_module_section(self):
        """헤더 파일 → Module 섹션."""
        gen = DeploymentChecklistGenerator()
        result = gen.execute(file_paths=["common.h"])

        assert result.success
        sections = result.data["sections"]
        assert len(sections) == 1
        assert sections[0]["section_id"] == "module"

    def test_empty_file_list(self):
        """빈 파일 목록 → 빈 체크리스트."""
        gen = DeploymentChecklistGenerator()
        result = gen.execute(file_paths=[])

        assert result.success
        assert result.data["sections"] == []
        assert result.data["total_items"] == 0

    def test_unsupported_extension_ignored(self):
        """지원하지 않는 확장자는 무시."""
        gen = DeploymentChecklistGenerator()
        result = gen.execute(
            file_paths=["readme.md", "config.yaml", "test.py"],
        )

        assert result.success
        assert result.data["sections"] == []
        assert result.data["total_items"] == 0

    def test_section_order_preserved(self):
        """섹션 순서: screen → tp → module → batch → dbio."""
        gen = DeploymentChecklistGenerator()
        result = gen.execute(
            file_paths=["ddl.sql", "view.js", "batch.pc", "service.c"],
        )

        sections = result.data["sections"]
        section_ids = [s["section_id"] for s in sections]
        assert section_ids == ["screen", "tp", "batch", "dbio"]

    def test_multiple_files_same_section(self):
        """같은 섹션의 여러 파일 → 하나의 섹션에 통합."""
        gen = DeploymentChecklistGenerator()
        result = gen.execute(
            file_paths=["a.js", "b.js", "c.js"],
        )

        sections = result.data["sections"]
        assert len(sections) == 1
        assert sections[0]["section_id"] == "screen"
        assert len(sections[0]["files"]) == 3

    def test_total_items_count(self):
        """전체 항목 수가 정확한지 확인."""
        gen = DeploymentChecklistGenerator()
        result = gen.execute(
            file_paths=["view.js", "service.c"],
        )

        # screen(5) + tp(7) = 12
        assert result.data["total_items"] == 12

    def test_files_by_section_in_data(self):
        """files_by_section이 올바르게 반환되는지."""
        gen = DeploymentChecklistGenerator()
        result = gen.execute(
            file_paths=["a.js", "b.c", "c.sql"],
        )

        fbs = result.data["files_by_section"]
        assert "screen" in fbs
        assert "tp" in fbs
        assert "dbio" in fbs

    def test_tp_and_module_c_files(self):
        """C 파일이 TP와 Module 모두로 분류."""
        gen = DeploymentChecklistGenerator()
        result = gen.execute(
            file_paths=["service.c", "util.c", "common.h"],
            file_first_lines={
                "service.c": "/* SERVICE: payment */",
                "util.c": "/* module: lib */",
            },
        )

        sections = result.data["sections"]
        section_ids = [s["section_id"] for s in sections]
        assert "tp" in section_ids
        assert "module" in section_ids

    def test_items_have_correct_structure(self):
        """체크 항목 구조 확인."""
        gen = DeploymentChecklistGenerator()
        result = gen.execute(file_paths=["test.sql"])

        items = result.data["sections"][0]["items"]
        for item in items:
            assert "id" in item
            assert "item" in item
            assert "checked" in item
            assert item["checked"] is False
