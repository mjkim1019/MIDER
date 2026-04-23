# 체크리스트

- [x] T1: Project Scaffold
  - [x] T1.1: 디렉토리 구조 생성
  - [x] T1.2: __init__.py 파일 생성
  - [x] T1.3: requirements.txt
  - [x] T1.4: settings.yaml
  - [x] T1.5: lint-configs
  - [x] T1.6: conftest.py
  - [x] T1.7: .gitignore 업데이트
- [x] T2: Data Models
  - [x] T2.1: execution_plan.py
  - [x] T2.2: file_context.py
  - [x] T2.3: analysis_result.py
  - [x] T2.4: report.py
  - [x] T2.5: __init__.py re-export
  - [x] T2.6: 단위 테스트
- [x] T3: Base Infrastructure
  - [x] T3.1: base_agent.py
  - [x] T3.2: base_tool.py
  - [x] T3.3: llm_client.py
  - [x] T3.4: prompt_loader.py
  - [x] T3.5: logging_config.py
  - [x] T3.6: 단위 테스트
- [x] T4: File I/O & Search Tools
  - [x] T4.1: file_reader.py
  - [x] T4.2: grep.py
  - [x] T4.3: glob_tool.py
  - [x] T4.4: ast_grep_search.py
  - [x] T4.5: 단위 테스트
- [x] T5: Utility Tools
  - [x] T5.1: sql_extractor.py
  - [x] T5.2: dependency_resolver.py
  - [x] T5.3: task_planner.py
  - [x] T5.4: checklist_generator.py
  - [x] T5.5: 단위 테스트
- [x] T6: Static Analysis Tools
  - [x] T6.1: eslint_runner.py
  - [x] T6.2: clang_tidy_runner.py
  - [x] T6.3: proc_runner.py
  - [x] T6.4: 단위 테스트
- [x] T7: LSP Tool
  - [x] T7.1: lsp_client.py
  - [x] T7.2: 단위 테스트
- [x] T8: Prompt Templates
  - [x] T8.1: orchestrator.txt
  - [x] T8.2: task_classifier.txt
  - [x] T8.3: context_collector.txt
  - [x] T8.4: js_analyzer (error_focused + heuristic)
  - [x] T8.5: c_analyzer (error_focused + heuristic)
  - [x] T8.6: proc_analyzer (error_focused + heuristic)
  - [x] T8.7: sql_analyzer (error_focused + heuristic)
  - [x] T8.8: reporter.txt
- [x] T9: TaskClassifierAgent (Phase 0)
  - [x] T9.1: task_classifier.py 구현
  - [x] T9.2: dependency_resolver 연동
  - [x] T9.3: LLM 우선순위 보정
  - [x] T9.4: ExecutionPlan 반환
  - [x] T9.5: 단위 테스트
- [x] T10: ContextCollectorAgent (Phase 1)
  - [x] T10.1: context_collector.py 기본 구조
  - [x] T10.2: Import/Include 추출 + 호출 관계 매핑
  - [x] T10.3: 공통 패턴 탐지
  - [x] T10.4: LLM 컨텍스트 보정 + FileContext 반환
  - [x] T10.5: 단위 테스트
- [x] T11: Phase 2 Analyzer Agents
  - [x] T11.1: js_analyzer.py
  - [x] T11.2: c_analyzer.py
  - [x] T11.3: proc_analyzer.py
  - [x] T11.4: sql_analyzer.py
  - [x] T11.5: 단위 테스트 4개
- [x] T12: ReporterAgent (Phase 3)
  - [x] T12.1: reporter.py 구현
  - [x] T12.2: checklist_generator 연동
  - [x] T12.3: 3개 JSON 출력
  - [x] T12.4: RiskAssessment 생성
  - [x] T12.5: 단위 테스트
- [x] T13: OrchestratorAgent
  - [x] T13.1: orchestrator.py 구현
  - [x] T13.2: call_agent, glob_expand, validate_files
  - [x] T13.3: Sub-agent 호출 관리
  - [x] T13.4: Progress 콜백
  - [x] T13.5: 단위 테스트
