# Mider 시스템 아키텍처 개요

> 폐쇄망 소스코드 분석 CLI의 전체 시스템 구조, Agent/Tool 구성, 데이터 흐름

---

## 전체 흐름

```
CLI (main.py)
  │  --files *.pc *.c *.js *.sql *.xml
  │  --output ./output
  │  --explain-plan plan.txt
  │  --verbose
  ↓
OrchestratorAgent.run()
  ├─ Phase 0: 파일 분류          (TaskClassifierAgent)
  │    └─ 출력: ExecutionPlan
  ├─ Phase 1: 컨텍스트 수집       (ContextCollectorAgent)
  │    └─ 출력: FileContext
  ├─ Phase 2: 코드 분석           (언어별 AnalyzerAgent)
  │    └─ 출력: AnalysisResult[]
  └─ Phase 3: 리포트 생성         (ReporterAgent)
       └─ 출력: issue-list / checklist / summary / deployment-checklist
```

### CLI 진입점

`mider/main.py`의 `main()` 함수가 진입점이다.

1. `.env` 로드 → API 키 검증 (Azure / OpenAI)
2. 모델 결정: CLI 인자 > `MIDER_MODEL` 환경변수 > `settings.yaml`
3. `OrchestratorAgent` 생성 → `run(files=..., explain_plan_file=...)` 호출
4. 결과를 JSON 파일로 출력 + 터미널 이슈 출력

### 종료 코드

| 코드 | 의미 |
|------|------|
| 0 | 정상 완료, Critical 없음 |
| 1 | 정상 완료, Critical 있음 (배포 불가) |
| 2 | 파일 입력 오류 |
| 3 | LLM API 오류 |

---

## Agent 목록 (9개)

| # | Agent | Phase | 역할 | 모델 |
|---|-------|-------|------|------|
| 1 | `OrchestratorAgent` | 전체 | 파이프라인 조율, Phase 0~3 순차 실행 | gpt-5 |
| 2 | `TaskClassifierAgent` | 0 | 파일 분류, 의존성 분석, 실행 계획 수립 | gpt-5-mini |
| 3 | `ContextCollectorAgent` | 1 | import/include, 함수 호출, 코드 패턴 수집 | gpt-5-mini |
| 4 | `JavaScriptAnalyzerAgent` | 2 | ESLint + LLM JS 분석 | gpt-5 |
| 5 | `CAnalyzerAgent` | 2 | clang-tidy + CHeuristicScanner + LLM C 분석 | gpt-5 |
| 6 | `ProCAnalyzerAgent` | 2 | proc + SQLExtractor + Scanner + LLM ProC 분석 | gpt-5 |
| 7 | `SQLAnalyzerAgent` | 2 | sqlparse + AstGrepSearch + ExplainPlan + LLM SQL 분석 | gpt-5 |
| 8 | `XMLAnalyzerAgent` | 2 | XMLParser + 인라인 JS → JSAnalyzer 위임 | gpt-5-mini |
| 9 | `ReporterAgent` | 3 | 이슈 통합, 체크리스트, 배포 판정, 리포트 생성 | gpt-4.1-mini |

### 언어 → Analyzer 라우팅

`OrchestratorAgent`의 `_LANGUAGE_AGENT_MAP`에서 매핑:

```
"javascript" → JavaScriptAnalyzerAgent
"c"          → CAnalyzerAgent
"proc"       → ProCAnalyzerAgent
"sql"        → SQLAnalyzerAgent
"xml"        → XMLAnalyzerAgent
```

### BaseAgent 공통 기능

모든 Agent는 `BaseAgent(ABC)`를 상속한다.

- **LLM 호출**: `call_llm(messages, json_mode=True)` — JSON Mode 기본
- **재시도**: 최대 3회 지수 백오프 재시도 (2^attempt초 대기)
- **Fallback**: 기본 모델 실패 시 fallback 모델로 전환
- **Reasoning Logger**: 추론 과정 시각화 (verbose 모드)

---

## Tool 목록 (16개)

### 파일 I/O (file_io/)

| Tool | 파일 | 설명 |
|------|------|------|
| `FileReader` | `file_reader.py` | 파일 읽기 (UTF-8/EUC-KR) |

### 검색 (search/)

