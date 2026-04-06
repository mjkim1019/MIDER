"""XMLParser: WebSquare(Proframe) XML 정적 분석 도구.

ElementTree 기반으로 WebSquare XML을 파싱하여
데이터 리스트, 컬럼 정의, 이벤트 바인딩, 컴포넌트 ID를 추출한다.
"""

import codecs
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult

logger = logging.getLogger(__name__)

# FileReader와 동일한 인코딩 탐지 순서
_SUPPORTED_ENCODINGS = ("utf-8", "cp949", "euc-kr")


def _read_text_auto(path: Path) -> str:
    """FileReader와 동일한 인코딩 폴백으로 텍스트 파일을 읽는다."""
    raw = path.read_bytes()
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    for enc in _SUPPORTED_ENCODINGS:
        try:
            return raw.decode(enc).replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    # 최후 수단: cp949 lossy
    return raw.decode("cp949", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

# 이벤트 속성 패턴 (ev:onclick, ev:onchange 등)
_EVENT_ATTR_RE = re.compile(r"\{.*\}on\w+$|^ev:on\w+$|^on\w+$")

# 데이터 정의 내부 요소 — 컴포넌트 ID 중복 검사에서 제외
# dataList/dataMap 자체는 $w.getById()로 접근하는 document-level ID이므로 유지
_DATA_DEFINITION_TAGS = frozenset({"column", "columnInfo", "data"})

# 인라인 JS 판별 키워드
_JS_KEYWORDS_RE = re.compile(r"(?:function\b|var\b|let\b|const\b|scwin\.|return\b|if\s*\(|\{)")


@dataclass
class ScriptBlock:
    """추출된 인라인 JS 블록의 위치 정보."""

    xml_start: int   # 원본 XML에서 CDATA 코드 시작 라인 (1-based)
    js_start: int    # 추출된 JS에서의 시작 라인 (1-based)
    length: int      # 블록 줄 수


def js_line_to_xml_line(js_line: int, offset_map: list[ScriptBlock]) -> int:
    """추출된 JS 라인 번호를 원본 XML 라인 번호로 변환한다."""
    for block in offset_map:
        if block.js_start <= js_line < block.js_start + block.length:
            return block.xml_start + (js_line - block.js_start)
    return js_line


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
            content = _read_text_auto(path)
        except Exception as e:
            raise ToolExecutionError("XMLParser", f"파일 읽기 실패: {e}")

        data_lists: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        component_ids: list[dict[str, Any]] = []
        duplicate_ids: list[dict[str, Any]] = []
        parse_errors: list[str] = []

        try:
            # XXE/Billion Laughs 방어: DOCTYPE 선언이 있으면 거부
            if "<!DOCTYPE" in content or "<!ENTITY" in content:
                parse_errors.append("보안: DOCTYPE/ENTITY 선언이 포함된 XML은 파싱 거부")
                return ToolResult(
                    success=False,
                    data={
                        "data_lists": [],
                        "events": [],
                        "component_ids": [],
                        "duplicate_ids": [],
                        "parse_errors": parse_errors,
                    },
                    error="보안: DOCTYPE/ENTITY 선언 포함",
                )
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

        # 1-1) dataList ID 유효성 검사
        for dl in data_lists:
            dl_id = dl.get("id", "")
            if not dl_id or not dl_id.strip() or dl_id.strip() == ":":
                parse_errors.append(
                    f"dataList ID가 비어있거나 유효하지 않음: '{dl_id}'"
                )

        # 2) 이벤트 바인딩 추출
        events = self._extract_events(root)

        # 3) 컴포넌트 ID 추출 + 중복 검사
        component_ids, duplicate_ids = self._extract_component_ids(root, content)

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
    def extract_inline_scripts(*, file: str) -> tuple[str, list[ScriptBlock]]:
        """XML에서 인라인 <script> CDATA의 JS 코드를 추출한다.

        src= 속성이 있는 외부 스크립트는 제외하고,
        CDATA 내용이 JS 코드인 블록만 추출한다.

        Returns:
            (JS 코드 문자열, 라인 오프셋 맵). JS 없으면 ("", []).
        """
        path = Path(file)
        if not path.exists():
            return "", []

        try:
            file_lines = _read_text_auto(path).splitlines()
        except Exception:
            return "", []

        js_blocks: list[str] = []
        offset_map: list[ScriptBlock] = []
        js_line_cursor = 1

        in_script = False
        cdata_start: int | None = None
        cdata_lines: list[str] = []

        for i, line in enumerate(file_lines, 1):
            lower = line.lower()

            if "<script" in lower and "src=" not in lower:
                in_script = True
                cdata_start = None
                cdata_lines = []

            if in_script:
                if "<![CDATA[" in line:
                    cdata_start = i
                    after = line.split("<![CDATA[", 1)[1]
                    if "]]>" in after:
                        # 한 줄짜리 CDATA: <![CDATA[code]]>
                        content = after.split("]]>", 1)[0]
                        if content.strip() and _JS_KEYWORDS_RE.search(content):
                            js_blocks.append(content)
                            offset_map.append(ScriptBlock(
                                xml_start=i,
                                js_start=js_line_cursor,
                                length=1,
                            ))
                            js_line_cursor += 1
                        cdata_start = None
                    elif after.strip():
                        cdata_lines.append(after)
                elif cdata_start is not None:
                    if "]]>" in line:
                        before_end = line.split("]]>", 1)[0]
                        if before_end.strip():
                            cdata_lines.append(before_end)

                        block_text = "\n".join(cdata_lines)
                        if block_text.strip() and _JS_KEYWORDS_RE.search(block_text):
                            block_length = len(cdata_lines)
                            js_blocks.append(block_text)
                            offset_map.append(ScriptBlock(
                                xml_start=cdata_start + 1,
                                js_start=js_line_cursor,
                                length=block_length,
                            ))
                            js_line_cursor += block_length

                        cdata_start = None
                        cdata_lines = []
                    else:
                        cdata_lines.append(line)

            if "</script>" in lower:
                in_script = False

        if not js_blocks:
            return "", []

        return "\n".join(js_blocks), offset_map

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
        content: str = "",
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """UI 컴포넌트의 ID를 추출하고 중복을 검사한다.

        데이터 정의 내부 요소(column, columnInfo, data)는
        dataList 스코프 내 스키마 정의이므로 제외한다.
        중복 ID 발견 시 원본 텍스트에서 라인 번호를 추출한다.
        """
        id_map: dict[str, list[str]] = {}  # id → [tag1, tag2, ...]
        all_ids: list[dict[str, Any]] = []

        for elem in root.iter():
            elem_id = elem.get("id")
            if not elem_id:
                continue

            local_tag = elem.tag.rsplit("}", 1)[-1] if "}" in elem.tag else elem.tag

            # 데이터 정의 내부 요소는 컴포넌트 ID가 아니므로 제외
            if local_tag in _DATA_DEFINITION_TAGS:
                continue

            all_ids.append({
                "id": elem_id,
                "tag": local_tag,
            })

            if elem_id not in id_map:
                id_map[elem_id] = []
            id_map[elem_id].append(local_tag)

        # 중복 ID 추출 + 라인 번호
        duplicates: list[dict[str, Any]] = []
        for eid, tags in id_map.items():
            if len(tags) > 1:
                lines = _find_id_lines(content, eid) if content else []
                duplicates.append({
                    "id": eid,
                    "count": len(tags),
                    "tags": tags,
                    "lines": lines,
                })

        return all_ids, duplicates


def _find_id_lines(content: str, elem_id: str) -> list[int]:
    """원본 XML 텍스트에서 id="elem_id" 패턴의 라인 번호를 찾는다.

    dataList 내부 column 등 데이터 정의 요소는 제외한다.
    """
    pattern = re.compile(rf'\bid=["\']{re.escape(elem_id)}["\']')
    lines: list[int] = []
    for line_num, line in enumerate(content.splitlines(), start=1):
        if pattern.search(line):
            # 데이터 정의 태그(column, columnInfo, data) 안의 id는 제외
            stripped = line.strip()
            is_data_def = False
            for tag in _DATA_DEFINITION_TAGS:
                if stripped.startswith(f"<w2:{tag}") or stripped.startswith(f"<{tag}"):
                    is_data_def = True
                    break
            if not is_data_def:
                lines.append(line_num)
    return lines


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
