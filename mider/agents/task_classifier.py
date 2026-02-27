"""TaskClassifierAgent: Phase 0 - 파일 분류 및 실행 계획 수립.

선택된 파일 목록을 분석하여 언어를 분류하고,
의존성을 파악하고, LLM으로 우선순위를 보정하여 ExecutionPlan을 생성한다.
"""

import json
import logging
from typing import Any

from mider.agents.base_agent import BaseAgent
from mider.config.prompt_loader import load_prompt
from mider.models.execution_plan import DependencyGraph, ExecutionPlan
from mider.tools.file_io.file_reader import FileReader
from mider.tools.utility.dependency_resolver import DependencyResolver
from mider.tools.utility.task_planner import TaskPlanner

logger = logging.getLogger(__name__)


class TaskClassifierAgent(BaseAgent):
    """Phase 0: 파일 분류 및 실행 계획을 수립하는 Agent.

    DependencyResolver로 의존성을 분석하고,
    TaskPlanner로 실행 계획을 생성한 뒤,
    LLM을 호출하여 우선순위를 보정한다.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        fallback_model: str | None = "gpt-4o",
        temperature: float = 0.0,
    ) -> None:
        super().__init__(
            model=model,
            fallback_model=fallback_model,
            temperature=temperature,
        )
        self._dependency_resolver = DependencyResolver()
        self._task_planner = TaskPlanner()
        self._file_reader = FileReader()

    async def run(
        self,
        *,
        files: list[str],
    ) -> dict[str, Any]:
        """파일 목록을 분류하고 실행 계획을 생성한다.

        Args:
            files: 분석 대상 파일 경로 리스트

        Returns:
            ExecutionPlan 형식의 딕셔너리
        """
        if not files:
            logger.warning("분석 대상 파일이 없습니다.")
            return ExecutionPlan(
                sub_tasks=[],
                dependencies=DependencyGraph(),
                total_files=0,
                estimated_time_seconds=0,
            ).model_dump()

        # Step 1: 의존성 분석
        logger.info(f"Phase 0 시작: {len(files)}개 파일 분류")
        dep_result = self._dependency_resolver.execute(files=files)

        edges = dep_result.data["edges"]
        has_circular = dep_result.data["has_circular"]
        warnings = dep_result.data["warnings"]

        # Step 2: 실행 계획 생성 (토폴로지 정렬 + 메타데이터)
        plan_result = self._task_planner.execute(
            files=files,
            edges=edges,
            has_circular=has_circular,
            warnings=warnings,
        )

        plan_data = plan_result.data

        # Step 3: LLM 우선순위 보정
        plan_data = await self._refine_priorities_with_llm(
            files=files,
            plan_data=plan_data,
        )

        # Step 4: ExecutionPlan 스키마로 검증
        execution_plan = ExecutionPlan.model_validate(plan_data)

        logger.info(
            f"Phase 0 완료: {execution_plan.total_files}개 파일, "
            f"예상 {execution_plan.estimated_time_seconds}초"
        )

        return execution_plan.model_dump()

    async def _refine_priorities_with_llm(
        self,
        *,
        files: list[str],
        plan_data: dict[str, Any],
    ) -> dict[str, Any]:
        """LLM을 호출하여 우선순위를 보정한다.

        Tool 기반 분석(토폴로지 정렬)에 LLM의 코드 이해를 더해
        critical 패턴이 있는 파일의 우선순위를 상향한다.

        LLM 호출 실패 시 Tool 결과를 그대로 반환한다 (graceful degradation).
        """
        # 파일 내용 수집
        file_contents = self._read_file_contents(files)
        if not file_contents:
            logger.warning("모든 파일 읽기 실패, LLM 우선순위 보정 건너뜀")
            return plan_data

        file_list_str = "\n".join(files)
        contents_str = "\n\n".join(
            f"### {path}\n```\n{content}\n```"
            for path, content in file_contents.items()
        )

        try:
            prompt = load_prompt(
                "task_classifier",
                file_list=file_list_str,
                file_contents=contents_str,
            )

            messages = [
                {
                    "role": "system",
                    "content": (
                        "당신은 소스코드 분석 전문가입니다. "
                        "파일을 분류하고 우선순위를 결정합니다. "
                        "반드시 JSON 형식으로 응답하세요."
                    ),
                },
                {"role": "user", "content": prompt},
            ]

            response = await self.call_llm(messages, json_mode=True)
            llm_result = json.loads(response)

            if not isinstance(llm_result, dict):
                logger.warning(f"LLM 응답이 dict가 아님: {type(llm_result)}")
                return plan_data

            # LLM 결과에서 우선순위만 추출하여 기존 plan에 적용
            return self._apply_llm_priorities(plan_data, llm_result)

        except Exception as e:
            logger.warning(
                f"LLM 우선순위 보정 실패, Tool 결과를 사용합니다: {e}"
            )
            return plan_data

    def _read_file_contents(
        self,
        files: list[str],
    ) -> dict[str, str]:
        """파일 내용을 읽어서 딕셔너리로 반환한다."""
        contents: dict[str, str] = {}
        for file_path in files:
            try:
                result = self._file_reader.execute(path=file_path)
                content = result.data["content"]
                # LLM 토큰 절약: 500줄 초과 시 처음/끝만 포함
                lines = content.split("\n")
                if len(lines) > 500:
                    head = "\n".join(lines[:250])
                    tail = "\n".join(lines[-250:])
                    content = f"{head}\n\n... ({len(lines) - 500} lines omitted) ...\n\n{tail}"
                contents[file_path] = content
            except Exception as e:
                logger.warning(f"파일 읽기 실패, 건너뜀: {file_path}: {e}")
        return contents

    def _apply_llm_priorities(
        self,
        plan_data: dict[str, Any],
        llm_result: dict[str, Any],
    ) -> dict[str, Any]:
        """LLM 결과의 우선순위를 기존 plan에 적용한다.

        LLM이 반환한 sub_tasks의 priority 순서를 기존 plan의
        sub_tasks에 적용한다. LLM 결과에 없는 task는 원래 순서를 유지한다.
        """
        llm_tasks = llm_result.get("sub_tasks", [])
        if not llm_tasks:
            return plan_data

        # LLM이 제안한 파일별 우선순위 매핑
        llm_priority_map: dict[str, int] = {}
        for task in llm_tasks:
            file_path = task.get("file", "")
            priority = task.get("priority")
            if file_path and isinstance(priority, int):
                llm_priority_map[file_path] = priority

        if not llm_priority_map:
            return plan_data

        # 기존 sub_tasks에 LLM 우선순위 적용
        existing_tasks = plan_data.get("sub_tasks", [])
        for task in existing_tasks:
            file_path = task.get("file", "")
            if file_path in llm_priority_map:
                task["priority"] = llm_priority_map[file_path]

        # 우선순위 순으로 재정렬
        existing_tasks.sort(key=lambda t: t.get("priority", 999))

        # task_id 재부여
        for idx, task in enumerate(existing_tasks, start=1):
            task["task_id"] = f"task_{idx}"
            task["priority"] = idx

        plan_data["sub_tasks"] = existing_tasks

        logger.info(
            f"LLM 우선순위 보정 적용: {len(llm_priority_map)}개 파일"
        )

        return plan_data
