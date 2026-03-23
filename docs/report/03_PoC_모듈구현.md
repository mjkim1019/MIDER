# 3단계: PoC 모듈 구현

## 1. 구현 범위 (1차 PoC)

### 전체 파이프라인

```
CLI (main.py)
  --files --explain-plan --output --model --verbose --version
    │
    ▼
OrchestratorAgent
  Phase 0 → Phase 1 → Phase 2 → Phase 3
    │          │          │          │
    ▼          ▼          ▼          ▼
  Task      Context    JS/C/ProC  Reporter
  Classifier Collector SQL/XML    Agent
  Agent      Agent    Analyzers
```

### 구현 완료 모듈

| 구분 | 모듈 | 파일 | 설명 |
|------|------|------|------|
| **진입점** | CLI | `main.py` | argparse, Rich 출력, 종료 코드 |
| **Agent** | BaseAgent | `agents/base_agent.py` | ABC, LLM 재시도/fallback, ReasoningLogger |
| | OrchestratorAgent | `agents/orchestrator.py` | Phase 0→1→2→3 조율, Analyzer 캐싱 |
| | TaskClassifierAgent | `agents/task_classifier.py` | 언어 식별, ExecutionPlan 생성 |
| | ContextCollectorAgent | `agents/context_collector.py` | import/include 추출, FileContext 생성 |
| | JavaScriptAnalyzerAgent | `agents/js_analyzer.py` | ESLint + LLM 2경로 |
| | CAnalyzerAgent | `agents/c_analyzer.py` | clang-tidy + Heuristic 3경로 |
| | ProCAnalyzerAgent | `agents/proc_analyzer.py` | proc + HeuristicScanner + LLM |
| | SQLAnalyzerAgent | `agents/sql_analyzer.py` | sqlparse + ExplainPlan + 정적이슈 + LLM 병합 |
| | XMLAnalyzerAgent | `agents/xml_analyzer.py` | XMLParser + JS 핸들러 검증 + LLM |
| | ReporterAgent | `agents/reporter.py` | 4개 JSON 리포트 생성 |
| **Model** | ExecutionPlan | `models/execution_plan.py` | SubTask, FileMetadata, DependencyGraph |
| | FileContext | `models/file_context.py` | ImportInfo, CallInfo, PatternInfo |
| | AnalysisResult | `models/analysis_result.py` | Issue, Location, CodeFix |
| | Report | `models/report.py` | IssueList, Checklist, Summary, DeploymentChecklist |
| **Tool** | FileReader | `tools/file_io/file_reader.py` | 파일 읽기 |
| | Grep | `tools/search/grep.py` | 패턴 검색 |
| | GlobTool | `tools/search/glob_tool.py` | 와일드카드 확장 |
| | AstGrepSearch | `tools/search/ast_grep_search.py` | AST 기반 구조 검색 |
| | ESLintRunner | `tools/static_analysis/eslint_runner.py` | ESLint 실행 |
| | ClangTidyRunner | `tools/static_analysis/clang_tidy_runner.py` | clang-tidy 실행 (homebrew 자동 탐색) |
| | ProcRunner | `tools/static_analysis/proc_runner.py` | Oracle proc 실행 |
| | StubHeaderGenerator | `tools/static_analysis/stub_header_generator.py` | clang-tidy용 stub 헤더 자동 생성 |
| | CHeuristicScanner | `tools/static_analysis/c_heuristic_scanner.py` | C 위험 패턴 regex 6종 |
| | ProCHeuristicScanner | `tools/static_analysis/proc_heuristic_scanner.py` | Pro*C 위험 패턴 regex |
| | SQLSyntaxChecker | `tools/static_analysis/sql_syntax_checker.py` | sqlparse 문법 검증 |
| | XMLParser | `tools/static_analysis/xml_parser.py` | WebSquare XML 파싱 + XXE 방어 |
| | LSPClient | `tools/lsp/lsp_client.py` | 심볼 정의/참조 탐색 |
| | SQLExtractor | `tools/utility/sql_extractor.py` | EXEC SQL 블록 추출 |
| | ChecklistGenerator | `tools/utility/checklist_generator.py` | 검증 체크리스트 생성 |
| | TaskPlanner | `tools/utility/task_planner.py` | 실행 계획 수립 |
| | DependencyResolver | `tools/utility/dependency_resolver.py` | 의존성 분석 |
| | TokenOptimizer | `tools/utility/token_optimizer.py` | 구조 요약, 함수 추출, 파일 최적화 |
| | DeploymentChecklistGenerator | `tools/utility/deployment_checklist.py` | 배포 체크리스트 (5개 섹션) |
| | ExplainPlanParser | `tools/utility/explain_plan_parser.py` | Oracle Explain Plan 파싱 |
| **Config** | settings.yaml | `config/settings.yaml` | LLM/API/정적분석 설정 |
| | LLMClient | `config/llm_client.py` | OpenAI/Azure 래퍼 |
| | SettingsLoader | `config/settings_loader.py` | YAML 로더 |
| | PromptLoader | `config/prompt_loader.py` | 프롬프트 템플릿 로더 |
| | LoggingConfig | `config/logging_config.py` | Rich 로깅 설정 |
| | ReasoningLogger | `config/reasoning_logger.py` | 추론 과정 시각화 (verbose 모드) |

