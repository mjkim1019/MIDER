# 작업 계획서

## 개요
1차 PoC 기능 확장: (1) SQL 성능개선 강화 — 문법 검증 + Explain Plan 파일 입력 기반 튜닝 포인트 분석, (2) Proframe XML 지원 — WebSquare XML 정적 분석 + JS 교차 검증. 이후 T15 통합 테스트로 마무리.

## 완료된 Task
- T1~T14, T16, T17: Project Scaffold → Data Models → Base Infrastructure → Tools → Prompts → Agents → CLI → 토큰 최적화 → 배포 체크리스트
- T18: SQL 성능개선 강화
- T20: C Heuristic Pre-Scanner (2-Pass 분석)

## 진행 예정 Task

### T21: Pass 2 함수별 개별 LLM 호출 (depends: T20)

2-Pass 분석의 Pass 2에서 선별된 함수를 한 번에 LLM에 보내는 대신,
**함수별로 개별 LLM 호출**하여 대형 함수가 소형 함수 분석을 지배하는 문제를 해결한다.

참조: `docs/issue-log/003-pass2-large-function-dominance.md`

#### T21.1: `_run_two_pass()` 함수별 개별 호출 리팩토링 → 대상: `mider/agents/c_analyzer.py`
- 기존: risky_functions 전체를 한 프롬프트에 묶어 gpt-4o 1회 호출
- 변경: risky_functions를 for-loop으로 순회, 함수별로 코드 추출 + warnings + LLM 호출
- 각 함수별 결과를 합산하여 issues 리스트 반환
- `MIDER_EXCLUDE_FUNCTIONS` 임시 workaround 제거
- issue_id 충돌 방지: 합산 시 C-001부터 재번호 부여

#### T21.2: 함수별 프롬프트 최적화 → 대상: `mider/config/prompts/c_analyzer_error_focused.txt`
- 단일 함수 분석에 맞게 프롬프트 조정 (복수 함수 섹션 순회 지시 제거)
- 구조 요약은 파일 전체 유지 (함수 간 참조 관계 파악용)
- 해당 함수의 warnings만 전달

#### T21.3: asyncio.gather 병렬 호출 (선택) → 대상: `mider/agents/c_analyzer.py`
- 함수별 LLM 호출을 asyncio.gather()로 병렬 실행
- 비용 동일, 시간 단축 (4개 함수 → 직렬 4배 → 병렬 1배)
- rate limit 고려하여 semaphore로 동시 호출 수 제한 (기본 3)

#### T21.4: 단위 테스트 → 대상: `tests/test_agents/test_c_analyzer.py`
- 함수별 개별 호출 검증: mock LLM이 함수 수만큼 호출되는지
- 결과 합산 검증: 여러 함수의 issues가 올바르게 병합되는지
- MIDER_EXCLUDE_FUNCTIONS 제거 확인
- 기존 테스트 호환성 유지

---

### T22: clang-tidy + Heuristic 하이브리드 분석 (depends: T20)

clang-tidy가 있어도 Heuristic Scanner를 **항상 함께 실행**하여
두 결과를 합쳐 분석 커버리지를 극대화한다.

참조: `docs/issue-log/002-clang-tidy-header-limitation.md`

#### T22.1: Error-Focused 경로에 Heuristic Scanner 추가 → 대상: `mider/agents/c_analyzer.py`
- 기존: clang-tidy 있음 → clang-tidy warnings만 사용
- 변경: clang-tidy 있음 → clang-tidy warnings + Heuristic findings 합산
- 중복 제거: 동일 라인 + 유사 패턴은 clang-tidy 우선

#### T22.2: 합산 로직 구현 → 대상: `mider/agents/c_analyzer.py`
- `_merge_warnings()` 메서드: clang-tidy warnings + heuristic findings → 통합 리스트
- 중복 판정: 같은 라인 ±2줄 AND 같은 카테고리 → clang-tidy 결과 우선
- 합산 결과를 Error-Focused 프롬프트의 `{clang_tidy_warnings}` 변수에 전달

#### T22.3: 단위 테스트 → 대상: `tests/test_agents/test_c_analyzer.py`
- clang-tidy + heuristic 합산 검증
- 중복 제거 검증 (같은 라인 경고가 2번 나오지 않음)
- clang-tidy 없을 때 기존 동작 유지 확인

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

### T15: Integration Test (depends: T19, T21, T22)

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
| T18 | T11, T14 | ✅ 완료 |
| T20 | T16 | ✅ 완료 |
| T21 | T20 | 대기 (다음) |
| T22 | T20 | 대기 |
| T19 | T3, T8 | 대기 |
| T15 | T19, T21, T22 | 대기 (마지막) |
