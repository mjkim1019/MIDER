# 데이터 스키마 정의: Mider

> Agent 간 데이터 계약 (Contract). 모든 스키마는 Pydantic v2 모델로 구현한다.

---

## 1. Phase 0 → Phase 1: ExecutionPlan

TaskClassifierAgent가 생성하여 OrchestratorAgent에 반환.

```python
class SubTask(BaseModel):
    task_id: str              # "task_1", "task_2", ...
    file: str                 # 파일 경로 (절대경로)
    language: Literal["javascript", "c", "proc", "sql"]
    priority: int             # 1(높음) ~ N(낮음)
    metadata: FileMetadata

class FileMetadata(BaseModel):
    file_size: int            # bytes
    line_count: int
    last_modified: datetime

class DependencyGraph(BaseModel):
    edges: List[DependencyEdge]
    has_circular: bool        # 순환 의존성 여부
    warnings: List[str]       # 순환 감지 시 경고 메시지

class DependencyEdge(BaseModel):
    source: str               # 참조하는 파일
    target: str               # 참조되는 파일
    type: Literal["import", "include", "exec_sql_include"]

class ExecutionPlan(BaseModel):
    sub_tasks: List[SubTask]
    dependencies: DependencyGraph
    total_files: int
    estimated_time_seconds: int
```

**JSON 예시:**
```json
{
  "sub_tasks": [
    {
      "task_id": "task_1",
      "file": "/app/src/db/orders.sql",
      "language": "sql",
      "priority": 1,
      "metadata": {
        "file_size": 2048,
        "line_count": 85,
        "last_modified": "2026-02-20T10:30:00"
      }
    },
    {
      "task_id": "task_2",
      "file": "/app/src/service/calc.c",
      "language": "c",
      "priority": 2,
      "metadata": {
        "file_size": 15360,
        "line_count": 520,
        "last_modified": "2026-02-22T14:00:00"
      }
    }
  ],
  "dependencies": {
    "edges": [
      {"source": "/app/src/service/calc.c", "target": "/app/src/db/orders.sql", "type": "include"}
    ],
    "has_circular": false,
    "warnings": []
  },
  "total_files": 2,
  "estimated_time_seconds": 120
}
```

---

## 2. Phase 1 → Phase 2: FileContext

ContextCollectorAgent가 생성하여 각 AnalyzerAgent에 전달.

```python
class ImportInfo(BaseModel):
    statement: str            # 원본 구문 (e.g., "#include <stdio.h>")
    resolved_path: Optional[str]  # 선택된 파일 내 매칭된 경로 (없으면 None)
    is_external: bool         # 외부 라이브러리 여부

class CallInfo(BaseModel):
    function_name: str        # 호출되는 함수명
    line: int                 # 호출 위치
    target_file: Optional[str]  # 대상 파일 (매칭된 경우)

class PatternInfo(BaseModel):
    pattern_type: Literal[
        "error_handling",     # try-catch, if-return, SQLCA check
        "logging",            # console.log, printf, log_error
        "transaction",        # COMMIT, ROLLBACK
        "memory_management"   # malloc/free 쌍
    ]
    description: str
    line: int

class SingleFileContext(BaseModel):
    file: str                 # 파일 경로
    language: Literal["javascript", "c", "proc", "sql"]
    imports: List[ImportInfo]
    calls: List[CallInfo]
    patterns: List[PatternInfo]

class FileContext(BaseModel):
    file_contexts: List[SingleFileContext]
    dependencies: DependencyGraph  # ExecutionPlan에서 전달받은 것 재사용
    common_patterns: Dict[str, int]  # 패턴 유형별 빈도수
```

**JSON 예시:**
```json
{
  "file_contexts": [
    {
      "file": "/app/src/service/calc.c",
      "language": "c",
      "imports": [
        {"statement": "#include <stdio.h>", "resolved_path": null, "is_external": true},
        {"statement": "#include \"utils.h\"", "resolved_path": "/app/src/common/utils.h", "is_external": false}
      ],
      "calls": [
        {"function_name": "execSQL", "line": 45, "target_file": null},
        {"function_name": "log_error", "line": 78, "target_file": "/app/src/common/utils.h"}
      ],
      "patterns": [
        {"pattern_type": "error_handling", "description": "if-return error handling", "line": 50},
        {"pattern_type": "memory_management", "description": "malloc without free", "line": 120}
      ]
    }
  ],
  "dependencies": {"edges": [], "has_circular": false, "warnings": []},
  "common_patterns": {"error_handling": 3, "memory_management": 1}
}
```

---

## 3. Phase 2: AnalysisResult

각 AnalyzerAgent(JS/C/ProC/SQL)가 생성하여 OrchestratorAgent에 반환.