---

## 2. 핵심 구현 상세

### 2.1 BaseAgent — LLM 재시도 + Fallback

```python
class BaseAgent(ABC):
    def __init__(self, model, fallback_model=None, temperature=0.0, max_retries=3):
        self.model = model
        self.fallback_model = fallback_model
        self.max_retries = max_retries
        self._llm_client = None  # Lazy init
        self.rl = ReasoningLogger()  # 추론 과정 시각화

    async def call_llm(self, messages, json_mode=True) -> str:
        # 최대 max_retries 재시도
        # 마지막 실패 시 fallback_model로 1회 시도
        # verbose 모드: spinner 애니메이션 표시
        # gpt-5 계열: temperature 파라미터 제외
```

### 2.2 LLMClient — Azure/OpenAI 자동 판별

```python
class LLMClient:
    def _create_client(self):
        # AZURE_OPENAI_API_KEY + ENDPOINT → AsyncAzureOpenAI
        # OPENAI_API_KEY → AsyncOpenAI
        # api_version: "2024-12-01-preview"

    async def chat(self, model, messages, temperature=0.0, json_mode=True):
        # gpt-5 계열은 temperature 기본값만 지원
        if not model.startswith("gpt-5"):
            kwargs["temperature"] = temperature
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
```

### 2.3 토큰 최적화 — Structure + Function Window (~80% 절감)

```python
class TokenOptimizer:
    def build_structure_summary(file_context):
        # imports/includes (Phase 1 FileContext에서 추출)
        # 함수 시그니처 목록 (ast-grep)
        # 전역 변수/상수

    def extract_error_functions(file_content, errors):
        # 에러 라인을 포함하는 함수 전체 추출
        # 함수 밖 에러는 주변 ±20줄 추출

    def optimize_file_content(file_content, file_context):
        # ≤500줄: 전체 코드
        # >500줄: head(200줄) + "...(중략)..." + tail(100줄) + 구조 요약
```

### 2.4 CAnalyzerAgent — 3경로 분기

```
경로 A: clang-tidy 있음
  → StubHeaderGenerator: #include 파싱 → stub 헤더 생성 → -I stubs/ 전달
  → clang-tidy 실행 (stub 덕분에 Level 2 데이터 흐름 분석 가능)
  → clang-tidy + CHeuristicScanner 합산 (_merge_warnings, 중복 제거)
  → structure_summary + error_functions 추출
  → gpt-5 호출 (c_analyzer_error_focused.txt)
  → 분석 후 stubs/ 디렉토리 자동 삭제

경로 B: clang-tidy 없음 AND >500줄
  Pass 1: CHeuristicScanner → 함수별 패턴 요약 → gpt-5-mini 선별
  Pass 2: risky_functions별 개별 gpt-5 호출
  → 이슈 합산 + 재번호

경로 C: clang-tidy 없음 AND ≤500줄
  → _optimize_file_content → gpt-5 호출 (c_analyzer_heuristic.txt)
```

