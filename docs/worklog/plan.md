# 작업 계획서

## 개요
Agent 추론 로그 시각화 — 분석 중 의사결정 과정(스캔 결과 → 경로 선택 이유 → 프롬프트 구성 → LLM 응답 → 후처리)을 컬러 dot 기반으로 CLI에 실시간 표시.

## 출력 예시 (목표)

### XML Analyzer (Error-Focused)
```
[Phase 2] ── XMLAnalyzerAgent ────────────────────────────
  ● Parse: 2 dataList (DS_REQR_INFO: 10cols, DS_FAX_INFO: 5cols)     ← cyan
  ● Parse: 29 events, 54 components, 1 duplicate ID                  ← cyan
  ● Detect: btn_save 중복 (916행, 1015행) — trigger × 2              ← red
  ● JS검증: 대응 JS 파일 없음 — 핸들러 교차검증 불가                 ← yellow
  ● Decision: Error-Focused path                                      ← yellow
     ∵ duplicate_ids=1건, missing_handlers=0건, parse_errors=0건
  ● Prompt: xml_analyzer_error_focused (2 dataList, 29 events 포함)   ← blue
     입력 토큰 추정: ~3,200
  ● LLM 호출: gpt-5-mini 요청 중...                                  ← magenta
  ● LLM 응답: 4,682 tokens, 18.2초                                   ← magenta
  ● Parse: LLM JSON 파싱 → 1개 이슈 추출                             ← blue
  ● Validate: AnalysisResult 스키마 검증 통과                         ← blue
  ● Result: 1 issue, 20.7초                                          ← green
     [HIGH] XML-001 중복 컴포넌트 ID 'btn_save' (916행, 1015행)
```

### C Analyzer (2-Pass)
```
[Phase 2] ── CAnalyzerAgent ──────────────────────────────
  ● File: ordss03s0100t81.c (1,200줄, ~12K tokens)                   ← cyan
  ● clang-tidy: 실행 실패 (바이너리 없음)                            ← red
  ● Heuristic: regex 6종 스캔 → 4건 탐지                             ← cyan
     UNINIT_VAR: 2건 (45행, 312행), NULL_DEREF: 1건 (89행), BUFFER: 1건 (156행)
  ● Decision: 2-Pass 전략                                            ← yellow
     ∵ clang-tidy 없음 + 1,200줄(>500) + 위험 패턴 4건
  ● 함수 매핑: 4건 → 3개 함수 (c100: 636줄, c200: 1115줄, c400: 127줄)  ← blue
  ● Pass 1 Prompt: c_prescan (3개 함수 시그니처 + 4건 위험 패턴)     ← blue
  ● Pass 1 LLM: gpt-5-mini 요청 중...                               ← magenta
  ● Pass 1 응답: 2,841 tokens, 5.3초                                 ← magenta
  ● Pass 1 판정: 3개 함수 중 2개 위험 (c100, c400)                   ← yellow
     ∵ c200은 위험 패턴이 주석 내부로 판단 → 제외
  ● Pass 2: 함수별 개별 분석 (2개 병렬)                              ← blue
  ● Pass 2 Prompt[c100]: error_focused (636줄 + clang warnings 없음) ← blue
  ● Pass 2 Prompt[c400]: error_focused (127줄 + UNINIT_VAR 1건)      ← blue
  ● Pass 2 LLM[c100]: gpt-5 요청 중...                              ← magenta
  ● Pass 2 LLM[c400]: gpt-5 요청 중...                              ← magenta
  ● Pass 2 응답[c100]: 3,456 tokens, 8.1초 → 2개 이슈               ← magenta
  ● Pass 2 응답[c400]: 1,230 tokens, 4.2초 → 1개 이슈               ← magenta
  ● Merge: 3개 이슈 합산, issue_id 재번호 (C-001~C-003)             ← blue
  ● Validate: AnalysisResult 스키마 검증 통과                        ← blue
  ● Result: 3 issues, 14.5초                                        ← green
     [CRITICAL] C-001 strcpy 버퍼 오버플로우 (c100:45행)
     [HIGH] C-002 미초기화 변수 svc_cnt (c400:23행)
     [MEDIUM] C-003 malloc 반환값 미검사 (c100:89행)
```

### Phase 0/1 (단일 파일)
```
[Phase 0] ── TaskClassifierAgent ─────────────────────────
  ● 입력: 1개 파일 (xml: ZORDSS03S0100_buggy.xml)                    ← cyan
  ● Decision: LLM 우선순위 보정 skip                                 ← yellow
     ∵ 단일 파일이므로 정렬할 대상 없음
  ● Result: 1 task, 예상 10초                                        ← green

[Phase 1] ── ContextCollectorAgent ───────────────────────
  ● Scan: imports=0, calls=28 (scwin.* handlers), patterns=[event_binding: 29]  ← cyan
  ● Decision: LLM 컨텍스트 보정 skip                                 ← yellow
     ∵ 단일 파일이므로 교차 참조 보정 불필요
  ● Result: 1 file context collected                                  ← green
```