- [x] T14: CLI Entry Point
  - [x] T14.1: main.py argparse
  - [x] T14.2: 환경 변수 처리
  - [x] T14.3: Rich Progress Bar
  - [x] T14.4: Before/After 출력
  - [x] T14.5: 종료 코드
  - [x] T14.6: 단위 테스트
- [x] T16: 토큰 최적화 (Structure + Function Window)
  - [x] T16.1: _extract_error_functions() 구현
  - [x] T16.2: _build_structure_summary() 구현
  - [x] T16.3: 4개 Analyzer _build_messages() 수정
  - [x] T16.4: 8개 프롬프트 템플릿 변수 변경
  - [x] T16.5: 단위 테스트
- [x] T17: 배포 체크리스트 자동 생성
  - [x] T17.1: 배포 체크리스트 데이터 정의
  - [x] T17.2: 파일 확장자 → 섹션 매핑 로직
  - [x] T17.3: DeploymentChecklist Pydantic 스키마
  - [x] T17.4: ReporterAgent 연동
  - [x] T17.5: CLI 출력 + JSON 파일
  - [x] T17.6: 단위 테스트
- [x] T18: SQL 성능개선 강화
  - [x] T18.1: SQL 문법 검증 도구 (sqlparse)
  - [x] T18.2: Explain Plan 파서
  - [x] T18.3: ExplainPlan Pydantic 스키마 (별도 모델 생략 — ToolResult.data dict 패턴)
  - [x] T18.4: SQLAnalyzerAgent 강화
  - [x] T18.5: 프롬프트 템플릿 수정
  - [x] T18.6: CLI --explain-plan 옵션 + 파이프라인 연동
  - [x] T18.7: 단위 테스트
- [x] T20: C Heuristic Pre-Scanner (2-Pass 분석)
  - [x] T20.1: C Heuristic Scanner Tool (regex 6종 패턴)
  - [x] T20.2: Pass 1 프롬프트 (few-shot 선별)
  - [x] T20.3: CAnalyzerAgent 2-Pass 흐름 구현
  - [x] T20.4: c_analyzer_heuristic 프롬프트에 few-shot 예시 추가
  - [x] T20.5: 단위 테스트
- [x] T21: Pass 2 함수별 개별 LLM 호출
  - [x] T21.1: `_run_two_pass()` 함수별 개별 호출 리팩토링
  - [x] T21.2: 함수별 프롬프트 최적화
  - [x] T21.3: asyncio.gather 병렬 호출
  - [x] T21.4: 단위 테스트
- [x] T22: clang-tidy + Heuristic 하이브리드 분석 (T31에 흡수)
  - [x] T22.1: Error-Focused 경로에 Heuristic Scanner 추가 → T31.2+T31.3
  - [x] T22.2: 합산 로직 구현 → T31.3 (scanner_findings 병합)
  - [x] T22.3: 단위 테스트 → T31.6
- [x] T19: Proframe XML 지원
  - [x] T19.1: XML 파서/분석 도구
  - [x] T19.2: XMLAnalyzerAgent 구현
  - [x] T19.3: XML 프롬프트 템플릿
  - [x] T19.4: 파이프라인 연동 (TaskClassifier, ContextCollector, Orchestrator)
  - [x] T19.5: CLI/배포 체크리스트 XML 지원
  - [x] T19.6: 단위 테스트
- [x] T23: SQL 분석 파이프라인 검증 및 테스트 (T18 확장)
  - [x] T23.1: 텍스트 덤프 파싱 단위 테스트
  - [x] T23.2: SQL 대형 파일 안전장치 + 로깅
  - [x] T23.3: 프롬프트 개선 (인덱스 힌트 유도)
  - [x] T23.4: 전체 파이프라인 E2E 테스트