```python
class Location(BaseModel):
    file: str
    line_start: int
    line_end: int
    column_start: Optional[int] = None
    column_end: Optional[int] = None

class CodeFix(BaseModel):
    before: str               # 수정 전 코드 (1-3줄)
    after: str                # 수정 후 코드 (1-3줄)
    description: str          # 한국어 수정 설명

class Issue(BaseModel):
    issue_id: str             # "JS-001", "C-001", "PC-001", "SQL-001"
    category: Literal[
        "memory_safety",      # 버퍼 오버플로우, 메모리 누수, use-after-free
        "null_safety",        # NULL 포인터, undefined 체크
        "data_integrity",     # SQLCA 누락, INDICATOR 누락, 트랜잭션
        "error_handling",     # 예외 처리 누락, Promise rejection
        "security",           # XSS, injection
        "performance",        # Full Table Scan, 인덱스 억제, N+1
        "code_quality"        # 코드 품질, 컨벤션
    ]
    severity: Literal["critical", "high", "medium", "low"]
    title: str                # 이슈 제목 (한국어)
    description: str          # 상세 설명 (한국어)
    location: Location
    fix: CodeFix
    source: Literal["static_analysis", "llm", "hybrid"]  # 탐지 출처
    static_tool: Optional[str] = None  # "eslint", "clang-tidy", "proc" (정적 분석 도구명)
    static_rule: Optional[str] = None  # ESLint rule, clang-tidy check 등

class AnalysisResult(BaseModel):
    task_id: str              # ExecutionPlan의 task_id와 매칭
    file: str
    language: Literal["javascript", "c", "proc", "sql"]
    agent: str                # "JavaScriptAnalyzerAgent" 등
    issues: List[Issue]
    analysis_time_seconds: float
    llm_tokens_used: int
    error: Optional[str] = None  # Agent 내부 에러 발생 시
```

**JSON 예시:**
```json
{
  "task_id": "task_2",
  "file": "/app/src/service/calc.c",
  "language": "c",
  "agent": "CAnalyzerAgent",
  "issues": [
    {
      "issue_id": "C-001",
      "category": "memory_safety",
      "severity": "critical",
      "title": "strcpy 버퍼 오버플로우 위험",
      "description": "strcpy()는 버퍼 크기를 검증하지 않아 오버플로우가 발생할 수 있습니다. strncpy() 또는 snprintf()로 교체가 필요합니다.",
      "location": {
        "file": "/app/src/service/calc.c",
        "line_start": 234,
        "line_end": 234,
        "column_start": 5,
        "column_end": 35
      },
      "fix": {
        "before": "strcpy(dest, src);",
        "after": "strncpy(dest, src, sizeof(dest) - 1);\ndest[sizeof(dest) - 1] = '\\0';",
        "description": "strcpy를 strncpy로 교체하고, NULL 종료 문자를 보장합니다."
      },
      "source": "hybrid",
      "static_tool": "clang-tidy",
      "static_rule": "bugprone-not-null-terminated-result"
    }
  ],
  "analysis_time_seconds": 8.5,
  "llm_tokens_used": 4200,
  "error": null
}
```

---

## 4. Phase 3: Report 스키마 (3개)

ReporterAgent가 생성. 각각 별도 JSON 파일로 출력.

### 4.1 IssueList (`output/issue-list.json`)

```python
class IssueListItem(BaseModel):
    issue_id: str
    file: str
    language: str
    category: str
    severity: Literal["critical", "high", "medium", "low"]
    title: str
    description: str
    location: Location
    fix: CodeFix
    source: str

class IssueList(BaseModel):
    generated_at: datetime
    session_id: str
    total_issues: int
    by_severity: Dict[str, int]   # {"critical": 2, "high": 5, ...}
    issues: List[IssueListItem]   # severity 순 정렬 (critical → low)
```

**JSON 예시:**
```json
{
  "generated_at": "2026-02-24T15:30:00",
  "session_id": "20260224_153000",
  "total_issues": 7,
  "by_severity": {"critical": 2, "high": 3, "medium": 1, "low": 1},
  "issues": [
    {
      "issue_id": "C-001",
      "file": "/app/src/service/calc.c",
      "language": "c",
      "category": "memory_safety",
      "severity": "critical",
      "title": "strcpy 버퍼 오버플로우 위험",
      "description": "...",
      "location": {"file": "...", "line_start": 234, "line_end": 234},
      "fix": {"before": "strcpy(dest, src);", "after": "strncpy(...)", "description": "..."},
      "source": "hybrid"
    }
  ]
}
```

### 4.2 Checklist (`output/checklist.json`)

```python
class ChecklistItem(BaseModel):
    id: str                      # "CHECK-1", "CHECK-2", ...
    category: str                # issue category
    severity: Literal["critical", "high"]  # critical, high만 포함
    description: str             # 한국어 체크 항목
    related_issues: List[str]    # 연관 issue_id 목록
    verification_command: str    # 검증 명령어
    expected_result: str         # 기대 결과

class Checklist(BaseModel):
    generated_at: datetime
    session_id: str
    total_checks: int
    items: List[ChecklistItem]
```

**JSON 예시:**
```json
{
  "generated_at": "2026-02-24T15:30:00",
  "session_id": "20260224_153000",
  "total_checks": 5,
  "items": [
    {
      "id": "CHECK-1",
      "category": "memory_safety",
      "severity": "critical",
      "description": "모든 strcpy를 strncpy로 교체 완료",
      "related_issues": ["C-001", "C-003"],
      "verification_command": "grep -n 'strcpy' /app/src/service/calc.c",
      "expected_result": "매칭 결과 없음 (0건)"
    }
  ]
}
```