| Tool | 파일 | 설명 |
|------|------|------|
| `GlobTool` | `glob_tool.py` | 파일 패턴 매칭 (glob) |
| `Grep` | `grep.py` | 텍스트 검색 |
| `AstGrepSearch` | `ast_grep_search.py` | SQL 정적 패턴 검색 (select_star, function_in_where 등) |

### 정적 분석 (static_analysis/)

| Tool | 파일 | 설명 |
|------|------|------|
| `ESLintRunner` | `eslint_runner.py` | JavaScript ESLint 실행 |
| `ClangTidyRunner` | `clang_tidy_runner.py` | C clang-tidy 실행 |
| `StubHeaderGenerator` | `stub_header_generator.py` | clang-tidy용 가짜 헤더 생성 |
| `CHeuristicScanner` | `c_heuristic_scanner.py` | C 위험 패턴 7종 스캔 |
| `ProcRunner` | `proc_runner.py` | Oracle proc 프리컴파일러 실행 |
| `ProCHeuristicScanner` | `proc_heuristic_scanner.py` | Pro*C 위험 패턴 4종 스캔 |
| `SQLSyntaxChecker` | `sql_syntax_checker.py` | SQL 문법 검증 (sqlparse) |
| `XMLParser` | `xml_parser.py` | WebSquare XML 구조 파싱 |

### 유틸리티 (utility/)

| Tool | 파일 | 설명 |
|------|------|------|
| `DependencyResolver` | `dependency_resolver.py` | 파일 간 의존성 분석 (include/import) |
| `TaskPlanner` | `task_planner.py` | 토폴로지 정렬 기반 실행 계획 |
| `SQLExtractor` | `sql_extractor.py` | Pro*C에서 EXEC SQL 블록 추출 |
| `ExplainPlanParser` | `explain_plan_parser.py` | Oracle Explain Plan 텍스트 파싱 |
| `ChecklistGenerator` | `checklist_generator.py` | 이슈 기반 체크리스트 자동 생성 |
| `DeploymentChecklistGenerator` | `deployment_checklist.py` | 배포 체크리스트 생성 (파일 유형별) |
| `TokenOptimizer` (함수 모음) | `token_optimizer.py` | 토큰 최적화 유틸리티 |

---

## 데이터 흐름

```
Phase 0                Phase 1              Phase 2              Phase 3
┌──────────┐     ┌──────────────┐     ┌───────────────┐     ┌──────────────┐
│Execution │────→│ FileContext   │────→│AnalysisResult │────→│    Report     │
│  Plan    │     │              │     │   (파일별)    │     │   (4개 JSON) │
└──────────┘     └──────────────┘     └───────────────┘     └──────────────┘

 sub_tasks[]       file_contexts[]       issues[]            issue_list
 dependencies      imports/calls         severity            checklist
 priority          patterns              location            summary
 metadata          common_patterns       fix                 deployment_checklist
```

### Phase 0 출력: ExecutionPlan

```
ExecutionPlan
├─ sub_tasks: SubTask[]
│   ├─ task_id: "task_1"
│   ├─ file: "/path/to/file.c" (절대경로)
│   ├─ language: "c" | "javascript" | "proc" | "sql" | "xml"
│   ├─ priority: 1 (높음) ~ N (낮음)
│   └─ metadata: FileMetadata (file_size, line_count, last_modified)
├─ dependencies: DependencyGraph
│   ├─ edges: DependencyEdge[] (source, target, type)
│   ├─ has_circular: bool
│   └─ warnings: str[]
├─ total_files: int
└─ estimated_time_seconds: int
```

### Phase 1 출력: FileContext

```
FileContext
├─ file_contexts: SingleFileContext[]
│   ├─ file: str
│   ├─ language: str
│   ├─ imports: ImportInfo[] (statement, resolved_path, is_external)
│   ├─ calls: CallInfo[] (function_name, line, target_file)
│   └─ patterns: PatternInfo[] (pattern_type, description, line)
├─ dependencies: DependencyGraph (Phase 0에서 전달)
└─ common_patterns: {pattern_type: count}
```

### Phase 2 출력: AnalysisResult (파일별)

