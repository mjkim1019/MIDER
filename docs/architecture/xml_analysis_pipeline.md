# XML 파일 분석 파이프라인

> WebSquare XML 파일이 입력되었을 때 Mider가 분석을 수행하는 전체 프로세스

---

## 전체 흐름

```
main.py (진입점)
  ↓
OrchestratorAgent.run()
  ├─ Phase 0: 파일 분류 (TaskClassifierAgent)
  ├─ Phase 1: 컨텍스트 수집 (ContextCollectorAgent)
  ├─ Phase 2: XML 분석 (XMLAnalyzerAgent)
  └─ Phase 3: 리포트 생성 (ReporterAgent)
```

---

## Phase 0: 파일 분류

**담당**: `TaskClassifierAgent` (`mider/agents/task_classifier.py`)

1. 파일 확장자(`.xml`)로 언어 감지 → `language: "xml"`
2. `DependencyResolver`로 의존성 분석 (XML 파일은 보통 독립적)
3. `TaskPlanner`로 실행 순서 결정 (위상 정렬)
4. LLM으로 우선순위 조정 (파일 2개 이상일 때)

**출력**: `ExecutionPlan` — sub_tasks 리스트 (task_id, file, language, priority, metadata)

**라우팅**: `OrchestratorAgent`의 `_LANGUAGE_AGENT_MAP`에서 `"xml"` → `XMLAnalyzerAgent`로 매핑

---

## Phase 1: 컨텍스트 수집

**담당**: `ContextCollectorAgent` (`mider/agents/context_collector.py`)

각 XML 파일에 대해 아래 정보를 추출:

| 수집 항목 | 설명 |
|-----------|------|
| 이벤트 핸들러 함수 호출 | `scwin.funcName()` 패턴에서 함수명 추출 |
| 이벤트 바인딩 패턴 | ev:onclick, ev:onchange 등 이벤트 바인딩 수 집계 |

XML 파일은 import/include 관계를 추출하지 않는다.

**출력**: `FileContext` — 이벤트 함수 호출, 패턴 정보

---

## Phase 2: XML 분석 (핵심)

**담당**: `XMLAnalyzerAgent` (`mider/agents/xml_analyzer.py`)

### 아키텍처 개요

```
Step 1: XML 파싱 (XMLParser)
  ├─ dataList + 컬럼 추출
  ├─ 이벤트 바인딩 추출
  ├─ 컴포넌트 ID 추출
  └─ 중복 ID 검사
       ↓
Step 2: JS 교차 검증 (이벤트 핸들러 ↔ JS 함수)
       ↓
Step 3: XML 구조 LLM 분석
       ↓
Step 4: 인라인 JS 추출 → JSAnalyzer 위임
       ↓
Step 5: 이슈 병합 + issue_id 재번호
```

### 2-1. XML 파싱

- **도구**: `XMLParser` (`mider/tools/static_analysis/xml_parser.py`)
- **엔진**: Python `xml.etree.ElementTree`
- **보안**: DOCTYPE/ENTITY 선언이 포함된 XML은 파싱 거부 (XXE/Billion Laughs 방어)

**추출 항목**:

| 항목 | 설명 |
|------|------|
| `data_lists` | `w2:dataList` 요소 — id + 하위 column (id, name, dataType) |
| `events` | 이벤트 바인딩 — element_id, element_tag, event_type, handler, handler_functions |
| `component_ids` | UI 컴포넌트 id 속성 (데이터 정의 내부 column/columnInfo/data는 제외) |
| `duplicate_ids` | 중복 id 목록 (id, count, tags, 원본 XML 라인 번호) |
| `parse_errors` | XML 파싱 오류 메시지 |

**이벤트 핸들러 함수명 추출**:

이벤트 핸들러 문자열에서 함수명을 추출한다:
- `scwin.btn_search_onclick()` → `["btn_search_onclick"]`
- `scwin.fn_init(); scwin.fn_load();` → `["fn_init", "fn_load"]`
- scwin 없는 직접 호출도 매칭 (제어문 키워드 제외)

### 2-2. JS 교차 검증

XML 이벤트 핸들러에 대응하는 JS 함수가 실제로 존재하는지 검증한다.

**JS 파일 탐색 순서**:
1. `{xml_stem}.js` (같은 디렉토리)
2. `{xml_stem}_wq.js` (같은 디렉토리)

**검증 방법**: 대응 JS 파일에서 `\b{func_name}\b` 정규표현식으로 함수명 존재 여부를 확인한다.

**출력**: `{js_file: str | null, missing_handlers: [{function_name, element_id, event_type}, ...]}`

### 2-3. XML 구조 LLM 분석