- [x] T24: Explain Plan 튜닝 포인트 → 정적 이슈 자동 생성 (이슈 #004)
  - [x] T24.1: 튜닝 포인트 → 정적 이슈 변환 메서드 (_generate_static_issues)
  - [x] T24.2: LLM + 정적 이슈 병합 로직 (_merge_issues)
  - [x] T24.3: 단위 테스트
- [x] T25: XML 중복 ID 스코프 개선 (이슈 #005 Phase 3)
  - [x] T25.1: `_extract_component_ids`에서 데이터 정의 요소 제외
  - [x] T25.2: 테스트 수정 및 추가
  - [x] T25.3: 이슈 로그 업데이트
  - [x] T25.4: gpt-5/gpt-5-mini 업그레이드 + settings_loader 도입
  - [x] T25.5: 중복 ID 라인 번호 추출 + 프롬프트 개선
  - [x] T25.6: 단일 파일 Phase 0/1 LLM skip
- [x] T26: Agent 추론 로그 시각화
  - [x] T26.1: ReasoningLogger 유틸 구현
  - [x] T26.2: OrchestratorAgent에 추론 로그 연동
  - [x] T26.3: Analyzer Agent에 추론 로그 추가
  - [x] T26.4: CLI 출력 통합
  - [x] T26.5: 단위 테스트
- [x] T27: clang-tidy 헤더 누락 시 Heuristic/2-Pass fallback
  - [x] T27.1: clang-tidy 결과에서 헤더 에러 필터링
  - [x] T27.2: 추론 로그 추가
  - [x] T27.3: 단위 테스트
- [x] T28: clang-tidy Level 1 저가치 경고 필터링 (이슈 #002 확장)
  - [x] T28.1: Level 1/Level 2 분류 로직 구현
  - [x] T28.2: 이슈 #002 로그 업데이트
  - [x] T28.3: 추론 로그 개선
  - [x] T28.4: 단위 테스트
- [x] T29: C 분석 이슈 후처리 중복 제거
  - [x] T29.1: `_deduplicate_issues()` 구현
  - [x] T29.2: 이슈 로그 작성
  - [x] T29.3: 단위 테스트
- [x] T30: Pro*C Heuristic Scanner (2-Pass)
  - [x] T30.1: ProCHeuristicScanner Tool 구현
  - [x] T30.2: ProCAnalyzerAgent에 Scanner 연동
  - [x] T30.3: Pro*C 프롬프트 Few-shot 추가
  - [x] T30.4: Scanner 단위 테스트
  - [x] T30.5: Agent 통합 테스트
- [x] T31: CAnalyzer 통합 개선 (T22 흡수)
  - [x] T31.1: build_all_functions_summary() 구현 (token_optimizer.py)
  - [x] T31.2: Scanner 항상 실행 + 라우팅 변경 (>500→2-Pass, ≤500+clang→EF, ≤500→Heuristic)
  - [x] T31.3: Error-Focused에 scanner findings 병합 (≤500줄+clang)
  - [x] T31.4: Heuristic(≤500줄)에 scanner findings 추가
  - [x] T31.5: 2-Pass에 clang 데이터 + all_functions_summary 통합 (>500줄)
  - [x] T31.6: 단위 테스트 (6개 추가, 기존 27개 호환)
- [x] T32: JS 긴 파일 전략 — ESLint + 전체 코드 단일 호출
  - [x] T32.1: ESLint 에러 포함 + 전체 코드 전달 방식 구현
  - [x] T32.2: 프롬프트 통합 (Error-Focused/Heuristic → 단일)
- [x] T33-old: ProC 유틸리티 (글로벌 컨텍스트, 커서 맵, SQL 함수 매핑, Pass 1 프롬프트)
- [x] T33: ProC 분석 재설계 — 전체 코드 전달 + 스마트 그룹핑
  - [x] T33.1: 프롬프트 통합 (Error-Focused/Heuristic → 단일) → proc_analyzer.txt
  - [x] T33.2: 함수 패턴 분류기 (`classify_proc_functions`) → token_optimizer.py
  - [x] T33.3: 토큰 기반 전달 분기 (`_decide_delivery_mode`) → proc_analyzer.py
  - [x] T33.4: 단일 호출 경로 (`_run_single_call`) → proc_analyzer.py
  - [x] T33.5: 그룹핑 호출 경로 (`_run_grouped_call`) → proc_analyzer.py
  - [x] T33.6: `run()` 리팩토링 (통일 파이프라인) → proc_analyzer.py
  - [x] T33.7: 단위 테스트
- [x] T34: XML 분석 강화 — 인라인 JS 추출 + JS Analyzer 위임 + dataList 요약
  - [x] T34.1: XMLParser에 `<script>` CDATA 추출 + 라인 오프셋 맵
  - [x] T34.2: dataList 요약 함수 (`build_datalist_summary`)
  - [x] T34.3: XML Analyzer 재구조화 (JS Analyzer 위임 + 라인 변환 + 병합)
  - [x] T34.4: XML 프롬프트 통합 (2개 → 1개)
  - [x] T34.5: 단위 테스트
- ~~T35: 주석 처리 전략 검토~~ (v1 범위 제외)
- [x] T36: Agent 표준 로그 개선 — 언어별 동작 차이 가시화
  - [x] T36.1: 분석 경로 선택 로그 추가 (5개 Analyzer)
  - [x] T36.2: 도구 실행 결과 로그 추가 (5개 Analyzer)
  - [x] T36.3: 후처리 로그 추가 (C dedup, SQL merge)
  - [x] T36.4: 단위 테스트 (caplog 검증)
- [x] T15: Integration Test
  - [x] T15.1: 샘플 파일 5개 (JS, C, ProC, SQL, XML)
  - [x] T15.2: E2E 테스트
  - [x] T15.3: Exit code 검증
  - [x] T15.4: 출력 파일 검증 (4개 JSON)

---

## v1 릴리스 정리

- [x] T40: 미사용 파일 정리
  - [x] T40.1: 미사용 ProC 프롬프트 삭제
  - [x] T40.2: 프롬프트 개수 테스트 수정
- [x] T41: README v1 리라이트
  - [x] T41.1: XML 지원 추가 + 모델명 업데이트
  - [x] T41.2: 아키텍처 & 분석 전략 섹션 추가
  - [x] T41.3: 사용자 매뉴얼 섹션 추가
- [x] T43: 시스템 아키텍처 문서 (docs/architecture/)
  - [x] T43.1: system_overview.md — 전체 시스템 구조
  - [x] T43.2: js_analysis_pipeline.md — JS 분석 파이프라인
  - [x] T43.3: proc_analysis_pipeline.md — ProC 분석 파이프라인
  - [x] T43.4: sql_analysis_pipeline.md — SQL 분석 파이프라인
  - [x] T43.5: xml_analysis_pipeline.md — XML 분석 파이프라인
  - [x] T43.6: c_analysis_pipeline.md 최신화
- [ ] T42: 버전 1.0.0 릴리스 (depends: T40, T41, T43)
  - [ ] T42.1: 버전 범프 (0.1.0 → 1.0.0)
  - [ ] T42.2: 로컬 브랜치 정리 (38개)
  - [ ] T42.3: 원격 브랜치 정리
  - [ ] T42.4: v1.0.0 태그 + GitHub Release

---

## 배포용 실행파일 환경 구축

- [x] T44: 고정 입력 폴더 기반 파일 경로 처리
  - [x] T44.1: get_base_dir() + resolve_input_files() + main() 수정
  - [x] T44.2: 기존 테스트 호환성 확인 및 수정
- [x] T45: Windows 실행파일 빌드 스크립트
  - [x] T45.1: mider.spec PyInstaller spec 파일
  - [x] T45.2: scripts/build_exe.py 빌드 스크립트
  - [x] T45.3: .env.example 업데이트
- [x] T46: 사용자 매뉴얼
  - [x] T46.1: docs/USER_MANUAL.md 작성

---

## AICA API 전환

- [ ] T47: LLM Client AICA API 전환
  - [ ] T47.1: llm_client.py — httpx 기반 AICA API 클라이언트 구현
  - [ ] T47.2: 모델명 매핑 + settings.yaml 업데이트
  - [ ] T47.3: 단위 테스트 수정
- [ ] T48: 환경 변수 및 CLI 업데이트
  - [ ] T48.1: main.py validate_api_key() AICA 방식 변경
  - [ ] T48.2: .env.example AICA 환경 변수로 변경
  - [ ] T48.3: settings.yaml api 섹션 업데이트
- [ ] T49: CI/빌드/문서 업데이트
  - [ ] T49.1: build-windows-exe.yml secrets 변경
  - [ ] T49.2: build_exe.py 안내 메시지 업데이트
  - [ ] T49.3: USER_MANUAL.md API 관련 업데이트
  - [ ] T49.4: 기존 테스트 호환성 확인

---

## SSO 인증 연동

- [x] T50: SSO 인증 모듈 구현
  - [x] T50.1: sso_auth.py — SSOAuthenticator 클래스 (Selenium 로그인, 세션 캐싱, 만료 감지)
  - [x] T50.2: 단위 테스트 (test_sso_auth.py)
- [x] T51: LLM Client AICA 응답 파싱 수정 + SSO 연동
  - [x] T51.1: _chat_aica() 응답 파싱 수정 (token.data → choices[0].message.content) + app_env 추가
  - [x] T51.2: SSO user_id 연동 (SSOAuthenticator → payload user_id)
  - [x] T51.3: SSO 만료 감지 + 자동 재인증
  - [x] T51.4: 단위 테스트 수정 (test_llm_client.py)
- [x] T52: CLI --sso 옵션 및 설정 업데이트
  - [x] T52.1: main.py — --sso CLI 옵션 + validate_api_key() SSO 분기
  - [x] T52.2: settings.yaml — SSO 설정 섹션 추가
  - [x] T52.3: requirements.txt — selenium 의존성 추가
  - [x] T52.4: .env.example + .gitignore SSO 관련 업데이트
- [x] T53: 파일 탐색 개선 — input 유지 + workspace 재귀 검색 추가
  - [x] T53.1: resolve_input_files() — base_dir rglob 검색 단계 추가 (input 유지)
  - [x] T53.2: 단위 테스트 추가 (7개)

---

## C Analyzer 스마트 라우팅

- [x] T54: C Analyzer 스마트 라우팅 (토큰 + 함수 크기 기반)
  - [x] T54.1: `_decide_c_delivery_mode()` 분기 함수 구현
  - [x] T54.2: `run()` 경로 분기 변경 (500줄 하드코딩 → 토큰+함수크기)
  - [x] T54.3: 단위 테스트 (균일→single, 편차→per_function, 기존 호환성)

---

## memset sizeof 타입 불일치 탐지

- [x] T55: memset sizeof 타입 불일치 탐지
  - [x] T55.1: Scanner `MEMSET_SIZE_MISMATCH` 패턴 추가
  - [x] T55.2: LLM 프롬프트에 memset 타입 불일치 체크 + few-shot 추가
  - [x] T55.3: 단위 테스트 (불일치 탐지, 정상 미탐지, 구조체 멤버 제외)

---

## 인터랙티브 UX 개선

- [x] T56: 인터랙티브 Explain Plan 프롬프트
  - [x] T56.1: `prompt_for_explain_plan()` + `main()` 연동
  - [x] T56.2: 단위 테스트 (SQL 포함→질문, SQL 미포함→미질문, Enter→None)

---

## 우선순위 요약

| 순위 | Task | 피처 | 상태 | 비고 |
|------|------|------|------|------|
| **P0 (최우선)** | **T71 (PII 전처리 강화)** | **F** | **미착수** | SOC 반복 테스트 블로커 |
| P0 | T64 (외부 경로) | A | 미착수 | 독립 |
| P0 | T65 (Skills + Navigator) | A | 미착수 | **T72와 동시 진행** |
| P0 | T72 (이슈 타입 분류) | G | 미착수 | **T65와 동시, T70과 reporter 통합** |
| P0 | T70 (Reporter 속도) | E | 미착수 | T72와 reporter 부분 통합 |
| P1 | T57 (YAML 축소) | A | 미착수 | — |
| P1 | T58 (Scanner 리팩토링) | A | 미착수 | — |
| P1 | T73 (Header 의존성) | G | 미착수 | depends: T72. OOB 근본 해결 |
| P2 | T66 (문서) | A | 미착수 | — |
| P2 | T68 (Orphan 정리) | A | 미착수 | — |
| P2 | T59 (서명 + 배포) | A | 미착수 | — |
| P3 | T60~T63 | B | 미착수 | 병렬 가능 |

### 착수 순서 권장

1. **T71** (단독 최우선)
2. **T64** 기반 + **T70** 병렬
3. **T65 + T72** 동시 진행 (Skills frontmatter에 issue_type 통합)
4. T57 → T58 → **T73** (OOB 근본 해결)
5. T66 → T68 → T59 (정리·배포)
6. 피처 B는 별도 일정 병행

### 충돌 조정 규칙 (요약)

- **T65 + T72** → 프롬프트/스키마 공유. Skill frontmatter에 `issue_type` 필드
- **T70 + T72** → reporter.py 공동 작업. 템플릿에 `# 🔴 오류` / `# 🟡 개선사항` 분리
- **T65.3 + T73** → Skills(framework별) + T73(general fallback). 역할 분담
- **T58 + T68** → T58.5 패턴 상수 제거, T68.1 전수 검증. 순서 엄수
- **T73.4 + T60** → JSON 캐시 → SQLite 마이그레이션 경로

---

## 피처 F (신규, 최우선): PII 전처리 강화

**배경**: 실측 AICA 로그에서 `PASSPORT(NT0000074)`, `EMAIL(@skbroadband.com)`, `PHONE(1600-2000)` 등이 필터에 걸려 재시도/Connection error 발생. 로컬 PIDScanner가 놓치는 패턴을 보강하여 AICA 전송 전에 선마스킹.

### T71: PII 전처리 강화 — P0 (최우선)

- [x] T71.1: PIDScanner 패턴 확장 (여권 1~2자, 대표번호 1XXX, 이메일 broad, 안심번호 0507)
- [ ] T71.2: LLMClient 호출 전 로컬 선탐지 + 선마스킹 파이프라인 (llm_client.py 또는 base_agent.py)
- [ ] T71.3: AICA 에러 로그 분리 (PII 필터 / 콘텐츠 필터 / 네트워크)
- [x] T71.4: 통신사 식별자 패턴 (IMSI/IMEI/ICCID) + Luhn 체크
- [x] T71.5: Secret/API key 스캐너 (AWS/GitHub/JWT/Google/Stripe/Hardcoded PW/DB URL) — `secret_scanner.py` 신규
- [x] T71.6: 로마자 한글 이름 휴리스틱 (주요 성씨 50개 + 3~5자 영문)
- [ ] T71.7: 단위 테스트 + 실측 검증 (실측 3건 재현 + AICA 재시도 0 확인)

---

## 피처 A (재설계): LLM-first 룰 시스템

**통합**: 구 피처 A(룰 외부화) + C(Skills) + D(ProFrame) → 단일 피처. Scanner Navigator 강등, Skills를 판단 1차 소스로.

### T64: 외부 리소스 경로 레이어 (기반) — P0

- [ ] T64.1: `resource_path.py` 신설 (환경변수 > exe옆 > 번들 fallback)
- [ ] T64.2: `prompt_loader.py` 리팩토링 (resource_path 사용)
- [ ] T64.3: `rule_loader.py` / `skill_loader.py` resource_path 통합
- [ ] T64.4: `mider.spec` 업데이트 (rules + skills 번들, overlay 주석)
- [ ] T64.5: `scripts/export_default_resources.py` (기본 리소스 추출)
- [ ] T64.6: 단위 테스트 (fallback + 환경변수 우선순위)

### T65: Skill 포맷 + 로더 + Navigator 강등 (핵심) — P0 (depends: T64)

- [ ] T65.1: Skill 파일 포맷 정의 + `_SCHEMA.md`
- [ ] T65.2: `SkillLoader` 구현 (frontmatter 파싱, graceful degradation)
- [ ] T65.3: 초기 Skill 작성
  - C 기본 3개: UNSAFE_FUNC / UNINIT_VAR / MEMSET_SIZE_MISMATCH
  - **ProFrame 4개** (구 T67 흡수): PROFRAME_A000_INIT / PROFRAME_DBIO_RECCNT / PROFRAME_LEN_CONSTANT / PROFRAME_CTX_ALLOCATED
  - JS/SQL 3개: XSS_INNERHTML / EVAL_USAGE / SQL_INJECTION_RISK
- [ ] T65.4: Scanner Navigator 강등 (`ScannerFinding` → `NavigationHint`, 단독 보고 경로 제거)
- [ ] T65.5: 5개 Analyzer few-shot 자동 주입 (`_build_messages()` + 토큰 예산 관리)
- [ ] T65.6: `_REMOVE_KEYWORDS` 보강 (c_analyzer.py, issue_merger.py)
- [ ] T65.7: 오답 예시 → post_check 자동 변환 (선택, 간단 케이스)
- [ ] T65.8: 단위/통합 테스트 + 실측 검증 (ordsb0100010t01.c 오탐률 비교)

### T57: 룰 YAML 스키마 + 로더 (스코프 축소) — P1 (depends: T65)

- [ ] T57.1: Rule 모델 정의 (severity/post_check 제거, navigation 필드만)
- [ ] T57.2: RuleLoader 구현 (내장 + 외부 병합)
- [ ] T57.3: 기본 룰 YAML 생성 (navigation 키워드만)
- [ ] T57.4: 단위 테스트

### T58: Scanner 리팩토링 (하드코딩 → YAML 로드) — P1 (depends: T57)

- [ ] T58.1: CHeuristicScanner 리팩토링
- [ ] T58.2: JSHeuristicScanner 리팩토링 (상태머신 코드 유지)
- [ ] T58.3: ProCHeuristicScanner 리팩토링 (루프/파일 추적 유지)
- [ ] T58.4: AstGrepSearch 리팩토링
- [ ] T58.5: 기존 테스트 호환성 확인 + 단위 테스트 + dead pattern 상수 완전 제거 검증

### T66: 문서화 — P2 (depends: T65, T58)

- [ ] T66.1: 아키텍처 문서 (`docs/architecture/llm_first_rules.md`)
- [ ] T66.2: USER_MANUAL 커스텀 Skill/룰 섹션 + MIDER_DEV_MODE 사용법
- [x] T66.3: 이슈 로그 010 (llm-first-rule-redesign) — 작성 완료

### T68: Orphan 파일/코드 정리 (신규) — P2 (depends: T65, T58, T66)

- [ ] T68.1: dead import / 미사용 상수 전수 스캔 (vulture, ruff F401/F841)
- [ ] T68.2: 구 Scanner 테스트 fixture 정리 (NavigationHint 검증으로 변경 또는 제거)
- [ ] T68.3: 프롬프트 템플릿 내 Skill 이관 섹션 삭제 (c_analyzer_*.txt, c_prescan_fewshot.txt)
- [ ] T68.4: 문서 업데이트 (TECH_SPEC.md, DATA_SCHEMA.md — 구 Scanner 역할 → Navigator)
- [ ] T68.5: docs/worklog 내 완료 계획 아카이브 (`docs/archive/` 필요 시 생성)
- [ ] T68.6: git grep 기반 deprecated 심볼 참조 0 확인 (ScannerFinding, 구 _PATTERNS 등)

### T59: PyInstaller + 서명 + dev_mode — P2 (depends: T58)

- [ ] T59.1: `mider.spec` 데이터 파일 번들 (rules + skills + 공개키)
- [ ] T59.2: `settings.yaml` rules_dir / skills_dir 설정
- [ ] T59.3: Ed25519 서명 시스템 (개인키 운영자만, exe에 공개키 번들)
- [ ] T59.4: `MIDER_DEV_MODE=1` 환경변수 (서명 검증 skip, SOC 반복 테스트)
- [ ] T59.5: `scripts/sign_resources.py` (개인키 서명 스크립트)
- [ ] T59.6: 단위 테스트 (서명 검증, dev_mode 우회)

---

## 피처 E (신규): Reporter 속도 개선

### T70: Reporter 속도 개선 — P0 (피처 A와 병렬)

**목표**: Phase 3 Reporter 20초 → 3~5초

- [ ] T70.1: Reporter 프로파일링 (LLM 호출 수, 호출별 시간, 출력 토큰 측정)
- [ ] T70.2: 템플릿 기반 결정적 섹션 분리 (`_build_deterministic_sections()`) — 이슈 테이블/severity 카운트/배포 체크리스트
- [ ] T70.3: LLM 호출 범위 축소 — Executive Summary 내러티브만 (출력 토큰 max 400)
- [ ] T70.4: Reporter 모델 다운그레이드 옵션 (settings.yaml reporter 전용 모델, 기본 gpt-5-mini)
- [ ] T70.5: 병렬 LLM 호출 (Executive Summary / RiskAssessment / 권고안 asyncio.gather)
- [ ] T70.6: LLM skip 조건 (이슈 0건 또는 low severity only → 템플릿만 출력)
- [ ] T70.7: 단위 테스트 + 실측 (before/after 시간 + 품질 회귀 확인)

---

## 피처 G (신규): 이슈 품질 관리

**배경**: Mider가 "개선사항"(strcpy→strlcpy 권장, 매직 넘버 등)을 "오류"로 보고하여 진짜 오류가 파묻힘. OOB는 헤더 정보 부족으로 LLM이 추측성 오탐.

### T72: 이슈 타입 분류 강화 — P0 (T65와 동시 진행)

- [ ] T72.1: AnalysisResult.Issue 스키마에 `issue_type` 필드 추가 (error|suggestion|info)
- [ ] T72.2: 5개 Analyzer 프롬프트에 분류 기준 + 예시 추가 ("애매하면 suggestion")
- [ ] T72.3: Skill frontmatter에 `issue_type` 필드 추가 (T65와 통합)
- [ ] T72.4: Reporter 분리 출력 (T70과 통합, error primary / suggestion secondary)
- [ ] T72.5: CLI `--include-suggestions` 옵션 (기본 off)
- [ ] T72.6: issue_merger.py — issue_type 필드 보존·전파
- [ ] T72.7: 단위 테스트 + 실측 (보고 이슈 수 before/after)

### T73: Header 의존성 해결 — P1 (depends: T72)

- [ ] T73.1: Include 파서 (include_resolver.py 신규)
- [ ] T73.2: 심볼 의존성 추적 (LLM 1-shot으로 외부 심볼 목록 추출 + 헤더 매칭)
- [ ] T73.3: 인터랙션 프롬프트 (CLI) — 트리거 3조건 AND 만족 시에만
- [ ] T73.4: 세션 캐시 (`.mider_cache/header_decisions.json` — T60 완료 후 SQLite 마이그레이션)
- [ ] T73.5: Analyzer 프롬프트 조건부 지시 (헤더 미제공 시 OOB 보고 금지)
- [ ] T73.6: CLI 플래그 (`--headers`, `--assume-headers-missing`, `--no-interactive`)
- [ ] T73.7: 단위 테스트 + 실측 검증 (헤더 제공/미제공 OOB 정확도)

---

## 피처 B: 구조분석 캐싱 + 누적 학습

- [ ] T60: StructureStore 인프라 (SQLite) — P3
  - [ ] T60.1: 구조 캐싱 스키마 정의 (`models/structure_store.py`)
  - [ ] T60.2: StructureStore 구현 (`tools/utility/structure_store.py`)
  - [ ] T60.3: 단위 테스트
- [ ] T61: 파이프라인 연동 — P3 (depends: T60)
  - [ ] T61.1: Phase 1 → StructureStore 자동 저장
  - [ ] T61.2: Phase 2 → 이전 분석 컨텍스트 프롬프트 주입
  - [ ] T61.3: Phase 3 → 이력 비교 보고 (delta)
  - [ ] T61.4: 단위 테스트
- [ ] T62: CLI + 관리 — P3 (depends: T61)
  - [ ] T62.1: CLI 명령 (cache status/clear/show)
  - [ ] T62.2: settings.yaml cache 섹션
  - [ ] T62.3: --no-cache CLI 옵션
  - [ ] T62.4: 단위 테스트
- [ ] T63: 벡터 DB 확장 (2단계, 선택적) — P3 (depends: T62)
  - [ ] T63.1: ChromaDB + sentence-transformers 통합
  - [ ] T63.2: 유사 코드 이슈 검색 → 프롬프트 주입
  - [ ] T63.3: AICA 임베딩 API 옵션
  - [ ] T63.4: 단위 테스트