### Phase 3 (Reporter)
```
[Phase 3] ── ReporterAgent ───────────────────────────────
  ● Input: 1 file, 1 issue (CRITICAL:0 HIGH:1 MEDIUM:0 LOW:0)       ← cyan
  ● Decision: 배포 가능 (HIGH < 3, CRITICAL = 0)                    ← yellow
  ● Prompt: reporter (1개 이슈 요약 요청)                            ← blue
  ● LLM 호출: gpt-5-mini 요청 중...                                 ← magenta
  ● LLM 응답: 3,211 tokens, 26.3초                                  ← magenta
  ● Result: issue-list + checklist + summary + deployment-checklist   ← green
```

## Dot 색상 규칙

| 색상 | 의미 | 예시 |
|------|------|------|
| cyan | 입력 데이터 / 스캔 결과 | 파일 정보, 파서 결과, 패턴 탐지 |
| red | 오류 / 경고 / 탐지된 문제 | clang-tidy 실패, 중복 ID 발견 |
| yellow | 의사결정 + 근거(∵) | 경로 선택, LLM skip 판단, 위험 판정 |
| blue | 내부 처리 | 프롬프트 구성, 함수 매핑, JSON 파싱, 스키마 검증 |
| magenta | LLM 호출 / 응답 | 요청 시작, 응답 토큰/시간 |
| green | 최종 결과 | 이슈 수, 소요 시간, 이슈 목록 |

## 완료된 Task
- T1~T25, T19: 전체 기능 구현 완료

## 미완료 (기존)
- T22: clang-tidy + Heuristic 하이브리드 (브랜치 미머지)

## 진행 예정 Task

### T26: Agent 추론 로그 시각화

#### T26.1: ReasoningLogger 유틸 구현 → 대상: `mider/config/reasoning_logger.py`
- `ReasoningLogger` 클래스: 컬러 dot 기반 구조화된 로그 출력
- 로그 메서드: `scan()`, `detect()`, `decision()`, `prompt()`, `llm_request()`, `llm_response()`, `process()`, `validate()`, `result()`
- 각 메서드는 색상 자동 적용 (cyan/red/yellow/blue/magenta/green)
- `decision()`은 `∵` 근거 라인 지원
- `result()`는 이슈 목록 하위 출력 지원
- `phase_header()`: `[Phase N] ── AgentName ───` 헤더 출력
- verbose 모드(-v)일 때만 출력, 기본은 기존 Phase 진행률만
- Rich Console 기반 (기존 logging과 분리)

#### T26.2: OrchestratorAgent에 추론 로그 연동 → 대상: `mider/agents/orchestrator.py`
- ReasoningLogger 인스턴스를 Sub-Agent에 전달
- 각 Phase 시작 시 `phase_header()` 출력
- 단일 파일 skip 시 decision 로그

#### T26.3: Analyzer Agent에 추론 로그 추가 → 대상: `mider/agents/*_analyzer.py` (6개)
- XMLAnalyzerAgent: 파서 결과 scan → 중복 detect → 경로 decision → 프롬프트 prompt → LLM 호출/응답 → 파싱/검증 → result
- CAnalyzerAgent: clang-tidy/heuristic scan → 2-Pass decision → Pass 1/2 LLM → merge → result
- JS/ProC/SQL Analyzer: 동일 패턴 적용
- TaskClassifierAgent: 단일/다중 decision
- ContextCollectorAgent: scan 결과 + LLM skip/호출 decision
- ReporterAgent: 이슈 집계 → 배포 판정 decision → LLM 요약

#### T26.4: CLI 출력 통합 → 대상: `mider/main.py`
- `--verbose`에서 ReasoningLogger 활성화
- 기본 모드: 기존 Phase 진행률 유지
- ReasoningLogger 인스턴스를 OrchestratorAgent에 주입

#### T26.5: 단위 테스트 → 대상: `tests/test_config/test_reasoning_logger.py`
- 각 로그 메서드 출력 형식 검증
- verbose/non-verbose 모드 동작 확인
- phase_header 형식 검증

---

### T15: Integration Test (depends: T26)
- T15.1~T15.4: (기존 계획 유지)

---

## 일정 요약
| Task | 의존성 | 상태 |
|------|--------|------|
| T1~T25, T19 | - | ✅ 완료 |
| T22 | T20 | ⚠️ 브랜치 미머지 |
| T26 | T25 | **다음** — 추론 로그 시각화 |
| T15 | T26 | 대기 (마지막) |
