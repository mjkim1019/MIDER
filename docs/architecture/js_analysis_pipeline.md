# JavaScript 파일 분석 파이프라인

> JavaScript 파일이 입력되었을 때 Mider가 분석을 수행하는 전체 프로세스

---

## 전체 흐름

```
main.py (진입점)
  ↓
OrchestratorAgent.run()
  ├─ Phase 0: 파일 분류 (TaskClassifierAgent)
  ├─ Phase 1: 컨텍스트 수집 (ContextCollectorAgent)
  ├─ Phase 2: JS 분석 (JavaScriptAnalyzerAgent)
  └─ Phase 3: 리포트 생성 (ReporterAgent)
```

---

## Phase 0: 파일 분류

**담당**: `TaskClassifierAgent` (`mider/agents/task_classifier.py`)

1. 파일 확장자(`.js`)로 언어 감지 → `language: "javascript"`
2. `DependencyResolver`로 `import`/`require` 의존성 분석
3. `TaskPlanner`로 실행 순서 결정 (위상 정렬)
4. LLM으로 우선순위 조정 (파일 2개 이상일 때)

**출력**: `ExecutionPlan` — sub_tasks 리스트 (task_id, file, language, priority, metadata)

**라우팅**: `OrchestratorAgent`의 `_LANGUAGE_AGENT_MAP`에서 `"javascript"` → `JavaScriptAnalyzerAgent`로 매핑

---

## Phase 1: 컨텍스트 수집

**담당**: `ContextCollectorAgent` (`mider/agents/context_collector.py`)

각 JS 파일에 대해 아래 정보를 추출:

| 수집 항목 | 설명 |
|-----------|------|
| import/require | `import ... from '...'` 또는 `require('...')` 의존성 |
| 함수 호출 관계 | 어떤 함수가 어떤 함수를 호출하는지 |
| 코드 패턴 | error_handling (try/catch, null 체크), logging (console.*) |

**출력**: `FileContext` — 의존성, 함수 호출, 패턴 정보

---

## Phase 2: JS 분석 (핵심)

**담당**: `JavaScriptAnalyzerAgent` (`mider/agents/js_analyzer.py`)

### 2-1. 정적 분석 실행

#### ESLint

- **도구**: `ESLintRunner` (`mider/tools/static_analysis/eslint_runner.py`)
- **설정**: `mider/resources/lint-configs/.eslintrc.json`
- **동작**: portable node + ESLint 바이너리 실행 → JSON 출력 파싱
- **타임아웃**: 60초
- **출력**: errors/warnings 리스트 (severity, message, line, rule)
- **실패 시**: None 반환, LLM만으로 분석 진행

### 2-2. LLM 분석 (단일 경로)

JS 분석은 파일 크기에 관계없이 **전체 코드 단일 LLM 호출**로 수행한다.

```
파일 전체 코드 + ESLint 결과 + FileContext
  ↓
js_analyzer.txt 프롬프트
  ↓
LLM (gpt-5) 단일 호출
  ↓
issues[] (JSON)
```

#### 프롬프트 구성

`_build_messages()` 메서드에서 프롬프트를 구성한다:

| 항목 | 내용 |
|------|------|
| system | "JavaScript/TypeScript 보안 및 품질 분석 전문가. JSON 형식 응답." |
| user | `js_analyzer.txt` 프롬프트에 아래 변수를 주입 |

**프롬프트 변수**:

| 변수 | 설명 |
|------|------|
| `file_path` | 분석 대상 파일 경로 |
| `file_content` | 파일 전체 코드 |
| `eslint_results` | ESLint errors/warnings JSON (없으면 "ESLint 결과 없음") |
| `file_context` | Phase 1 FileContext JSON (없으면 "컨텍스트 정보 없음") |

### 2-3. 후처리

#### Issue ID 부여

- 형식: `JS-001`, `JS-002`, ...
- LLM이 JSON 응답에서 직접 부여

#### AnalysisResult 생성

- 토큰 추정: `(prompt 길이 + response 길이) // 4`
- Pydantic `AnalysisResult.model_validate()`로 스키마 검증

---

## Phase 3: 리포트 생성

**담당**: `ReporterAgent` (`mider/agents/reporter.py`)

모든 JS 파일의 분석 결과를 통합하여 최종 리포트 생성:

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
├─ issue_id: "JS-001"
├─ category: memory_safety | null_safety | data_integrity | error_handling | security | performance | code_quality
├─ severity: critical | high | medium | low
├─ title: "innerHTML을 통한 XSS 취약점" (한국어)
├─ description: "사용자 입력을 innerHTML에 직접..." (한국어)
├─ location: {file, line_start, line_end}
├─ fix: {before, after, description}
├─ source: static_analysis | llm | hybrid
├─ static_tool: "eslint" | null
└─ static_rule: "no-eval" | null
```

---

## 프롬프트 파일

| 프롬프트 | 용도 |
|---------|------|
| `js_analyzer.txt` | 전체 코드 + ESLint 결과 통합 분석 |

---

## 관련 파일 경로

| 구성 요소 | 파일 경로 |
|-----------|-----------|
| 진입점 | `mider/main.py` |
| 오케스트레이터 | `mider/agents/orchestrator.py` |
| JS 분석 에이전트 | `mider/agents/js_analyzer.py` |
| ESLint 도구 | `mider/tools/static_analysis/eslint_runner.py` |
| ESLint 설정 | `mider/resources/lint-configs/.eslintrc.json` |
| 분석 결과 모델 | `mider/models/analysis_result.py` |
| 실행 계획 모델 | `mider/models/execution_plan.py` |
| 프롬프트 | `mider/config/prompts/js_analyzer.txt` |
