# SQL 파일 분석 파이프라인

> SQL 파일이 입력되었을 때 Mider가 분석을 수행하는 전체 프로세스

---

## 전체 흐름

```
main.py (진입점)
  ↓
OrchestratorAgent.run()
  ├─ Phase 0: 파일 분류 (TaskClassifierAgent)
  ├─ Phase 1: 컨텍스트 수집 (ContextCollectorAgent)
  ├─ Phase 2: SQL 분석 (SQLAnalyzerAgent)
  └─ Phase 3: 리포트 생성 (ReporterAgent)
```

---

## Phase 0: 파일 분류

**담당**: `TaskClassifierAgent` (`mider/agents/task_classifier.py`)

1. 파일 확장자(`.sql`)로 언어 감지 → `language: "sql"`
2. `DependencyResolver`로 의존성 분석 (SQL 파일은 보통 독립적)
3. `TaskPlanner`로 실행 순서 결정 (위상 정렬)
4. LLM으로 우선순위 조정 (파일 2개 이상일 때)

**출력**: `ExecutionPlan` — sub_tasks 리스트 (task_id, file, language, priority, metadata)

**라우팅**: `OrchestratorAgent`의 `_LANGUAGE_AGENT_MAP`에서 `"sql"` → `SQLAnalyzerAgent`로 매핑

**Explain Plan 전달**: CLI에서 `--explain-plan` 옵션으로 전달된 파일 경로를 `OrchestratorAgent`가 SQL Analyzer에 `explain_plan_file` 파라미터로 전달한다.

---

## Phase 1: 컨텍스트 수집

**담당**: `ContextCollectorAgent` (`mider/agents/context_collector.py`)

각 SQL 파일에 대해 아래 정보를 추출:

| 수집 항목 | 설명 |
|-----------|------|
| 트랜잭션 패턴 | COMMIT, ROLLBACK, BEGIN TRANSACTION |

SQL 파일은 import/include 관계나 함수 호출을 추출하지 않는다.

**출력**: `FileContext` — 패턴 정보

---

## Phase 2: SQL 분석 (핵심)

**담당**: `SQLAnalyzerAgent` (`mider/agents/sql_analyzer.py`)

### 아키텍처 개요

```
Step 1: 파일 읽기 + 토큰 추정
Step 2: SQL 문법 검증 (sqlparse)
Step 3: 정적 패턴 검색 (AstGrepSearch)
Step 4: Explain Plan 파싱 (옵션)
Step 5: LLM 분석 (Error-Focused / Heuristic 분기)
Step 5.5: 정적 이슈 자동 생성 + LLM 이슈 병합
Step 6: AnalysisResult 생성
```

### 2-1. SQL 문법 검증

- **도구**: `SQLSyntaxChecker` (`mider/tools/static_analysis/sql_syntax_checker.py`)
- **엔진**: `sqlparse` 토큰화 + 커스텀 규칙
- **검사 항목**:

| 검사 | 유형 | 설명 |
|------|------|------|
| 괄호 불일치 | error | 여는/닫는 괄호 매칭 (문자열/주석 내부 제외) |
| 따옴표 미닫힘 | error | 작은따옴표 홀수 감지 ('' 이스케이프 제외) |
| SELECT 문 FROM 누락 | error | FROM 절 없는 SELECT (단순 값 SELECT 제외) |
| UPDATE 문 WHERE 누락 | warning | 전체 행 갱신 위험 |
| DELETE 문 WHERE 누락 | warning | 전체 행 삭제 위험 |

- **Fallback**: sqlparse 파싱 실패 시 정규식 기반 기본 검사로 전환
- **출력**: `{syntax_errors: [...], warnings: [...]}`

### 2-2. 정적 패턴 검색

- **도구**: `AstGrepSearch` (`mider/tools/search/ast_grep_search.py`)
- **검색 패턴 5종**:

| 패턴 | 설명 |
|------|------|
| `select_star` | SELECT * 사용 |
| `function_in_where` | WHERE 절에 함수 호출 (인덱스 억제) |
| `like_wildcard` | LIKE '%...' 선행 와일드카드 |
| `subquery` | 서브쿼리 사용 |
| `or_condition` | OR 조건 사용 |

- **출력**: matches 리스트 (pattern, line, content)
- **테이블명 추출**: FROM/INTO/UPDATE/JOIN 뒤의 테이블명을 자동 추출하여 로그

### 2-3. Explain Plan 파싱 (옵션)

CLI에서 `--explain-plan` 옵션으로 파일 경로를 전달한 경우에만 실행한다.

- **도구**: `ExplainPlanParser` (`mider/tools/utility/explain_plan_parser.py`)
- **지원 형식**:
  - DBMS_XPLAN.DISPLAY_CURSOR 출력
  - EXPLAIN PLAN + SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY) 출력
  - 표 형식 (구분선 '---' 포함)
- **출력**:
  - `steps[]`: 실행 계획 단계별 (id, operation, name, cost, rows, bytes, predicates)
  - `tuning_points[]`: 비효율 오퍼레이션 자동 탐지
  - `formatted_table`: 포맷된 테이블 텍스트

**튜닝 포인트 자동 탐지**:

