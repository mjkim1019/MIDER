# 작업 계획서

## 개요
Mider 1차 PoC 전체 구현. 8개 Agent + 12개 Tool + CLI를 Bottom-up으로 15개 Task로 분해하여 순차 구현.

## Task 목록

### T1: Project Scaffold
- T1.1: 디렉토리 구조 생성 → `mider/` 전체
- T1.2: `__init__.py` 파일 → 모든 패키지
- T1.3: `requirements.txt` → openai, httpx, pydantic, rich
- T1.4: `config/settings.yaml` → LLM/API 설정
- T1.5: `resources/lint-configs/` → .eslintrc.json, .clang-tidy
- T1.6: `tests/conftest.py` → pytest fixture
- T1.7: `.gitignore` 업데이트

### T2: Data Models (depends: T1)
- T2.1: `models/execution_plan.py` → ExecutionPlan 5클래스
- T2.2: `models/file_context.py` → FileContext 5클래스
- T2.3: `models/analysis_result.py` → AnalysisResult 4클래스
- T2.4: `models/report.py` → IssueList/Checklist/Summary 8클래스
- T2.5: `models/__init__.py` → re-export
- T2.6: `tests/test_models/` → 단위 테스트

### T3: Base Infrastructure (depends: T1, T2)
- T3.1: `agents/base_agent.py` → BaseAgent ABC, call_llm() 재시도
- T3.2: `tools/base_tool.py` → BaseTool ABC, ToolResult, ToolExecutionError
- T3.3: `config/llm_client.py` → OpenAI 래퍼, JSON Mode
- T3.4: `config/prompt_loader.py` → 프롬프트 파일 로드
- T3.5: `config/logging_config.py` → Rich 로깅
- T3.6: 단위 테스트

### T4: File I/O & Search Tools (depends: T3)
- T4.1: `tools/file_io/file_reader.py`
- T4.2: `tools/search/grep.py`
- T4.3: `tools/search/glob_tool.py`
- T4.4: `tools/search/ast_grep_search.py`
- T4.5: 단위 테스트

### T5: Utility Tools (depends: T3, T4)
- T5.1: `tools/utility/sql_extractor.py`
- T5.2: `tools/utility/dependency_resolver.py`
- T5.3: `tools/utility/task_planner.py`
- T5.4: `tools/utility/checklist_generator.py`
- T5.5: 단위 테스트

### T6: Static Analysis Tools (depends: T3)
- T6.1: `tools/static_analysis/eslint_runner.py`
- T6.2: `tools/static_analysis/clang_tidy_runner.py`
- T6.3: `tools/static_analysis/proc_runner.py`
- T6.4: 단위 테스트

### T7: LSP Tool (depends: T3)
- T7.1: `tools/lsp/lsp_client.py`
- T7.2: 단위 테스트

### T8: Prompt Templates (depends: T3)
- T8.1~T8.8: `config/prompts/*.txt` 12개 프롬프트 파일

### T9: Phase 0 - TaskClassifierAgent (depends: T2, T3, T4, T5, T8)
- T9.1: `agents/task_classifier.py`
- T9.2: dependency_resolver + task_planner 연동
- T9.3: LLM 우선순위 보정
- T9.4: ExecutionPlan 반환
- T9.5: 단위 테스트

### T10: Phase 1 - ContextCollectorAgent (depends: T2, T3, T4, T7, T8)
- T10.1: `agents/context_collector.py`
- T10.2: 호출 관계 매핑
- T10.3: 공통 패턴 탐지
- T10.4: FileContext 반환
- T10.5: 단위 테스트

### T11: Phase 2 - 4개 Analyzer Agents (depends: T2, T3, T4, T5, T6, T7, T8)
- T11.1: `agents/js_analyzer.py` (ESLint + gpt-4o)
- T11.2: `agents/c_analyzer.py` (clang-tidy + gpt-4o)
- T11.3: `agents/proc_analyzer.py` (proc + gpt-4o)
- T11.4: `agents/sql_analyzer.py` (패턴 + gpt-4o-mini)
- T11.5: 단위 테스트 4개

### T12: Phase 3 - ReporterAgent (depends: T2, T3, T5, T8)
- T12.1: `agents/reporter.py`
- T12.2: checklist_generator 연동
- T12.3: 3개 JSON 출력
- T12.4: RiskAssessment 생성
- T12.5: 단위 테스트

### T13: OrchestratorAgent (depends: T9, T10, T11, T12)
- T13.1: `agents/orchestrator.py` (Phase 0→1→2→3)
- T13.2: call_agent, glob_expand, validate_files
- T13.3: Sub-agent 호출 관리
- T13.4: Progress 콜백
- T13.5: 단위 테스트

### T14: CLI Entry Point (depends: T13)
- T14.1: `main.py` argparse
- T14.2: 환경 변수 처리
- T14.3: Rich Progress Bar
- T14.4: Before/After 터미널 출력
- T14.5: 종료 코드
- T14.6: 단위 테스트

### T15: Integration Test (depends: T14)
- T15.1: `tests/fixtures/` 샘플 파일 4개
- T15.2: E2E 테스트
- T15.3: Exit code 검증
- T15.4: 출력 파일 검증

## 일정 요약
| Task | 의존성 | 병렬 가능 |
|------|--------|----------|
| T1 | - | - |
| T2 | T1 | - |
| T3 | T1, T2 | - |
| T4, T6, T7, T8 | T3 | **병렬** |
| T5 | T3, T4 | - |
| T9 | T2~T5, T8 | - |
| T10 | T2~T4, T7, T8 | - |
| T11, T12 | T2~T8 | **병렬** |
| T13 | T9~T12 | - |
| T14 | T13 | - |
| T15 | T14 | - |