### 2.5 SQLAnalyzerAgent — 6단계 파이프라인

```
Step 1: sqlparse 문법 검증
Step 2: AstGrepSearch 정적 패턴 5종
Step 3: ExplainPlanParser (--explain-plan 옵션 시)
Step 4: _generate_static_issues (Explain Plan → 정적 이슈)
Step 5: LLM 분석 (Error-Focused or Heuristic)
Step 6: _merge_issues (LLM + Static, 중복 제거, source="hybrid")
```

### 2.6 XMLAnalyzerAgent — WebSquare 분석

```
Step 1: XMLParser (ElementTree)
  → XXE 방어 (DOCTYPE/ENTITY 거부)
  → data_lists, events, component_ids, duplicate_ids 추출
Step 2: JS 핸들러 검증
  → {filename}.js 또는 {filename}_wq.js 탐색
  → handler_functions → grep 검색
  → missing_handlers 목록
Step 3: LLM 분석 (gpt-5-mini)
```

### 2.7 ReporterAgent — 4개 JSON 출력

| 출력 파일 | 스키마 | 내용 |
|-----------|--------|------|
| `issue-list.json` | IssueList | 전체 이슈 (severity순, Before/After 포함) |
| `checklist.json` | Checklist | Critical/High 검증 체크리스트 (명령어 포함) |
| `summary.json` | Summary | 통계 요약 + 배포 판정 + risk_description (LLM) |
| `deployment-checklist.json` | DeploymentChecklist | 5개 섹션 배포 절차 (화면/TP/모듈/배치/DBIO) |

배포 판정 로직:
- Critical > 0 → CRITICAL (차단)
- High >= 3 → HIGH (차단)
- High >= 1 → MEDIUM (가능, 수정 권고)
- 그 외 → LOW (가능)

---

## 3. 데이터 스키마 (Pydantic v2)

### Phase 0 → Phase 1: ExecutionPlan

```python
class SubTask(BaseModel):
    task_id: str          # "task_1", "task_2", ...
    file: str             # 파일 경로
    language: Literal["javascript", "c", "proc", "sql", "xml"]
    priority: int         # 1(높음) ~ N(낮음)
    metadata: FileMetadata

class ExecutionPlan(BaseModel):
    sub_tasks: List[SubTask]
    dependencies: DependencyGraph
    total_files: int
    estimated_time_seconds: int
```

### Phase 1 → Phase 2: FileContext

```python
class SingleFileContext(BaseModel):
    file: str
    language: Literal["javascript", "c", "proc", "sql", "xml"]
    imports: List[ImportInfo]
    calls: List[CallInfo]
    patterns: List[PatternInfo]

class FileContext(BaseModel):
    file_contexts: List[SingleFileContext]
    dependencies: DependencyGraph
    common_patterns: Dict[str, int]
```

### Phase 2: AnalysisResult

```python
class Issue(BaseModel):
    issue_id: str         # "JS-001", "C-001", "PC-001", "SQL-001", "XML-001"
    category: Literal["memory_safety", "null_safety", "data_integrity",
                       "error_handling", "security", "performance", "code_quality"]
    severity: Literal["critical", "high", "medium", "low"]
    title: str            # 한국어
    description: str      # 한국어
    location: Location
    fix: CodeFix          # before, after, description
    source: Literal["static_analysis", "llm", "hybrid"]
    static_tool: Optional[str]   # "eslint", "clang-tidy", "proc"
    static_rule: Optional[str]

class AnalysisResult(BaseModel):
    task_id: str
    file: str
    language: str
    agent: str
    issues: List[Issue]
    analysis_time_seconds: float
    llm_tokens_used: int
    error: Optional[str]
```