| 탐지 대상 | severity |
|-----------|----------|
| TABLE ACCESS FULL | 비용 기반 (high/medium) |
| CARTESIAN / MERGE JOIN CARTESIAN | critical |
| INDEX RANGE SCAN (PK, Cost > 100) | high |
| SORT MERGE JOIN | medium |
| 고비용 오퍼레이션 (Cost > 1000) | medium |

### 2-4. LLM 분석 (2가지 경로 분기)

```
문법 에러 또는 정적 패턴 존재?
│
├─ YES → Error-Focused 경로
│          프롬프트: sql_analyzer_error_focused.txt
│          입력: 코드 + 문법에러 + 정적패턴 + FileContext + ExplainPlan
│
└─ NO  → Heuristic 경로
           프롬프트: sql_analyzer_heuristic.txt
           입력: 코드 + ExplainPlan
```

두 경로 모두 단일 LLM 호출 (gpt-5).

**LLM 프롬프트에 전달되는 Explain Plan 데이터**:

| Explain Plan 크기 | 처리 |
|-------------------|------|
| step 100개 이하 (소형) | 전체 테이블 + 튜닝 포인트 요약 |
| step 100개 초과 (대형) | 고비용 step만 필터링 (Cost>=50 또는 TABLE ACCESS/MERGE JOIN/CARTESIAN) + 심각도순 상위 20개 튜닝 포인트 |

토큰 경고: 파일 크기가 ~100K 토큰을 초과하면 LLM context 초과 경고를 로그한다.

### 2-5. 정적 이슈 자동 생성 + LLM 이슈 병합

**정적 이슈 자동 생성** (`_generate_static_issues()`):

Explain Plan의 HIGH/CRITICAL 튜닝 포인트를 LLM 비결정성과 무관하게 **항상** 이슈로 보고한다:

| 탐지 패턴 | severity | issue_id |
|-----------|----------|----------|
| MERGE JOIN CARTESIAN | critical | SQL-S001 |
| PK 인덱스 고비용 INDEX RANGE SCAN | high | SQL-S002 |
| TABLE ACCESS FULL | high | SQL-S003 |

같은 object에 대한 중복은 첫 번째만 유지한다.

**이슈 병합** (`_merge_issues()`):

```
LLM 이슈 + 정적 이슈
  ↓
중복 판단: 정적 이슈의 object 이름 키워드가 LLM 이슈 텍스트에 포함되는가?
  ├─ YES → LLM 이슈 우선 (정적 이슈 제외)
  └─ NO  → LLM 이슈 뒤에 정적 이슈 추가
  ↓
issue_id 재번호: SQL-001, SQL-002, ...
```

키워드 매칭 시 인덱스 접미사(_PK, _N1 등)를 제거하여 베이스 테이블명으로도 매칭한다.

### 2-6. 후처리

#### LLM 응답 source 정규화

LLM이 반환한 이슈의 `source` 필드가 `static_analysis`, `llm`, `hybrid` 중 하나가 아니면 `"llm"`으로 강제 설정한다.

#### Issue ID 부여

- 병합 후 `SQL-001`, `SQL-002`, ... 순차 재번호
- 정적 이슈는 병합 전 `SQL-S001` 형식 (병합 후 재번호)

---

## Phase 3: 리포트 생성

**담당**: `ReporterAgent` (`mider/agents/reporter.py`)

모든 SQL 파일의 분석 결과를 통합하여 최종 리포트 생성:

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
├─ issue_id: "SQL-001"
├─ category: memory_safety | null_safety | data_integrity | error_handling | security | performance | code_quality
├─ severity: critical | high | medium | low
├─ title: "MERGE JOIN CARTESIAN 발생 — JOIN 조건 누락 가능" (한국어)
├─ description: "Explain Plan에서 MERGE JOIN CARTESIAN이..." (한국어)
├─ location: {file, line_start, line_end}
├─ fix: {before, after, description}
├─ source: static_analysis | llm | hybrid
├─ static_tool: "sqlparse" | "explain_plan" | null
└─ static_rule: "missing_from" | null
```

---

## 프롬프트 파일

| 프롬프트 | 용도 | 사용 경로 |
|---------|------|-----------|
| `sql_analyzer_error_focused.txt` | 정적분석 + LLM 하이브리드 심층 분석 | Error-Focused |
| `sql_analyzer_heuristic.txt` | LLM 단독 패턴 분석 | Heuristic |

---

## 관련 파일 경로

| 구성 요소 | 파일 경로 |
|-----------|-----------|
| 진입점 | `mider/main.py` |
| 오케스트레이터 | `mider/agents/orchestrator.py` |
| SQL 분석 에이전트 | `mider/agents/sql_analyzer.py` |
| SQL 문법 검증 도구 | `mider/tools/static_analysis/sql_syntax_checker.py` |
| 패턴 검색 도구 | `mider/tools/search/ast_grep_search.py` |
| Explain Plan 파서 | `mider/tools/utility/explain_plan_parser.py` |
| 분석 결과 모델 | `mider/models/analysis_result.py` |
| 실행 계획 모델 | `mider/models/execution_plan.py` |
| 프롬프트 (Error-Focused) | `mider/config/prompts/sql_analyzer_error_focused.txt` |
| 프롬프트 (Heuristic) | `mider/config/prompts/sql_analyzer_heuristic.txt` |
