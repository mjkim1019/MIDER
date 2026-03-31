# C 파일 분석 파이프라인

> C 파일이 입력되었을 때 Mider가 분석을 수행하는 전체 프로세스

---

## 전체 흐름

```
main.py (진입점)
  ↓
OrchestratorAgent.run()
  ├─ Phase 0: 파일 분류 (TaskClassifierAgent)
  ├─ Phase 1: 컨텍스트 수집 (ContextCollectorAgent)
  ├─ Phase 2: C 분석 (CAnalyzerAgent)
  └─ Phase 3: 리포트 생성 (ReporterAgent)
```

---

## Phase 0: 파일 분류

**담당**: `TaskClassifierAgent` (`mider/agents/task_classifier.py`)

1. 파일 확장자(`.c`, `.h`)로 언어 감지 → `language: "c"`
2. `DependencyResolver`로 `#include` 의존성 분석
3. `TaskPlanner`로 실행 순서 결정 (위상 정렬)
4. LLM으로 우선순위 조정 (파일 2개 이상일 때)

**출력**: `ExecutionPlan` — sub_tasks 리스트 (task_id, file, language, priority, metadata)

**라우팅**: `OrchestratorAgent`의 `_LANGUAGE_AGENT_MAP`에서 `"c"` → `CAnalyzerAgent`로 매핑

---

## Phase 1: 컨텍스트 수집

**담당**: `ContextCollectorAgent` (`mider/agents/context_collector.py`)

각 C 파일에 대해 아래 정보를 추출:

| 수집 항목 | 설명 |
|-----------|------|
| `#include` 의존성 | 헤더 파일 참조 관계 |
| 함수 호출 관계 | 어떤 함수가 어떤 함수를 호출하는지 |
| 코드 패턴 | error_handling, logging, memory_management 패턴 감지 |

**출력**: `FileContext` — 의존성, 함수 호출, 패턴 정보

---

## Phase 2: C 분석 (핵심)

**담당**: `CAnalyzerAgent` (`mider/agents/c_analyzer.py`)

### 2-1. 정적 분석 실행

C 파일마다 두 가지 정적 분석을 **항상** 실행:

#### clang-tidy

- **도구**: `ClangTidyRunner` (`mider/tools/static_analysis/clang_tidy_runner.py`)
- **체크 규칙**: `-*,clang-analyzer-*,bugprone-*,-bugprone-branch-clone`
- **동작**: stub 헤더 생성 (`StubHeaderGenerator`) → clang-tidy 실행 → 경고 파싱
- **헤더 에러 필터링**: `file not found`, `unknown type name` 등 헤더 누락 에러는 제외
- **출력**: 경고 리스트 (severity, message, line, check). 헤더 에러만 있으면 None

#### CHeuristicScanner

- **도구**: `CHeuristicScanner` (`mider/tools/static_analysis/c_heuristic_scanner.py`)
- **6개 탐지 패턴**:

| 패턴 ID | 설명 | 예시 |
|---------|------|------|
| UNINIT_VAR | 미초기화 지역 변수 | `int cnt;` (초기값 없음) |
| UNSAFE_FUNC | 위험 함수 사용 | `strcpy`, `sprintf`, `gets` |
| BOUNDED_FUNC | 경계 함수 사용 | `strncpy`, `memcpy`, `memset` |
| MALLOC_NO_CHECK | malloc 후 NULL 미검사 | `p = malloc(n);` 후 검사 없음 |
| BUFFER_INDEX | 변수 인덱스 배열 접근 | `arr[i]` (범위 검사 없음) |
| FORMAT_STRING | 비리터럴 포맷 스트링 | `printf(var)` |

- **출력**: findings 리스트 (pattern_id, line, content, function, severity)

### 2-2. LLM 분석 (3가지 경로 분기)

파일 크기와 정적 분석 결과에 따라 분기:

```
파일 크기 & 정적분석 결과 확인
│
├─ >500줄 (대형 파일)
│  └─ 2-Pass 분석
│
├─ ≤500줄 + clang/scanner 결과 있음
│  └─ Error-Focused 분석
│
└─ ≤500줄 + clang 결과 없음
   └─ Heuristic 분석
```

#### 경로 A: 2-Pass 분석 (대형 파일)

대형 파일은 전체를 LLM에 넣으면 프롬프트를 압도하므로, 2단계로 나눠 분석:

**Pass 1 — 위험 함수 선별**
- 프롬프트: `c_prescan_fewshot.txt`
- 모델: mini (gpt-5-mini) — 빠른 필터링 용도
- 입력: 전체 함수 시그니처 요약 (`build_all_functions_summary()`) + clang 경고 + scanner 결과
- 출력: `risky_functions` 리스트 (위험도 높은 함수명)
- Fallback: LLM이 위험 함수를 못 찾으면 scanner가 탐지한 함수로 대체

