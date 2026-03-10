"""XMLParser: WebSquare(Proframe) XML 정적 분석 도구.

ElementTree 기반으로 WebSquare XML을 파싱하여
데이터 리스트, 컬럼 정의, 이벤트 바인딩, 컴포넌트 ID를 추출한다.
"""

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult

logger = logging.getLogger(__name__)

# WebSquare 네임스페이스 약어
_NS = {
    "w2": "http://www.inswave.com/websquare",
    "ev": "http://www.w3.org/2001/xml-events",
    "xf": "http://www.w3.org/2002/xforms",
}

# 이벤트 속성 패턴 (ev:onclick, ev:onchange 등)
_EVENT_ATTR_RE = re.compile(r"\{.*\}on\w+$|^ev:on\w+$|^on\w+$")


class XMLParser(BaseTool):
    """WebSquare XML 파일을 파싱하여 구조 정보를 추출하는 Tool."""

    def execute(
        self,
        *,
        file: str,
    ) -> ToolResult:
        """XML 파일을 파싱하여 구조 정보를 반환한다.

        Args:
            file: XML 파일 경로

        Returns:
            ToolResult (data: data_lists, events, component_ids,
                        duplicate_ids, parse_errors)
        """
        path = Path(file)
        if not path.exists():
            raise ToolExecutionError("XMLParser", f"파일 없음: {file}")

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            raise ToolExecutionError("XMLParser", f"파일 읽기 실패: {e}")

        data_lists: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        component_ids: list[dict[str, Any]] = []
        duplicate_ids: list[dict[str, Any]] = []
        parse_errors: list[str] = []

        try:
            root = ET.fromstring(content)
        except ET.ParseError as e:
            parse_errors.append(f"XML 파싱 오류: {e}")
            return ToolResult(
                success=False,
                data={
                    "data_lists": [],
                    "events": [],
                    "component_ids": [],
                    "duplicate_ids": [],
                    "parse_errors": parse_errors,
                },
                error=f"XML 파싱 오류: {e}",
            )

        # 1) 데이터 리스트 + 컬럼 추출
        data_lists = self._extract_data_lists(root)

        # 2) 이벤트 바인딩 추출
        events = self._extract_events(root)

        # 3) 컴포넌트 ID 추출 + 중복 검사
        component_ids, duplicate_ids = self._extract_component_ids(root)

        logger.debug(
            f"XML 파싱 완료: {file} → "
            f"dataList {len(data_lists)}개, event {len(events)}개, "
            f"ID {len(component_ids)}개, 중복 {len(duplicate_ids)}개"
        )

        return ToolResult(
            success=True,
            data={
                "data_lists": data_lists,
                "events": events,
                "component_ids": component_ids,
                "duplicate_ids": duplicate_ids,
                "parse_errors": parse_errors,
            },
        )

    @staticmethod
    def _extract_data_lists(root: ET.Element) -> list[dict[str, Any]]:
        """w2:dataList 요소에서 데이터 리스트와 컬럼을 추출한다."""
        result: list[dict[str, Any]] = []

        # 네임스페이스 포함/미포함 모두 탐색
        for dl in root.iter():
            tag = dl.tag
            # w2:dataList 또는 {namespace}dataList
            local_name = tag.rsplit("}", 1)[-1] if "}" in tag else tag
            if local_name != "dataList":
                continue

            dl_id = dl.get("id", "")
            columns: list[dict[str, str]] = []

            # 하위 w2:column 추출 (columnInfo 래퍼 포함 재귀 탐색)
            for col in dl.iter():
                col_local = col.tag.rsplit("}", 1)[-1] if "}" in col.tag else col.tag
                if col_local == "column":
                    columns.append({
                        "id": col.get("id", ""),
                        "name": col.get("name", ""),
                        "dataType": col.get("dataType", col.get("type", "")),
                    })

            result.append({
                "id": dl_id,
                "columns": columns,
            })

        return result

    @staticmethod
    def _extract_events(root: ET.Element) -> list[dict[str, Any]]:
        """이벤트 바인딩(ev:on*, onclick 등)을 추출한다."""
        events: list[dict[str, Any]] = []

        for elem in root.iter():
            # 요소의 모든 속성에서 이벤트 패턴 탐색
            for attr_name, attr_value in elem.attrib.items():
                if _EVENT_ATTR_RE.match(attr_name):
                    # 이벤트 타입 추출 (on 이후 부분)
                    local_attr = attr_name.rsplit("}", 1)[-1] if "}" in attr_name else attr_name
                    # ev:onclick → onclick, onclick → onclick
                    if local_attr.startswith("ev:"):
                        local_attr = local_attr[3:]

                    elem_id = elem.get("id", "")
                    local_tag = elem.tag.rsplit("}", 1)[-1] if "}" in elem.tag else elem.tag

                    # 핸들러 함수명 추출 (scwin.funcName() 패턴)
                    handler_functions = _extract_handler_functions(attr_value)

                    events.append({
                        "element_id": elem_id,
                        "element_tag": local_tag,
                        "event_type": local_attr,
                        "handler": attr_value.strip(),
                        "handler_functions": handler_functions,
                    })

        return events

    @staticmethod
    def _extract_component_ids(
        root: ET.Element,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """모든 컴포넌트의 ID를 추출하고 중복을 검사한다."""
        id_map: dict[str, list[str]] = {}  # id → [tag1, tag2, ...]
        all_ids: list[dict[str, Any]] = []

        for elem in root.iter():
            elem_id = elem.get("id")
            if not elem_id:
                continue

            local_tag = elem.tag.rsplit("}", 1)[-1] if "}" in elem.tag else elem.tag

            all_ids.append({
                "id": elem_id,
                "tag": local_tag,
            })

            if elem_id not in id_map:
                id_map[elem_id] = []
            id_map[elem_id].append(local_tag)

        # 중복 ID 추출
        duplicates: list[dict[str, Any]] = []
        for eid, tags in id_map.items():
            if len(tags) > 1:
                duplicates.append({
                    "id": eid,
                    "count": len(tags),
                    "tags": tags,
                })

        return all_ids, duplicates


def _extract_handler_functions(handler_str: str) -> list[str]:
    """이벤트 핸들러 문자열에서 함수명을 추출한다.

    예: "scwin.btn_search_onclick()" → ["btn_search_onclick"]
    예: "scwin.fn_init(); scwin.fn_load();" → ["fn_init", "fn_load"]
    """
    functions: list[str] = []
    # scwin.funcName 패턴 (괄호 유무 모두 매칭)
    for m in re.finditer(r"scwin\.(\w+)", handler_str):
        functions.append(m.group(1))

    # scwin 없이 직접 함수 호출 패턴 (fn_xxx() 등)
    if not functions:
        for m in re.finditer(r"\b(\w+)\s*\(", handler_str):
            func_name = m.group(1)
            # 제어문 키워드 제외
            if func_name not in {"if", "for", "while", "return", "switch"}:
                functions.append(func_name)

    return functions
