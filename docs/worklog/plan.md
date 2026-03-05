# 작업 계획서

## 개요
Mider 1차 PoC 전체 구현. 8개 Agent + 12개 Tool + CLI를 Bottom-up으로 15개 Task로 분해하여 순차 구현.
T1~T9 완료, T10부터 재개.

## 완료된 Task
- T1~T9: Project Scaffold → Data Models → Base Infrastructure → Tools → Prompts → TaskClassifierAgent

## 진행 예정 Task

### T10: ContextCollectorAgent (Phase 1) (depends: T2, T3, T4, T7, T8)
- T10.1: `agents/context_collector.py` 기본 구조 → 대상: `mider/agents/context_collector.py`
  - BaseAgent 상속, gpt-4o-mini / fallback gpt-4o
  - Tool 인스턴스 생성 (FileReader, Grep, AstGrepSearch, DependencyResolver)
  - run(execution_plan: dict) 시그니처
- T10.2: Import/Include 추출 + 호출 관계 매핑 → 대상: `mider/agents/context_collector.py`
  - 언어별 import/include 정규표현식 또는 AstGrepSearch 패턴
  - 분석 대상 파일 내 resolved_path 매칭
  - 함수 호출 추출 (Grep/AstGrepSearch)
- T10.3: 공통 패턴 탐지 → 대상: `mider/agents/context_collector.py`
  - error_handling, logging, transaction, memory_management 4가지 패턴
  - 언어별 패턴 정규표현식
  - common_patterns 빈도 집계
- T10.4: LLM 컨텍스트 보정 + FileContext 반환 → 대상: `mider/agents/context_collector.py`
  - LLM에 파일 내용 + Tool 결과 전달하여 보정
  - FileContext 스키마 검증 (model_validate)
  - LLM 실패 시 Tool 결과로 graceful degradation
- T10.5: 단위 테스트 → 대상: `tests/test_agents/test_context_collector.py`
  - LLM mock, 각 언어별 파일 fixture
  - 빈 입력, 단일 파일, 다중 파일, LLM 실패 시나리오

### T11: Phase 2 - 4개 Analyzer Agents (depends: T2, T3, T4, T5, T6, T7, T8)
- T11.1: `agents/js_analyzer.py` → ESLint + gpt-4o, Error-Focused/Heuristic 2경로
- T11.2: `agents/c_analyzer.py` → clang-tidy + gpt-4o, Error-Focused/Heuristic 2경로
- T11.3: `agents/proc_analyzer.py` → proc + sql_extractor + gpt-4o, Error-Focused/Heuristic 2경로
- T11.4: `agents/sql_analyzer.py` → 정적 패턴 + gpt-4o-mini, Error-Focused/Heuristic 2경로
- T11.5: `tests/test_agents/test_{js,c,proc,sql}_analyzer.py` → 단위 테스트 4개

### T12: Phase 3 - ReporterAgent (depends: T2, T3, T5, T8)
- T12.1: `agents/reporter.py` 구현 → 심각도 분류, Before/After 추출
- T12.2: checklist_generator 연동 → critical/high 이슈 체크리스트
- T12.3: 3개 JSON 출력 → IssueList, Checklist, Summary
- T12.4: RiskAssessment 생성 → 배포 위험도 판정
- T12.5: `tests/test_agents/test_reporter.py` → 단위 테스트

### T13: OrchestratorAgent (depends: T9, T10, T11, T12)
- T13.1: `agents/orchestrator.py` → Phase 0→1→2→3 순차 실행
- T13.2: call_agent, glob_expand, validate_files 도구
- T13.3: Sub-agent 호출 관리
- T13.4: Progress 콜백 (Rich)
- T13.5: `tests/test_agents/test_orchestrator.py` → 단위 테스트

### T14: CLI Entry Point (depends: T13)
- T14.1: `main.py` argparse → 파일 경로, 옵션 파싱
- T14.2: 환경 변수 처리 → MIDER_API_KEY, MIDER_API_BASE
- T14.3: Rich Progress Bar → Phase별 진행률
- T14.4: Before/After 터미널 출력 → Critical 이슈 요약
- T14.5: 종료 코드 → 0: 정상, 1: critical 발견, 2: 에러
- T14.6: `tests/test_cli/test_main.py` → 단위 테스트