```
AnalysisResult
├─ task_id: str
├─ file: str
├─ language: str
├─ agent: str
├─ issues: Issue[]
│   ├─ issue_id: "JS-001" | "C-001" | "PC-001" | "SQL-001" | "XML-001"
│   ├─ category: memory_safety | null_safety | data_integrity |
│   │            error_handling | security | performance | code_quality
│   ├─ severity: critical | high | medium | low
│   ├─ title: str (한국어)
│   ├─ description: str (한국어)
│   ├─ location: {file, line_start, line_end}
│   ├─ fix: {before, after, description}
│   ├─ source: static_analysis | llm | hybrid
│   ├─ static_tool: str | null
│   └─ static_rule: str | null
├─ analysis_time_seconds: float
├─ llm_tokens_used: int
└─ error: str | null
```

### Phase 3 출력: Report (4개 JSON)

| 출력 파일 | 스키마 | 내용 |
|-----------|--------|------|
| `{prefix}issue-list.json` | `IssueList` | 전체 이슈 목록 (심각도순 정렬, 수정 제안 포함) |
| `{prefix}checklist.json` | `Checklist` | 배포 전 확인 항목 (Critical/High 이슈 기반) |
| `{prefix}summary.json` | `Summary` | 분석 메타데이터 + 통계 + 배포 위험도 평가 |
| `{prefix}deployment-checklist.json` | `DeploymentChecklist` | 파일 유형별 배포 절차 체크리스트 |

파일명 접두사: `{첫번째파일명}_{YYYYMMDDHHMM}_` (예: `calc_202603311430_`)

### 배포 위험도 판정 규칙

```
Critical > 0         → CRITICAL (배포 차단)
High >= 3            → HIGH     (배포 차단)
High >= 1 & High < 3 → MEDIUM   (배포 가능, 수정 권고)
그 외                → LOW      (배포 가능)
```

---

## 설정 파일

### settings.yaml (`mider/config/settings.yaml`)

| 섹션 | 설명 |
|------|------|
| `llm.primary_model` | 기본 모델 (gpt-5) |
| `llm.fallback_model` | Fallback 모델 (gpt-5-mini) |
| `llm.mini_model` | 경량 모델 (gpt-5-mini, Pass 1 선별용) |
| `llm.agents.*` | Agent별 model/fallback/temperature |
| `static_analysis.*` | ESLint/clang-tidy/proc 설정 |
| `output` | 출력 디렉토리 |

### 프롬프트 파일 (`mider/config/prompts/`)

| 프롬프트 | 사용 Agent |
|---------|-----------|
| `task_classifier.txt` | TaskClassifierAgent (Phase 0) |
| `context_collector.txt` | ContextCollectorAgent (Phase 1) |
| `js_analyzer.txt` | JavaScriptAnalyzerAgent |
| `c_prescan_fewshot.txt` | CAnalyzerAgent (Pass 1) |
| `c_analyzer_error_focused.txt` | CAnalyzerAgent (Pass 2 / Error-Focused) |
| `c_analyzer_heuristic.txt` | CAnalyzerAgent (Heuristic) |
| `proc_prescan.txt` | ProCAnalyzerAgent (Pass 1) |
| `proc_analyzer.txt` | ProCAnalyzerAgent (Pass 2 / 단일 / 그룹) |
| `sql_analyzer_error_focused.txt` | SQLAnalyzerAgent (Error-Focused) |
| `sql_analyzer_heuristic.txt` | SQLAnalyzerAgent (Heuristic) |
| `xml_analyzer.txt` | XMLAnalyzerAgent |
| `reporter.txt` | ReporterAgent (Phase 3) |

---

## 관련 파일 경로

| 구성 요소 | 파일 경로 |
|-----------|-----------|
| CLI 진입점 | `mider/main.py` |
| 오케스트레이터 | `mider/agents/orchestrator.py` |
| BaseAgent | `mider/agents/base_agent.py` |
| Agent 구현 | `mider/agents/*.py` |
| Tool 구현 | `mider/tools/**/*.py` |
| 데이터 스키마 | `mider/models/*.py` |
| 설정 | `mider/config/settings.yaml` |
| 프롬프트 | `mider/config/prompts/*.txt` |
| 설정 로더 | `mider/config/settings_loader.py` |
| LLM 클라이언트 | `mider/config/llm_client.py` |
