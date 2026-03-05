"""DeploymentChecklist: 파일 확장자 기반 배포 체크리스트 자동 생성.

분석 대상 파일의 확장자를 기준으로 5개 섹션(화면/TP/Module/Batch/DBIO)의
배포 절차 체크리스트를 생성한다. LLM 없이 정적 데이터 기반으로 동작한다.
"""

import logging
from pathlib import Path
from typing import Any

from mider.tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)


# --- 섹션별 체크리스트 항목 정의 ---

SECTION_1_SCREEN: list[dict[str, str]] = [
    {"id": "SCR-01", "item": "화면 소스(xml/js) SVN 커밋 확인"},
    {"id": "SCR-02", "item": "개발서버 배포 및 정상 동작 확인"},
    {"id": "SCR-03", "item": "운영서버 배포 요청서 작성"},
    {"id": "SCR-04", "item": "운영서버 배포 후 화면 캐시 갱신 확인"},
    {"id": "SCR-05", "item": "운영서버 정상 동작 확인"},
]

SECTION_2_TP: list[dict[str, str]] = [
    {"id": "TP-01", "item": "TP 소스(.c) SVN 커밋 확인"},
    {"id": "TP-02", "item": "개발서버 컴파일(make) 정상 완료 확인"},
    {"id": "TP-03", "item": "개발서버 TP 기동 및 테스트 확인"},
    {"id": "TP-04", "item": "운영서버 배포 요청서 작성"},
    {"id": "TP-05", "item": "운영서버 컴파일(make) 정상 완료 확인"},
    {"id": "TP-06", "item": "운영서버 TP 재기동 확인"},
    {"id": "TP-07", "item": "운영서버 정상 거래 확인"},
]

SECTION_3_MODULE: list[dict[str, str]] = [
    {"id": "MOD-01", "item": "Module 소스(.c/.h) SVN 커밋 확인"},
    {"id": "MOD-02", "item": "개발서버 컴파일(make) 정상 완료 확인"},
    {"id": "MOD-03", "item": "Module 참조하는 TP 전체 재컴파일 확인"},
    {"id": "MOD-04", "item": "개발서버 테스트 확인"},
    {"id": "MOD-05", "item": "운영서버 배포 요청서 작성 (Module + 관련 TP)"},
    {"id": "MOD-06", "item": "운영서버 컴파일(make) 정상 완료 확인"},
    {"id": "MOD-07", "item": "운영서버 관련 TP 전체 재기동 확인"},
    {"id": "MOD-08", "item": "운영서버 정상 거래 확인"},
]

SECTION_4_BATCH: list[dict[str, str]] = [
    {"id": "BAT-01", "item": "Batch 소스(.pc) SVN 커밋 확인"},
    {"id": "BAT-02", "item": "개발서버 proc 프리컴파일 정상 완료 확인"},
    {"id": "BAT-03", "item": "개발서버 컴파일(make) 정상 완료 확인"},
    {"id": "BAT-04", "item": "개발서버 Batch 테스트 실행 확인"},
    {"id": "BAT-05", "item": "운영서버 배포 요청서 작성"},
    {"id": "BAT-06", "item": "운영서버 컴파일(make) 정상 완료 확인"},
    {"id": "BAT-07", "item": "운영서버 Batch 스케줄 등록/변경 확인"},
]

SECTION_5_DBIO: list[dict[str, str]] = [
    {"id": "DBI-01", "item": "DBIO SQL(.sql) SVN 커밋 확인"},
    {"id": "DBI-02", "item": "개발DB 스크립트 실행 확인"},
    {"id": "DBI-03", "item": "개발DB 데이터 검증 확인"},
    {"id": "DBI-04", "item": "운영DB 배포 요청서 작성"},
    {"id": "DBI-05", "item": "운영DB 스크립트 실행 확인"},
    {"id": "DBI-06", "item": "운영DB 데이터 검증 확인"},
]