### 4.3 Summary (`output/summary.json`)

```python
class AnalysisMetadata(BaseModel):
    session_id: str
    analyzed_at: datetime
    total_files: int
    total_lines: int
    analysis_duration_seconds: float
    total_llm_tokens: int

class IssueSummary(BaseModel):
    total: int
    by_severity: Dict[str, int]      # {"critical": 2, "high": 5, ...}
    by_category: Dict[str, int]      # {"memory_safety": 3, ...}
    by_language: Dict[str, int]      # {"c": 4, "javascript": 2, ...}
    by_file: Dict[str, int]          # {"/app/src/calc.c": 4, ...}

class RiskAssessment(BaseModel):
    deployment_risk: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    deployment_allowed: bool         # critical == 0이면 True
    blocking_issues: List[str]       # critical issue_id 목록
    risk_description: str            # 한국어 위험 설명

class Summary(BaseModel):
    analysis_metadata: AnalysisMetadata
    issue_summary: IssueSummary
    risk_assessment: RiskAssessment
```

**JSON 예시:**
```json
{
  "analysis_metadata": {
    "session_id": "20260224_153000",
    "analyzed_at": "2026-02-24T15:30:00",
    "total_files": 5,
    "total_lines": 2340,
    "analysis_duration_seconds": 45.2,
    "total_llm_tokens": 28500
  },
  "issue_summary": {
    "total": 7,
    "by_severity": {"critical": 2, "high": 3, "medium": 1, "low": 1},
    "by_category": {"memory_safety": 3, "data_integrity": 2, "performance": 1, "code_quality": 1},
    "by_language": {"c": 4, "proc": 2, "sql": 1},
    "by_file": {"/app/src/service/calc.c": 4, "/app/src/batch/process.pc": 2, "/app/src/db/orders.sql": 1}
  },
  "risk_assessment": {
    "deployment_risk": "CRITICAL",
    "deployment_allowed": false,
    "blocking_issues": ["C-001", "C-003"],
    "risk_description": "Critical 이슈 2건이 발견되었습니다. 배포 전 반드시 수정이 필요합니다."
  }
}
```

---

## 5. Session 스키마 (2차 PoC)

> 세션 저장/복구는 2차 PoC에서 구현 예정.

OrchestratorAgent가 세션 저장/복구에 사용.

```python
class Checkpoint(BaseModel):
    timestamp: datetime
    phase: Literal["phase_0", "phase_1", "phase_2", "phase_3"]
    data: Dict[str, Any]      # Phase별 출력 데이터

class SessionState(BaseModel):
    session_id: str           # "20260224_153000" (timestamp 기반)
    created_at: datetime
    updated_at: datetime
    current_phase: Literal["phase_0", "phase_1", "phase_2", "phase_3"]
    status: Literal["running", "paused", "completed", "failed"]
    input_files: List[str]    # 사용자가 지정한 파일 목록
    execution_plan: Optional[ExecutionPlan] = None     # Phase 0 완료 후
    file_context: Optional[FileContext] = None         # Phase 1 완료 후
    completed_tasks: List[str]                         # Phase 2 진행 중 완료된 task_id
    analysis_results: List[AnalysisResult]             # Phase 2 수집 결과
    checkpoints: List[Checkpoint]
    config: SessionConfig

class SessionConfig(BaseModel):
    llm_model: str            # "gpt-4o"
    llm_fallback: Optional[str]  # "gpt-4-turbo"
    temperature: float        # 0.0 ~ 1.0
    max_retries: int          # LLM API 재시도 횟수 (기본 3)
```

**세션 파일 경로:** `./sessions/session_{session_id}.json`

---

## 6. 스키마 간 데이터 흐름

```
[사용자 입력: 파일 목록]
        │
        ▼
  ┌─ Phase 0 ─┐
  │ ExecutionPlan │ ──→ sub_tasks[].task_id가 이후 모든 Phase에서 키로 사용
  └────────────┘
        │
        ▼
  ┌─ Phase 1 ─┐
  │ FileContext    │ ──→ file_contexts[].file이 AnalyzerAgent에 전달
  └────────────┘
        │
        ▼
  ┌─ Phase 2 ─┐
  │ AnalysisResult │ ──→ issues[]가 ReporterAgent에 전달
  │ (파일별 N개)   │
  └────────────┘
        │
        ▼
  ┌─ Phase 3 ─┐
  │ IssueList     │ ──→ output/issue-list.json
  │ Checklist     │ ──→ output/checklist.json
  │ Summary       │ ──→ output/summary.json
  └────────────┘
```

### 키 매칭 규칙
- `ExecutionPlan.sub_tasks[].task_id` = `AnalysisResult.task_id`
- `ExecutionPlan.sub_tasks[].file` = `FileContext.file_contexts[].file` = `AnalysisResult.file`
- `AnalysisResult.issues[].issue_id` = `IssueList.issues[].issue_id` = `Checklist.items[].related_issues[]`
