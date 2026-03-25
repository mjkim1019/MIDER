"""XMLAnalyzerAgent: Phase 2 - WebSquare XML 분석.

XML 파서 결과 + JS 교차 검증을 결합하여
WebSquare XML 파일의 구조 이슈를 탐지한다.
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from mider.agents.base_agent import BaseAgent
from mider.config.prompt_loader import load_prompt
from mider.config.settings_loader import (
    get_agent_fallback_model,
    get_agent_model,
    get_agent_temperature,
)
from mider.models.analysis_result import AnalysisResult
from mider.tools.file_io.file_reader import FileReader
from mider.tools.static_analysis.xml_parser import XMLParser

logger = logging.getLogger(__name__)


class XMLAnalyzerAgent(BaseAgent):
    """Phase 2: WebSquare XML 파일을 분석하는 Agent.

    XMLParser로 구조를 파싱하고, 대응 JS 파일과 교차 검증하여
    Error-Focused 또는 Heuristic 경로로 이슈를 탐지한다.
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

    async def run(
        self,
        *,
        task_id: str,
        file: str,
        language: str = "xml",
        file_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """XML 파일을 분석한다.

        Args:
            task_id: ExecutionPlan의 task_id
            file: 분석할 XML 파일 경로
            language: 파일 언어 ("xml")
            file_context: Phase 1에서 수집한 파일 컨텍스트

        Returns:
            AnalysisResult 형식의 딕셔너리
        """
        start_time = time.time()
        logger.info(f"XML 분석 시작: {file}")

        try:
            # Step 1: XML 파싱
            parse_result = self._xml_parser.execute(file=file)
            parse_data = parse_result.data

            # 파싱 결과 로그
            data_lists = parse_data.get("data_lists", [])
            dl_summary = ", ".join(
                f"{dl['id']}: {len(dl.get('columns', []))}cols"
                for dl in data_lists[:5]
            )
            self.rl.scan(
                f"Parse: {len(data_lists)} dataList ({dl_summary})"
            )
            self.rl.scan(
                f"Parse: {len(parse_data.get('events', []))} events, "
                f"{len(parse_data.get('component_ids', []))} components, "
                f"{len(parse_data.get('duplicate_ids', []))} duplicate ID"
            )

            # 중복 ID 상세
            for dup in parse_data.get("duplicate_ids", []):
                lines_str = ", ".join(str(ln) for ln in dup.get("lines", []))
                self.rl.detect(
                    f"Detect: {dup['id']} 중복 ({lines_str}행) "
                    f"— {' × '.join(dup.get('tags', []))}"
                )

            # 도구 실행 결과 표준 로그
            _fn = Path(file).name
            logger.info(
                f"XML [{_fn}] parse: "
                f"dataList={len(data_lists)}, "
                f"events={len(parse_data.get('events', []))}, "
                f"dup_ids={len(parse_data.get('duplicate_ids', []))}"
            )

            # Step 2: JS 교차 검증
            js_validation = self._validate_js_handlers(file, parse_data)
            js_file = js_validation.get("js_file")
            missing = js_validation.get("missing_handlers", [])
            total_events = len(parse_data.get("events", []))
            if js_file:
                if missing:
                    self.rl.detect(
                        f"JS검증: {len(missing)}개 핸들러 누락 ({js_file})"
                    )
                else:
                    self.rl.scan(f"JS검증: 핸들러 검증 통과 ({js_file})")
                logger.info(
                    f"XML [{_fn}] JS검증: "
                    f"missing={len(missing)}/{total_events} 핸들러"
                )
            else:
                self.rl.decision("JS검증: 대응 JS 파일 없음 — 핸들러 교차검증 불가")
                logger.info(f"XML [{_fn}] JS검증: 대응 JS 파일 없음")

            # Step 3: Error-Focused / Heuristic 분기
            has_errors = (
                parse_data.get("parse_errors")
                or parse_data.get("duplicate_ids")
                or js_validation.get("missing_handlers")
            )

            filename = Path(file).name
            dup_count = len(parse_data.get("duplicate_ids", []))
            miss_count = len(missing)
            err_count = len(parse_data.get("parse_errors", []))
            if has_errors:
                self.rl.decision(
                    "Decision: Error-Focused path",
                    reason=f"duplicate_ids={dup_count}건, "
                           f"missing_handlers={miss_count}건, "
                           f"parse_errors={err_count}건",
                )
                logger.info(
                    f"XML [{filename}] 경로: Error-Focused | "
                    f"dup_ids={dup_count}, missing_handlers={miss_count}, "
                    f"parse_errors={err_count}"
                )
            else:
                self.rl.decision(
                    "Decision: Heuristic path",
                    reason="정적 오류 없음 → 구조 전체 검증",
                )
                logger.info(
                    f"XML [{filename}] 경로: Heuristic | 정적 오류 없음"
                )

            prompt, messages = self._build_messages(
                file=file,
                parse_data=parse_data,
                js_validation=js_validation,
                has_errors=has_errors,
            )

            # 프롬프트 정보 로그
            prompt_name = "xml_analyzer_error_focused" if has_errors else "xml_analyzer_heuristic"
            prompt_tokens = len(prompt) // 4
            self.rl.prompt(
                f"Prompt: {prompt_name} "
                f"({len(data_lists)} dataList, "
                f"{len(parse_data.get('events', []))} events 포함)"
            )
            self.rl.prompt(f"  입력 토큰 추정: ~{prompt_tokens:,}")

            # Step 4: LLM 분석
            llm_start = time.time()
            self.rl.llm_request(f"LLM 호출: {self.model} 요청 중...")
            response = await self.call_llm(messages, json_mode=True)
            llm_elapsed = time.time() - llm_start
            tokens_estimate = (len(prompt) + len(response)) // 4
            self.rl.llm_response(
                f"LLM 응답: {tokens_estimate:,} tokens, {llm_elapsed:.1f}초"
            )

            llm_result = json.loads(response)

            if not isinstance(llm_result, dict):
                raise ValueError(f"LLM 응답이 dict가 아님: {type(llm_result)}")

            issues = llm_result.get("issues", [])
            self.rl.process(f"Parse: LLM JSON 파싱 → {len(issues)}개 이슈 추출")

            # Step 5: AnalysisResult 생성
            elapsed = time.time() - start_time

            result = AnalysisResult.model_validate({
                "task_id": task_id,
                "file": file,
                "language": language,
                "agent": "XMLAnalyzerAgent",
                "issues": issues,
                "analysis_time_seconds": round(elapsed, 2),
                "llm_tokens_used": tokens_estimate,
            })

            self.rl.process("Validate: AnalysisResult 스키마 검증 통과")

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

    def _validate_js_handlers(
        self,
        xml_file: str,
        parse_data: dict[str, Any],
    ) -> dict[str, Any]:
        """XML 이벤트 핸들러에 대응하는 JS 함수가 존재하는지 검증한다.

        JS 파일 매칭: XML 파일명과 동일한 stem의 .js 파일 탐색
        """
        events = parse_data.get("events", [])
        if not events:
            return {"js_file": None, "missing_handlers": []}

        # 대응 JS 파일 탐색
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
            logger.debug(f"대응 JS 파일 없음: {xml_file}")
            return {"js_file": None, "missing_handlers": []}

        # 핸들러 함수 존재 여부 검증 (단어 경계 매칭)
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

    def _build_messages(
        self,
        *,
        file: str,
        parse_data: dict[str, Any],
        js_validation: dict[str, Any],
        has_errors: bool,
    ) -> tuple[str, list[dict[str, str]]]:
        """프롬프트 경로를 선택하고 LLM 메시지를 구성한다."""
        # 공통 데이터 직렬화
        data_lists_str = json.dumps(
            parse_data.get("data_lists", []), ensure_ascii=False, indent=2,
        )
        events_str = json.dumps(
            parse_data.get("events", []), ensure_ascii=False, indent=2,
        )
        component_ids_str = json.dumps(
            parse_data.get("component_ids", [])[:50],  # 상위 50개만
            ensure_ascii=False, indent=2,
        )

        if has_errors:
            # Error-Focused 경로
            parse_errors_str = json.dumps(
                parse_data.get("parse_errors", []), ensure_ascii=False,
            )
            duplicate_ids_str = json.dumps(
                parse_data.get("duplicate_ids", []), ensure_ascii=False, indent=2,
            )
            missing_handlers_str = json.dumps(
                js_validation.get("missing_handlers", []),
                ensure_ascii=False, indent=2,
            )

            prompt = load_prompt(
                "xml_analyzer_error_focused",
                file_path=file,
                parse_errors=parse_errors_str,
                duplicate_ids=duplicate_ids_str,
                missing_handlers=missing_handlers_str,
                data_lists=data_lists_str,
                events=events_str,
                js_file=js_validation.get("js_file") or "없음",
            )
        else:
            # Heuristic 경로
            prompt = load_prompt(
                "xml_analyzer_heuristic",
                file_path=file,
                data_lists=data_lists_str,
                events=events_str,
                component_ids=component_ids_str,
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

        return prompt, messages