# 섹션 ID → 섹션 데이터 매핑
_SECTIONS: dict[str, dict[str, Any]] = {
    "screen": {
        "title": "화면 배포 (xml/js)",
        "items": SECTION_1_SCREEN,
    },
    "tp": {
        "title": "TP 배포 (.c)",
        "items": SECTION_2_TP,
    },
    "module": {
        "title": "Module 배포 (.c/.h)",
        "items": SECTION_3_MODULE,
    },
    "batch": {
        "title": "Batch 배포 (.pc)",
        "items": SECTION_4_BATCH,
    },
    "dbio": {
        "title": "DBIO 배포 (.sql)",
        "items": SECTION_5_DBIO,
    },
}


def classify_c_file(file_path: str, first_line: str) -> str:
    """C 파일이 TP인지 Module인지 판별한다.

    판별 우선순위:
    1. 첫 줄 주석: 'SERVICE' → TP, 'module' → Module
    2. 파일명: 뒤에서 3번째 문자가 't' → TP
    3. 기본값: TP

    Args:
        file_path: 파일 경로
        first_line: 파일 첫 줄 내용

    Returns:
        "tp" 또는 "module"
    """
    # 규칙 1: 첫 줄 주석 검사
    stripped = first_line.strip()
    if stripped.startswith("/*") or stripped.startswith("//"):
        upper = stripped.upper()
        if "SERVICE" in upper:
            return "tp"
        if "MODULE" in stripped.lower():
            return "module"

    # 규칙 2: 파일명 검사 (뒤에서 3번째 문자가 't')
    stem = Path(file_path).stem
    if len(stem) >= 3 and stem[-3].lower() == "t":
        return "tp"

    # 기본값: TP
    return "tp"


def map_file_to_section(file_path: str, first_line: str = "") -> str | None:
    """파일 확장자와 내용으로 배포 섹션을 결정한다.

    Args:
        file_path: 파일 경로
        first_line: 파일 첫 줄 내용 (C 파일의 TP/Module 판별용)

    Returns:
        섹션 ID ("screen", "tp", "module", "batch", "dbio") 또는 None
    """
    ext = Path(file_path).suffix.lower()

    if ext == ".js":
        return "screen"
    elif ext == ".xml":
        return "screen"
    elif ext == ".c":
        return classify_c_file(file_path, first_line)
    elif ext == ".h":
        return "module"
    elif ext == ".pc":
        return "batch"
    elif ext == ".sql":
        return "dbio"

    return None


class DeploymentChecklistGenerator(BaseTool):
    """파일 확장자 기반 배포 체크리스트를 생성하는 Tool."""

    def execute(
        self,
        *,
        file_paths: list[str],
        file_first_lines: dict[str, str] | None = None,
    ) -> ToolResult:
        """분석 대상 파일 목록에서 배포 체크리스트를 생성한다.

        Args:
            file_paths: 분석 대상 파일 경로 리스트
            file_first_lines: 파일별 첫 줄 내용 매핑 (C 파일 TP/Module 판별용)

        Returns:
            ToolResult (data: sections, total_items, files_by_section)
        """
        if file_first_lines is None:
            file_first_lines = {}

        # 파일별 섹션 매핑
        section_files: dict[str, list[str]] = {}
        for fp in file_paths:
            first_line = file_first_lines.get(fp, "")
            section = map_file_to_section(fp, first_line)
            if section is None:
                logger.debug(f"배포 섹션 매핑 불가 (미지원 확장자): {fp}")
                continue
            if section not in section_files:
                section_files[section] = []
            section_files[section].append(fp)

        # 활성 섹션만 체크리스트 생성
        sections: list[dict[str, Any]] = []
        total_items = 0

        # 섹션 순서 유지
        section_order = ["screen", "tp", "module", "batch", "dbio"]
        for section_id in section_order:
            if section_id not in section_files:
                continue

            section_data = _SECTIONS[section_id]
            items = [
                {
                    "id": item["id"],
                    "item": item["item"],
                    "checked": False,
                }
                for item in section_data["items"]
            ]

            sections.append({
                "section_id": section_id,
                "title": section_data["title"],
                "files": section_files[section_id],
                "items": items,
            })
            total_items += len(items)

        logger.debug(
            f"배포 체크리스트 생성 완료: {len(sections)}개 섹션, "
            f"{total_items}개 항목"
        )

        return ToolResult(
            success=True,
            data={
                "sections": sections,
                "total_items": total_items,
                "files_by_section": section_files,
            },
        )