dataList, 이벤트, 중복 ID, JS 교차 검증 결과를 LLM에 전달하여 XML 구조 이슈를 탐지한다.

- **프롬프트**: `xml_analyzer.txt`
- **모델**: gpt-5-mini

**프롬프트 변수**:

| 변수 | 설명 |
|------|------|
| `file_path` | XML 파일 경로 |
| `datalist_summary` | dataList 요약 (이름 + 컬럼 수) — `build_datalist_summary()`로 생성 |
| `events` | 이벤트 바인딩 목록 JSON |
| `duplicate_ids` | 중복 ID 목록 JSON |
| `missing_handlers` | JS 교차 검증 — 누락 핸들러 목록 JSON |
| `parse_errors` | XML 파싱 오류 JSON |
| `js_file` | 대응 JS 파일 경로 (없으면 "없음") |

**dataList 요약** (`build_datalist_summary()`):

~23K 토큰의 전체 dataList JSON 대신 ~2K 토큰 요약을 생성한다:

```
[dataList 요약] 총 15개
  dl_order: 8 columns
  dl_product: 12 columns
  ...
```

### 2-4. 인라인 JS 추출 → JSAnalyzer 위임

XML의 `<script>` 태그 내 CDATA에 포함된 인라인 JavaScript 코드를 추출하여 `JavaScriptAnalyzerAgent`에 위임한다.

**추출 규칙** (`XMLParser.extract_inline_scripts()`):
- `src=` 속성이 있는 외부 스크립트는 제외
- CDATA 내용이 JS 코드인 블록만 추출 (JS 키워드: `function`, `var`, `let`, `const`, `scwin.`, `return`, `if(`, `{`)
- 한 줄짜리 CDATA도 처리

**위임 과정**:

```
1. 인라인 JS 추출 → 연결된 JS 코드 문자열 + ScriptBlock[] 오프셋 맵
2. 임시 .js 파일 생성
3. JavaScriptAnalyzerAgent.run() 호출
4. 임시 파일 삭제
5. 라인 번호 변환 (JS 라인 → 원본 XML 라인)
6. 파일 경로를 원본 XML로 복원
```

**라인 번호 변환** (`js_line_to_xml_line()`):

`ScriptBlock` 오프셋 맵을 사용하여 추출된 JS의 라인 번호를 원본 XML의 라인 번호로 변환한다:

```
ScriptBlock
├─ xml_start: 원본 XML에서 CDATA 코드 시작 라인 (1-based)
├─ js_start: 추출된 JS에서의 시작 라인 (1-based)
└─ length: 블록 줄 수
```

### 2-5. 이슈 병합

`_merge_issues(xml_issues, js_issues)`:

1. XML 구조 이슈 + 인라인 JS 이슈를 순서대로 병합
2. issue_id를 `XML-001`, `XML-002`, ... 순차 재번호

---

## Phase 3: 리포트 생성

**담당**: `ReporterAgent` (`mider/agents/reporter.py`)

모든 XML 파일의 분석 결과를 통합하여 최종 리포트 생성:

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
├─ issue_id: "XML-001"
├─ category: memory_safety | null_safety | data_integrity | error_handling | security | performance | code_quality
├─ severity: critical | high | medium | low
├─ title: "중복 컴포넌트 ID: btn_search" (한국어)
├─ description: "btn_search ID가 2개 요소에서 중복..." (한국어)
├─ location: {file, line_start, line_end}
├─ fix: {before, after, description}
├─ source: static_analysis | llm | hybrid
├─ static_tool: null
└─ static_rule: null
```

---

## 프롬프트 파일

| 프롬프트 | 용도 |
|---------|------|
| `xml_analyzer.txt` | XML 구조 분석 (dataList, 이벤트, 중복 ID, 누락 핸들러) |
| `js_analyzer.txt` | 인라인 JS 분석 (JSAnalyzer에 위임 시 사용) |

---

## 관련 파일 경로

| 구성 요소 | 파일 경로 |
|-----------|-----------|
| 진입점 | `mider/main.py` |
| 오케스트레이터 | `mider/agents/orchestrator.py` |
| XML 분석 에이전트 | `mider/agents/xml_analyzer.py` |
| JS 분석 에이전트 | `mider/agents/js_analyzer.py` |
| XML 파서 도구 | `mider/tools/static_analysis/xml_parser.py` |
| 토큰 최적화 유틸 | `mider/tools/utility/token_optimizer.py` |
| 분석 결과 모델 | `mider/models/analysis_result.py` |
| 실행 계획 모델 | `mider/models/execution_plan.py` |
| 프롬프트 (XML) | `mider/config/prompts/xml_analyzer.txt` |
| 프롬프트 (JS) | `mider/config/prompts/js_analyzer.txt` |
