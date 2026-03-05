# 작업 계획서

## 개요
1차 PoC 기능 확장: (1) SQL 성능개선 강화 — 문법 검증 + Explain Plan 파일 입력 기반 튜닝 포인트 분석, (2) Proframe XML 지원 — WebSquare XML 정적 분석 + JS 교차 검증. 이후 T15 통합 테스트로 마무리.

## 완료된 Task
- T1~T14, T16, T17: Project Scaffold → Data Models → Base Infrastructure → Tools → Prompts → Agents → CLI → 토큰 최적화 → 배포 체크리스트

## 진행 예정 Task

### T18: SQL 성능개선 강화 (depends: T11, T14)

#### T18.1: SQL 문법 검증 도구 → 대상: `mider/tools/static_analysis/sql_syntax_checker.py`
- sqlparse 라이브러리로 Oracle SQL 문법 파싱
- 문법 오류 위치(라인), 에러 메시지 반환
- BaseTool 상속, execute(file=...) 인터페이스
- requirements.txt에 sqlparse 추가

#### T18.2: Explain Plan 파서 → 대상: `mider/tools/utility/explain_plan_parser.py`
- Oracle Explain Plan 텍스트 파일 파싱
- Operation, Options, Cost, Rows, Bytes, Time 추출
- Full Table Scan, Cartesian Join, Sort Merge Join 등 비효율 오퍼레이션 탐지
- BaseTool 상속, execute(file=...) 인터페이스

#### T18.3: ExplainPlan Pydantic 스키마 → 대상: `mider/models/analysis_result.py` 또는 신규
- ExplainPlanStep: operation, options, object_name, cost, rows, bytes
- ExplainPlanResult: steps[], warnings[], tuning_points[]
- SQLAnalyzerAgent run()에 explain_plan 파라미터 추가

#### T18.4: SQLAnalyzerAgent 강화 → 대상: `mider/agents/sql_analyzer.py`
- sql_syntax_checker 연동 (문법 오류 → Error-Focused 경로)
- explain_plan_parser 결과를 LLM 프롬프트에 추가
- Error-Focused: 문법 오류 + 정적 패턴 + Explain Plan → 종합 분석
- Heuristic: Explain Plan 있으면 함께 분석

#### T18.5: 프롬프트 템플릿 수정 → 대상: `mider/config/prompts/sql_analyzer_*.txt`
- Error-Focused: `{syntax_errors}`, `{explain_plan}` 변수 추가
- Heuristic: `{explain_plan}` 변수 추가

#### T18.6: CLI --explain-plan 옵션 + 파이프라인 연동
- `mider/main.py`: `--explain-plan` 옵션 추가 (파일 경로, SQL 파일과 1:1 또는 공용)
- `mider/agents/orchestrator.py`: explain_plan 파일 경로를 SQLAnalyzerAgent에 전달
- OrchestratorAgent.run()에 explain_plan 파라미터 추가

#### T18.7: 단위 테스트 → 대상: `tests/`
- sql_syntax_checker 테스트 (정상/오류 SQL)
- explain_plan_parser 테스트 (다양한 Explain Plan 형식)
- SQLAnalyzerAgent 강화 분기 테스트
- CLI --explain-plan 옵션 테스트

---

### T19: Proframe XML 지원 (depends: T3, T8)

#### T19.1: XML 파서/분석 도구 → 대상: `mider/tools/static_analysis/xml_parser.py`
- ElementTree 기반 WebSquare XML 파싱
- 데이터 리스트(w2:dataList), 컬럼 정의(w2:column) 추출
- 이벤트 바인딩(ev:on*) 추출 → JS 함수명 목록
- 컴포넌트 ID 추출 및 중복 검사
- BaseTool 상속, execute(file=...) 인터페이스

#### T19.2: XMLAnalyzerAgent 구현 → 대상: `mider/agents/xml_analyzer.py`
- BaseAgent 상속, gpt-4o-mini (fallback gpt-4o)
- XML 파서 결과 + JS 교차 검증 (이벤트 핸들러 존재 여부)
- Error-Focused: 파서 오류/중복 ID/핸들러 누락 시
- Heuristic: 오류 없어도 구조 검증
- AnalysisResult 반환

#### T19.3: XML 프롬프트 템플릿 → 대상: `mider/config/prompts/`
- `xml_analyzer_error_focused.txt`: 파서 오류 + 교차 검증 결과 기반
- `xml_analyzer_heuristic.txt`: XML 구조 전체 검증

#### T19.4: 파이프라인 연동
- `mider/agents/task_classifier.py`: `.xml` 확장자 → "xml" 언어 인식
- `mider/agents/context_collector.py`: XML 파일 컨텍스트 수집 (이벤트 바인딩 → JS 함수 매핑)
- `mider/agents/orchestrator.py`: `_LANGUAGE_AGENT_MAP`에 "xml" 추가, `_validate_files`에 `.xml` 추가

#### T19.5: CLI/배포 체크리스트 XML 지원
- `mider/main.py`: `ext_to_lang`에 `.xml` 추가
- `mider/tools/utility/deployment_checklist.py`: XML → 화면 배포 섹션(섹션 1) 매핑
- `print_file_list()`에 XML 표시

#### T19.6: 단위 테스트 → 대상: `tests/`
- xml_parser 테스트 (WebSquare XML 파싱, ID 중복, 이벤트 추출)
- XMLAnalyzerAgent 테스트 (Error-Focused/Heuristic, JS 교차 검증)
- 파이프라인 연동 테스트 (TaskClassifier, Orchestrator)
- 배포 체크리스트 XML 섹션 테스트

---

### T15: Integration Test (depends: T18, T19)

#### T15.1: 샘플 파일 5개 → 대상: `tests/fixtures/`
- JS, C, ProC, SQL, XML 각 1개씩 (기존 + XML 추가)

#### T15.2: E2E 테스트 → 대상: `tests/test_integration/`
- 전체 파이프라인 (OrchestratorAgent) 모의 실행
- XML + JS 교차 검증 시나리오
- SQL + Explain Plan 시나리오

#### T15.3: Exit code 검증
- 0: 정상 완료, 1: Critical 발견, 2: 파일 에러, 3: LLM 에러

#### T15.4: 출력 파일 검증 (4개 JSON)
- issue-list.json, checklist.json, summary.json, deployment-checklist.json

---

## 일정 요약
| Task | 의존성 | 상태 |
|------|--------|------|
| T1~T14, T16, T17 | - | ✅ 완료 |
| T18 | T11, T14 | 대기 |
| T19 | T3, T8 | 대기 (T18과 병렬 가능) |
| T15 | T18, T19 | 대기 (마지막) |