### T15: Integration Test (depends: T14)
- T15.1: `tests/fixtures/` 샘플 파일 4개 (JS, C, ProC, SQL)
- T15.2: E2E 테스트 → 전체 파이프라인
- T15.3: Exit code 검증
- T15.4: 출력 파일 검증 (3개 JSON)

### T16: 토큰 최적화 (Structure + Function Window) (depends: T11)
- T16.1: `_extract_error_functions()` 유틸리티 구현 → 대상: `mider/agents/` 또는 `mider/tools/utility/`
  - 정적분석 에러 라인 → AST/정규식으로 함수 경계 탐색 → 함수 전체 추출
  - 함수 밖 에러는 에러 주변 ±20줄 추출
  - SQL은 SQL 문(SELECT/INSERT/UPDATE/DELETE) 단위로 추출
- T16.2: `_build_structure_summary()` 유틸리티 구현 → 대상: `mider/agents/` 또는 `mider/tools/utility/`
  - Phase 1 file_context의 imports/calls/patterns + ast-grep 함수 시그니처 + 전역변수
- T16.3: 4개 Analyzer `_build_messages()` 수정 → 대상: `mider/agents/{js,c,proc,sql}_analyzer.py`
  - Error-Focused: `{file_content}` → `{structure_summary}` + `{error_functions}`
  - Heuristic: `{file_content}` → `{file_content_optimized}` (≤500줄 전체, >500줄 head+tail+구조요약)
- T16.4: 8개 프롬프트 템플릿 변수 변경 → 대상: `mider/config/prompts/*_analyzer_*.txt`
  - Error-Focused 4개: `{file_content}` → `{structure_summary}` + `{error_functions}`
  - Heuristic 4개: `{file_content}` → `{file_content_optimized}`
- T16.5: 단위 테스트 → 대상: `tests/test_agents/`, `tests/test_tools/`

### T17: 배포 체크리스트 자동 생성 (depends: T12)
- T17.1: 배포 체크리스트 데이터 정의 → 대상: `mider/tools/utility/deployment_checklist.py`
  - 5개 섹션별 체크리스트 항목을 구조화된 데이터로 정의
  - 섹션 1: 화면 배포 (xml/js), 섹션 2: TP 배포 (.c), 섹션 3: Module 배포 (.c, .h)
  - 섹션 4: Batch 배포 (.pc), 섹션 5: DBIO 배포 (.sql)
- T17.2: 파일 확장자 → 섹션 매핑 로직 → 대상: `mider/tools/utility/deployment_checklist.py`
  - `.js`→섹션1, `.c`→TP/Module판별→섹션2/3, `.h`→섹션3, `.pc`→섹션4, `.sql`→섹션5
  - TP/Module 판별: 첫줄주석(SERVICE→TP, module→Module) > 파일명(뒤에서3번째 t→TP)
- T17.3: DeploymentChecklist Pydantic 스키마 → 대상: `mider/models/report.py`
- T17.4: ReporterAgent 연동 → 대상: `mider/agents/reporter.py`
- T17.5: CLI 출력 + JSON 파일 → 대상: `mider/main.py`
- T17.6: 단위 테스트 → 대상: `tests/test_tools/test_deployment_checklist.py`

## 일정 요약
| Task | 의존성 | 병렬 가능 | 상태 |
|------|--------|----------|------|
| T1~T9 | - | - | ✅ 완료 |
| T10 | T2~T4, T7, T8 | - | ✅ 완료 |
| T11 | T2~T8 | T12와 병렬 | ✅ 완료 |
| T12 | T2, T3, T5, T8 | T11과 병렬 | ✅ 완료 |
| T13 | T9~T12 | - | ✅ 완료 |
| T14 | T13 | - | ✅ 완료 |
| T16 | T11 | - | ✅ 완료 |
| T17 | T12 | T16과 병렬 | 대기 |
| T15 | T14, T16, T17 | - | 대기 (마지막)
