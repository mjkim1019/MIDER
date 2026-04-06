"""XMLAnalyzerAgent: Phase 2 - WebSquare XML 분석.

XML 구조 분석 + 인라인 JS를 JSAnalyzer에 위임하여
WebSquare XML 파일의 구조 이슈와 JS 코드 이슈를 통합 탐지한다.
"""

import json
import logging
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from mider.agents.base_agent import BaseAgent
from mider.agents.js_analyzer import JavaScriptAnalyzerAgent
from mider.config.prompt_loader import load_prompt
from mider.config.settings_loader import (
    get_agent_fallback_model,
    get_agent_model,
    get_agent_temperature,
)
from mider.models.analysis_result import AnalysisResult
from mider.tools.file_io.file_reader import FileReader
from mider.tools.static_analysis.xml_parser import (
    ScriptBlock,
    XMLParser,
    js_line_to_xml_line,
)
from mider.tools.utility.token_optimizer import build_datalist_summary

logger = logging.getLogger(__name__)


class XMLAnalyzerAgent(BaseAgent):
    """Phase 2: WebSquare XML 파일을 분석하는 Agent.

    XML 구조 분석(dataList, 이벤트, 중복 ID)은 직접 수행하고,
    인라인 JS 코드는 JSAnalyzerAgent에 위임하여 분석한다.
    """

    def __init__(
        self,
        model: str | None = None,
        fallback_model: str | None = None,
        temperature: float | None = None,
    ) -> None:
        _name = "xml_analyzer"
        model = model or get_agent_model(_name)
        fallback_model = fallback_model or get_agent_fallback_model(_name)
        temperature = temperature if temperature is not None else get_agent_temperature(_name)
        super().__init__(
            model=model,
            fallback_model=fallback_model,
            temperature=temperature,
        )
        self._xml_parser = XMLParser()
        self._file_reader = FileReader()
        self._js_analyzer = JavaScriptAnalyzerAgent()

    async def run(
        self,
        *,
        task_id: str,
        file: str,
        language: str = "xml",
        file_context: dict[str, Any] | None = None,
        file_content: str | None = None,
    ) -> dict[str, Any]:
        """XML 파일을 분석한다.

        1. XML 구조 파싱 (dataList, events, duplicate IDs)
        2. 인라인 JS 추출 → JSAnalyzer 위임
        3. XML 구조 이슈 + JS 코드 이슈 병합

        Args:
            file_content: 주석 제거된 파일 내용 (현재 미사용 — XML 파서가 직접 파일을 읽음)

        Returns:
            AnalysisResult 형식의 딕셔너리
        """
        start_time = time.time()
        logger.info(f"XML 분석 시작: {file}")

        try:
            filename = Path(file).name

            # Step 1: XML 파싱
            parse_result = self._xml_parser.execute(file=file)
            parse_data = parse_result.data

            data_lists = parse_data.get("data_lists", [])
            events = parse_data.get("events", [])
            duplicate_ids = parse_data.get("duplicate_ids", [])
            parse_errors = parse_data.get("parse_errors", [])

            self.rl.scan(
                f"Parse: {len(data_lists)} dataList, "
                f"{len(events)} events, "
                f"{len(duplicate_ids)} duplicate ID"
            )
            logger.info(
                f"XML [{filename}] parse: "
                f"dataList={len(data_lists)}, "
                f"events={len(events)}, "
                f"dup_ids={len(duplicate_ids)}"
            )

            # Step 2: JS 교차 검증
            js_validation = self._validate_js_handlers(file, parse_data)
            js_file = js_validation.get("js_file")
            missing = js_validation.get("missing_handlers", [])
            if js_file:
                logger.info(
                    f"XML [{filename}] JS검증: "
                    f"missing={len(missing)}/{len(events)} 핸들러"
                )
            else:
                logger.info(f"XML [{filename}] JS검증: 대응 JS 파일 없음")

            # Step 3: XML 구조 분석 (LLM)
            xml_issues = await self._analyze_xml_structure(
                file=file,
                parse_data=parse_data,
                js_validation=js_validation,
            )
            logger.info(f"XML [{filename}] 구조 이슈: {len(xml_issues)}건")

            # Step 4: 인라인 JS 추출 → JSAnalyzer 위임
            js_issues = await self._analyze_inline_js(
                file=file,
                task_id=task_id,
            )
            logger.info(f"XML [{filename}] 인라인 JS 이슈: {len(js_issues)}건")

            # Step 5: 이슈 병합 + issue_id 재번호
            all_issues = self._merge_issues(xml_issues, js_issues)

            # Step 6: AnalysisResult 생성
            elapsed = time.time() - start_time
            result = AnalysisResult.model_validate({
                "task_id": task_id,
                "file": file,
                "language": language,
                "agent": "XMLAnalyzerAgent",
                "issues": all_issues,
                "analysis_time_seconds": round(elapsed, 2),
                "llm_tokens_used": 0,
            })

            logger.info(
                f"XML 분석 완료: {file} → {len(result.issues)}개 이슈, "
                f"{result.analysis_time_seconds}초"
            )
            return result.model_dump()

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"XML 분석 실패: {file}: {e}")
            return AnalysisResult(
                task_id=task_id,
                file=file,
                language=language,
                agent="XMLAnalyzerAgent",
                issues=[],
                analysis_time_seconds=round(elapsed, 2),
                llm_tokens_used=0,
                error=str(e),
            ).model_dump()

    async def _analyze_xml_structure(
        self,
        *,
        file: str,
        parse_data: dict[str, Any],
        js_validation: dict[str, Any],
    ) -> list[dict]:
        """XML 구조를 LLM으로 분석한다."""
        datalist_summary = build_datalist_summary(
            parse_data.get("data_lists", []),
        )
        events_str = json.dumps(
            parse_data.get("events", []), ensure_ascii=False, indent=2,
        )
        duplicate_ids_str = json.dumps(
            parse_data.get("duplicate_ids", []), ensure_ascii=False, indent=2,
        )
        missing_handlers_str = json.dumps(
            js_validation.get("missing_handlers", []),
            ensure_ascii=False, indent=2,
        )
        parse_errors_str = json.dumps(
            parse_data.get("parse_errors", []), ensure_ascii=False,
        )

        prompt = load_prompt(
            "xml_analyzer",
            file_path=file,
            datalist_summary=datalist_summary,
            events=events_str,
            duplicate_ids=duplicate_ids_str,
            missing_handlers=missing_handlers_str,
            parse_errors=parse_errors_str,
            js_file=js_validation.get("js_file") or "없음",
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 WebSquare(Proframe) XML 구조 분석 전문가입니다. "
                    "반드시 JSON 형식으로 응답하세요."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        response = await self.call_llm(messages, json_mode=True)
        llm_result = json.loads(response)

        if isinstance(llm_result, dict):
            return llm_result.get("issues", [])
        return []

    async def _analyze_inline_js(
        self,
        *,
        file: str,
        task_id: str,
    ) -> list[dict]:
        """인라인 JS를 추출하여 JSAnalyzer에 위임한다."""
        js_code, offset_map = XMLParser.extract_inline_scripts(file=file)

        if not js_code:
            logger.info(f"XML [{Path(file).name}] 인라인 JS 없음")
            return []

        js_line_count = len(js_code.splitlines())
        logger.info(
            f"XML [{Path(file).name}] 인라인 JS 추출: "
            f"{js_line_count}줄, {len(offset_map)}블록"
        )

        # 임시 .js 파일 생성 → JSAnalyzer 위임
        tmp_file = tempfile.NamedTemporaryFile(
            suffix=".js", mode="w", encoding="utf-8", delete=False,
        )
        tmp_path = Path(tmp_file.name)
        try:
            tmp_file.write(js_code)
            tmp_file.close()
            js_result = await self._js_analyzer.run(
                task_id=task_id,
                file=str(tmp_path),
                language="javascript",
            )
        finally:
            tmp_path.unlink(missing_ok=True)

        # 라인 번호 변환 + 파일 경로 원본으로 복원
        js_issues = js_result.get("issues", [])
        self._remap_js_issues(js_issues, file, offset_map)

        return js_issues

    @staticmethod
    def _remap_js_issues(
        issues: list[dict],
        original_file: str,
        offset_map: list[ScriptBlock],
    ) -> None:
        """JS 이슈의 라인 번호를 원본 XML 라인으로 변환한다."""
        for issue in issues:
            loc = issue.setdefault("location", {})
            if loc.get("line_start"):
                loc["line_start"] = js_line_to_xml_line(
                    loc["line_start"], offset_map,
                )
            if loc.get("line_end"):
                loc["line_end"] = js_line_to_xml_line(
                    loc["line_end"], offset_map,
                )
            loc["file"] = original_file

    @staticmethod
    def _merge_issues(
        xml_issues: list[dict],
        js_issues: list[dict],
    ) -> list[dict]:
        """XML 구조 이슈와 JS 코드 이슈를 병합하고 issue_id를 재번호한다.

        Note: 원본 dict의 issue_id를 직접 수정한다.
              호출 후 원본 리스트의 재사용은 하지 않는다.
        """
        all_issues = list(xml_issues) + list(js_issues)
        for idx, issue in enumerate(all_issues, 1):
            issue["issue_id"] = f"XML-{idx:03d}"
        return all_issues

    def _validate_js_handlers(
        self,
        xml_file: str,
        parse_data: dict[str, Any],
    ) -> dict[str, Any]:
        """XML 이벤트 핸들러에 대응하는 JS 함수가 존재하는지 검증한다."""
        events = parse_data.get("events", [])
        if not events:
            return {"js_file": None, "missing_handlers": []}

        xml_path = Path(xml_file)
        js_candidates = [
            xml_path.with_suffix(".js"),
            xml_path.parent / f"{xml_path.stem}_wq.js",
        ]

        js_file = None
        js_content = None
        for candidate in js_candidates:
            if candidate.exists():
                js_file = str(candidate)
                try:
                    read_result = self._file_reader.execute(path=js_file)
                    js_content = read_result.data["content"]
                except Exception as e:
                    logger.warning(f"JS 파일 읽기 실패: {js_file}: {e}")
                break

        if js_content is None:
            return {"js_file": None, "missing_handlers": []}

        missing_handlers: list[dict[str, str]] = []
        for event in events:
            for func_name in event.get("handler_functions", []):
                if not re.search(rf"\b{re.escape(func_name)}\b", js_content):
                    missing_handlers.append({
                        "function_name": func_name,
                        "element_id": event.get("element_id", ""),
                        "event_type": event.get("event_type", ""),
                    })

        return {
            "js_file": js_file,
            "missing_handlers": missing_handlers,
        }