### Phase 3: Report (4개)

```python
class IssueList(BaseModel):      # issue-list.json
class Checklist(BaseModel):      # checklist.json
class Summary(BaseModel):        # summary.json (RiskAssessment 포함)
class DeploymentChecklist(BaseModel):  # deployment-checklist.json (5개 섹션)
```

---

## 4. 프롬프트 설계 (15개 파일)

| 프롬프트 파일 | Agent | 사용 경로 |
|-------------|-------|---------|
| `orchestrator.txt` | OrchestratorAgent | 시스템 프롬프트 |
| `task_classifier.txt` | TaskClassifierAgent | 파일 분류 |
| `context_collector.txt` | ContextCollectorAgent | 컨텍스트 수집 |
| `js_analyzer_error_focused.txt` | JavaScriptAnalyzerAgent | ESLint 오류 있을 때 |
| `js_analyzer_heuristic.txt` | JavaScriptAnalyzerAgent | 오류 없을 때 |
| `c_analyzer_error_focused.txt` | CAnalyzerAgent | clang-tidy/Heuristic 오류 있을 때 |
| `c_analyzer_heuristic.txt` | CAnalyzerAgent | 오류 없을 때 (경로 C) |
| `c_prescan_fewshot.txt` | CAnalyzerAgent | 2-Pass Pass 1 few-shot 선별 |
| `proc_analyzer_error_focused.txt` | ProCAnalyzerAgent | proc/Heuristic 오류 있을 때 |
| `proc_analyzer_heuristic.txt` | ProCAnalyzerAgent | 오류 없을 때 |
| `sql_analyzer_error_focused.txt` | SQLAnalyzerAgent | 패턴/Explain Plan 있을 때 |
| `sql_analyzer_heuristic.txt` | SQLAnalyzerAgent | 패턴 없을 때 |
| `xml_analyzer_error_focused.txt` | XMLAnalyzerAgent | 구조 오류 있을 때 |
| `xml_analyzer_heuristic.txt` | XMLAnalyzerAgent | 오류 없을 때 |
| `reporter.txt` | ReporterAgent | risk_description 생성 |

---

## 5. 알려진 이슈 및 해결 기록

| # | 이슈 | 원인 | 해결 |
|---|------|------|------|
| 001 | 대형 파일 분석 갭 | 500줄+ 파일에서 토큰 초과 | Structure + Function Window 토큰 최적화 (~80% 절감) |
| 002 | clang-tidy 헤더 제한 | 폐쇄망에서 헤더 미존재 시 Level 2 분석 불가 | CHeuristicScanner로 보완 (regex 6종) |
| 003 | Pass 2 대형 함수 압도 | c100(636줄)+c200(1115줄)이 c400(127줄) 이슈를 묻음 | 함수별 개별 LLM 호출 (MIDER_EXCLUDE_FUNCTIONS 임시 우회) |
| 004 | gpt-4o-mini SQL 인덱스 힌트 한계 | mini 모델이 인덱스 힌트 최적화를 못 잡음 | SQL Analyzer는 gpt-5 (primary) 사용 |
| 005 | XML script 추출 누락 | WebSquare XML의 script 태그 미파싱 | XMLParser에 script 태그 추출 로직 추가 |
| 006 | clang-tidy 헤더 에러 fallback | 헤더 에러 시 전체 분석 실패 | Heuristic 경로로 자동 fallback |
| 007 | C 중복 이슈 | clang-tidy + Heuristic 중복 탐지 | _merge_warnings로 중복 제거 |
| 008 | Pro*C fclose 탐지 갭 | fclose 누락 패턴 미탐지 | ProCHeuristicScanner 패턴 추가 |
| 009 | clang-tidy stub 헤더 자동 생성 | 헤더 에러 필터링만으로는 Level 2 분석 활용 불가 | StubHeaderGenerator로 공통 타입 stub 헤더 생성 → -I 플래그 전달 |