**Pass 2 — 함수별 심층 분석**
- 프롬프트: `c_analyzer_error_focused.txt`
- 모델: primary (gpt-5)
- 선별된 함수마다 개별 LLM 호출 (최대 3개 동시 실행)
- 각 함수의 코드 + 해당 함수의 clang 경고만 전달
- 출력: 함수별 issue 리스트

#### 경로 B: Error-Focused 분석 (소형 파일 + 정적분석 있음)

- 프롬프트: `c_analyzer_error_focused.txt`
- 모델: primary (gpt-5)
- 단일 LLM 호출 — 파일 전체 + clang 경고 + scanner 결과 통합 전달
- 정적 분석 결과를 힌트로 LLM이 심층 분석

#### 경로 C: Heuristic 분석 (소형 파일 + 정적분석 없음)

- 프롬프트: `c_analyzer_heuristic.txt`
- 모델: primary (gpt-5)
- 단일 LLM 호출 — 최적화된 파일 내용 + scanner 결과 전달
- clang-tidy 없이 scanner + LLM으로 패턴 기반 분석

### 2-3. 후처리

#### 중복 제거 (3단계)

1. **프레임워크 안전 패턴 제거**: Proframe 단일 스레드 환경에서 불필요한 thread safety/동시성 경고, 프레임워크가 보장하는 NULL 검사, 코드 스타일 제안, 전역 변수 공유 경고, 구조체 부분 초기화 경고 등 (title 키워드 매칭으로 자동 제거)
2. **키워드 그룹 병합**: strncpy null termination 관련 이슈, ix 미초기화 관련 이슈 등을 하나로 병합
3. **변수+카테고리 병합**: 동일 변수에 대한 동일 카테고리 이슈 병합

#### Issue ID 부여

- 형식: `C-001`, `C-002`, ...
- 파일 내에서 순차적으로 번호 부여

---

## Phase 3: 리포트 생성

**담당**: `ReporterAgent` (`mider/agents/reporter.py`)

모든 C 파일의 분석 결과를 통합하여 최종 리포트 생성:

| 출력 파일 | 내용 |
|-----------|------|
| issue_list | 전체 이슈 목록 (상세 정보 포함) |
| summary | 심각도별/카테고리별/언어별 통계 |
| checklist | 배포 전 확인 체크리스트 |
| deployment_checklist | 위험도 평가 |

---

## Issue 스키마

각 이슈는 아래 구조로 저장 (`mider/models/analysis_result.py`):

```
Issue
├─ issue_id: "C-001"
├─ category: memory_safety | null_safety | data_integrity | error_handling | security | performance | code_quality
├─ severity: critical | high | medium | low
├─ title: "미초기화 변수 svc_cnt 사용" (한국어)
├─ description: "svc_cnt가 초기화 없이 루프에서..." (한국어)
├─ location: {file, line_start, line_end}
├─ fix: {before, after, description}
├─ source: static_analysis | llm | hybrid
├─ static_tool: "clang-tidy" | null
└─ static_rule: "bugprone-..." | null
```

---

## 프롬프트 파일

| 프롬프트 | 용도 | 사용 경로 |
|---------|------|-----------|
| `c_prescan_fewshot.txt` | Pass 1 위험 함수 선별 | 2-Pass (대형 파일) |
| `c_analyzer_error_focused.txt` | 정적분석 + LLM 하이브리드 심층 분석 | 2-Pass Pass 2 / Error-Focused |
| `c_analyzer_heuristic.txt` | LLM 단독 패턴 분석 | Heuristic |

---

## 관련 파일 경로

| 구성 요소 | 파일 경로 |
|-----------|-----------|
| 진입점 | `mider/main.py` |
| 오케스트레이터 | `mider/agents/orchestrator.py` |
| C 분석 에이전트 | `mider/agents/c_analyzer.py` |
| clang-tidy 도구 | `mider/tools/static_analysis/clang_tidy_runner.py` |
| 휴리스틱 스캐너 | `mider/tools/static_analysis/c_heuristic_scanner.py` |
| stub 헤더 생성 | `mider/tools/static_analysis/stub_header_generator.py` |
| 토큰 최적화 유틸 | `mider/tools/utility/token_optimizer.py` |
| 분석 결과 모델 | `mider/models/analysis_result.py` |
| 실행 계획 모델 | `mider/models/execution_plan.py` |
| 프롬프트 (Pass 1) | `mider/config/prompts/c_prescan_fewshot.txt` |
| 프롬프트 (Error-Focused) | `mider/config/prompts/c_analyzer_error_focused.txt` |
| 프롬프트 (Heuristic) | `mider/config/prompts/c_analyzer_heuristic.txt` |
