# 기술 설계서: Mider - Agent 상세 설계

> **1차 PoC 범위**: 정적 분석 + LLM 하이브리드 분석 (RAG 미포함), 토큰 최적화 (Structure + Function Window)
> **2차 PoC 예정**: RAG (Knowledge Base + Vector DB)

---

## 1. Agent 아키텍처 개요

```
┌─────────────────────────────────────────────────┐
│                  CLI (main.py)                    │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│            OrchestratorAgent                     │
│  Phase 0 → Phase 1 → Phase 2 → Phase 3           │
└──┬────────┬────────┬────────┬────────┬──────────┘
   │        │        │        │        │
   ▼        ▼        ▼        ▼        ▼
 Task    Context    JS/C/   ProC/    Reporter
Classifier Collector SQL    Analyzer  Agent
 Agent     Agent   Analyzer  Agent
```

### Agent 목록 (1차 PoC: 7개)

| # | Agent | 역할 | LLM Model |
|---|-------|------|-----------|
| 1 | OrchestratorAgent | 워크플로우 제어 | gpt-4o (temp 0.3) |
| 2 | TaskClassifierAgent | 파일 분류, 실행 계획 수립 | gpt-4o-mini |
| 3 | ContextCollectorAgent | import/include 추출, 의존성 매핑 | gpt-4o-mini |
| 4 | JavaScriptAnalyzerAgent | JS 정적분석 + LLM 심층분석 | gpt-4o |
| 5 | CAnalyzerAgent | C 정적분석 + LLM 심층분석 | gpt-4o |
| 6 | ProCAnalyzerAgent | Pro*C 정적분석 + LLM 심층분석 | gpt-4o |
| 7 | SQLAnalyzerAgent | SQL 패턴분석 + LLM 심층분석 | gpt-4o-mini |
| 8 | ReporterAgent | 리포트 생성 (3개 JSON) | gpt-4o-mini (temp 0.3) |

### 2차 PoC 예정 Agent

| Agent | 역할 | 비고 |
|-------|------|------|
| KnowledgeRetrieverAgent | RAG 기반 KB 검색 | ChromaDB + sentence-transformers |

---

## 2. Agent 상세 설계

---

### Agent 1: OrchestratorAgent

#### 2.1.1 페르소나 (Identity)

| 항목 | 정의 내용 |
|------|----------|
| Agent 이름 | OrchestratorAgent |
| 주요 역할 | 전체 분석 워크플로우 제어 및 Sub-agent 조율 총괄. Phase 0 → 1 → 2 → 3 순차 실행하며 각 단계의 입출력을 관리 |
| 핵심 목표 | - 사용자가 지정한 파일들을 분석하여 배포 차단 이슈를 탐지<br>- 각 Phase의 결과를 통합하여 최종 리포트 생성 |
| 톤앤매너 | 명령형, 시스템 관리자 스타일. 간결하고 정확한 상태 보고 |
| 제약 사항 | - 코드를 직접 수정하지 않음 (제안만)<br>- 사용자가 선택한 파일만 분석 (프로젝트 전체 탐색 금지)<br>- Phase 순서 고정 (0 → 1 → 2 → 3) |

#### 2.1.2 워크플로우 (Workflow & Logic)

**Step 1: Input Analysis**
```
1. 파일 경로 검증
   - 존재 여부 확인
   - 읽기 권한 확인
   - 와일드카드 확장 (glob)
2. TaskClassifierAgent 호출 준비
```

**Step 2: Phase Orchestration**
```
Phase 0: Task Classification
  → TaskClassifierAgent.classify(files)
  → ExecutionPlan 획득

Phase 1: Context Collection
  → ContextCollectorAgent.collect(ExecutionPlan)
  → FileContext 획득

Phase 2: Sequential Language Analysis
  For each task in ExecutionPlan:
    → 언어별 AnalyzerAgent 호출 (FileContext 전달)
    → AnalysisResult 수집

Phase 3: Report Generation
  → ReporterAgent.generate(all_results)
  → IssueList, Checklist, Summary 생성
```

**Step 3: Execution & Response**
```
1. 터미널 출력
   - 진행률 표시 (Progress Bar)
   - 각 Phase 완료 메시지
   - Critical 이슈 요약
   - 배포 가능 여부 판정
2. 파일 출력
   - ./output/issue-list.json
   - ./output/checklist.json
   - ./output/summary.json
```

#### 2.1.3 상태 관리

*세션 상태 관리 (SessionState, Checkpoint)는 2차 PoC에서 구현 예정*

#### 2.1.4 도구(Tools)

| 도구명 | 기능 설명 | 입력 | 출력 |
|--------|----------|------|------|
| call_agent | Sub-agent 호출 및 결과 수신 | agent_name: str, method: str, params: Dict | agent_result: Dict |
| glob_expand | 와일드카드 파일 패턴 확장 | pattern: str, root: str | matched_files: List[str] |
| validate_files | 파일 존재 및 권한 검증 | filepaths: List[str] | valid_files: List[str], errors: List[str] |

#### 2.1.5 메모리 전략

- **메모리 유형**: None (1차 PoC에서는 세션 저장 없음)
- **RAG**: 사용 안 함 (워크플로우 제어만 담당)
- **세션 저장/복구**: 2차 PoC 예정

#### 2.1.6 기술 스택

| 구분 | 선정 기술 | 사유 |
|------|----------|------|
| LLM Model | gpt-4o (temp 0.3, Fallback: gpt-4-turbo) | 중간 복잡도 작업, 비용 효율적 |
| Agent Framework | Python Class (순수 구현) | 워크플로우가 명확하고 순차적 |
| Prompt Strategy | System Prompt + Task-specific Instructions | Phase별 명확한 지시로 충분 |
| Output Parsing | JSON Mode (Structured Output) | Sub-agent 결과를 JSON으로 통일 |
| Monitoring | Python logging + 터미널 Progress Bar | 폐쇄망 환경, 외부 모니터링 불가 |

---

### Agent 2: TaskClassifierAgent

#### 2.2.1 페르소나 (Identity)

| 항목 | 정의 내용 |
|------|----------|
| Agent 이름 | TaskClassifierAgent |
| 주요 역할 | 선택된 파일들을 언어별로 분류하고, 파일 간 의존성을 분석하여 최적의 실행 계획(ExecutionPlan) 수립 |
| 핵심 목표 | - 파일 확장자 기반 언어 식별 (JS/C/ProC/SQL)<br>- import/include 기반 의존성 그래프 생성<br>- Critical 가능성 높은 파일 우선순위 부여<br>- 분석 순서 최적화 (의존성 역순) |
| 톤앤매너 | 분석적, 논리적. 명확한 분류 근거 제시 |
| 제약 사항 | - 선택된 파일만 분석 (디렉토리 탐색 금지)<br>- 파일 내용 읽기만 가능 (수정 불가)<br>- 실행 계획만 생성 (실제 분석 X) |

#### 2.2.2 워크플로우

**Step 1: Language Detection**
```
For each file in selected_files:
  1. 확장자 추출
     - .js → JavaScript
     - .c, .h → C
     - .pc → Pro*C
     - .sql → SQL
  2. 파일 메타데이터 수집
     - 파일 크기
     - 라인 수
     - 수정 날짜
```

**Step 2: Dependency Analysis**
```
1. 파일별 import/include 추출
   - JavaScript: import, require 구문
   - C: #include 지시문
   - Pro*C: EXEC SQL INCLUDE, #include
   - SQL: N/A (의존성 없음)
2. 의존성 그래프 생성
   - 파일 A → 파일 B (A가 B를 참조)
3. 순환 의존성 검사
   - 발견 시 경고 메시지
```

**Step 3: Priority & Order**
```
1. 우선순위 기준
   - 의존성: 하위 계층 먼저 (SQL → C → JS)
   - 복잡도: 라인 수 많을수록 높음
   - 수정 날짜: 최근 수정 파일 우선
2. 실행 순서 결정
   - Topological Sort (의존성 그래프 기반)
3. ExecutionPlan 생성
   {
     "sub_tasks": [
       {"task_id": "task_1", "file": "db.sql", "language": "sql", "priority": 1},
       {"task_id": "task_2", "file": "service.c", "language": "c", "priority": 2},
       ...
     ],
     "dependencies": {...},
     "estimated_time": 120
   }
```

#### 2.2.3 상태 관리

- **Stateless**: 입력(파일 리스트) → 출력(ExecutionPlan)만 생성

#### 2.2.4 도구(Tools)

| 도구명 | 기능 설명 | 입력 | 출력 |
|--------|----------|------|------|
| file_reader | 파일 내용 읽기 | path: str | content: str, encoding: str |
| local_dependency_resolver | 선택된 파일 간 의존성 분석 | files: List[str] | dependencies: Dict, graph: Dict |

#### 2.2.5 메모리 전략

- **메모리 유형**: None (Stateless)
- **저장 전략**: ExecutionPlan만 OrchestratorAgent에 반환
- **RAG**: 사용 안 함 (규칙 기반 분류로 충분)

#### 2.2.6 기술 스택

| 구분 | 선정 기술 | 사유 |
|------|----------|------|
| LLM Model | gpt-4o-mini (Fallback: gpt-4o) | 분류 작업은 경량 모델로 충분 |
| Agent Framework | Python Class | 단순 입출력 |
| Prompt Strategy | Few-Shot (파일 분류 예시 3개) | 정확한 언어 감지를 위한 예시 |
| Output Parsing | JSON Mode (ExecutionPlan 스키마) | 구조화된 실행 계획 반환 |
| Monitoring | Python logging | 분류 결과 로그 |

---

### Agent 3: ContextCollectorAgent

#### 2.3.1 페르소나 (Identity)

| 항목 | 정의 내용 |
|------|----------|
| Agent 이름 | ContextCollectorAgent |
| 주요 역할 | 선택된 파일들의 컨텍스트(import/include, 의존성, 공통 패턴)를 수집하여 Phase 2 분석을 위한 배경 정보 제공 |
| 핵심 목표 | - 각 파일의 import/include 구문 추출<br>- 파일 간 호출 관계 매핑<br>- 공통 에러 처리 패턴 파악<br>- Language Analyzer에게 컨텍스트 제공 |
| 톤앤매너 | 정보 수집가, 탐정 스타일. 팩트 기반 보고 |
| 제약 사항 | - 프로젝트 전체 탐색 금지 (선택된 파일만)<br>- 코드 분석 X (구문 추출만)<br>- 이슈 탐지 X (컨텍스트 수집만) |

#### 2.3.2 워크플로우

**Step 1: Import/Include Extraction**
```
For each file in ExecutionPlan:
  JavaScript:
    - import 구문 추출 (정규표현식)
    - require() 호출 추출
  C:
    - #include 지시문 추출
    - 시스템 헤더 vs 사용자 헤더 구분
  Pro*C:
    - EXEC SQL INCLUDE 추출
    - #include 추출
  SQL:
    - N/A (SQL 파일은 의존성 없음)
```

**Step 2: Dependency Mapping**
```
1. 파일 간 매칭
   import './utils' → 선택된 파일 중 utils.js 찾기
2. 호출 관계 매핑
   JS → C (callTPService)
   C → SQL (execSQL)
3. 매칭 실패 시
   - 외부 라이브러리로 간주
   - 경고 메시지 (선택된 파일 아님)
```

**Step 3: Pattern Analysis**
```
공통 패턴 탐지:
  - 에러 처리: try-catch, if-return, SQLCA check
  - 로깅: console.log, printf, log_error
  - 트랜잭션: COMMIT, ROLLBACK
  - 메모리 관리: malloc/free 쌍

FileContext 생성:
{
  "file_contexts": [
    {
      "file": "service.c",
      "imports": ["#include <stdio.h>", "#include \"utils.h\""],
      "calls": ["execSQL", "log_error"],
      "patterns": ["if-return error handling"]
    },
    ...
  ],
  "dependencies": {...},
  "common_patterns": {...}
}
```

#### 2.3.3 상태 관리

- **Stateless**: ExecutionPlan → FileContext 변환

#### 2.3.4 도구(Tools)

| 도구명 | 기능 설명 | 입력 | 출력 |
|--------|----------|------|------|
| file_reader | 파일 읽기 | path: str | content: str |
| grep | 패턴 검색 (import/include) | pattern: str, file: str | matches: List[Dict] |
| ast_grep_search | AST 기반 구조 패턴 검색 | pattern: str, file: str, language: str | matches: List[Dict] |
| lsp_client | 심볼 정의/참조 탐색 | action: str, file: str, line: int | location: Dict |
| local_dependency_resolver | 파일 간 의존성 해석 | files: List[str] | dependencies: Dict |

#### 2.3.5 메모리 전략

- **메모리 유형**: None (Stateless)
- **저장 전략**: FileContext만 반환
- **RAG**: 사용 안 함 (정적 분석으로 충분)

#### 2.3.6 기술 스택

| 구분 | 선정 기술 | 사유 |
|------|----------|------|
| LLM Model | gpt-4o-mini (Fallback: gpt-4o) | 빠른 탐색, 저비용 |
| Agent Framework | Python Class | 순차 처리, 간단한 로직 |
| Prompt Strategy | Instruction-based (금지 사항 명시) | "선택된 파일만 분석" 강조 |
| Output Parsing | JSON Mode (FileContext 스키마) | 구조화된 컨텍스트 반환 |
| Monitoring | Python logging | 추출 결과 로그 |

---

### Agent 4: JavaScriptAnalyzerAgent

#### 2.4.1 페르소나 (Identity)

| 항목 | 정의 내용 |
|------|----------|
| Agent 이름 | JavaScriptAnalyzerAgent |
| 주요 역할 | JavaScript 파일 전문 분석. ESLint 정적 분석 + LLM 심층 분석(클로저, 메모리 누수, XSS)을 결합하여 프로덕션 장애 가능성 높은 이슈 탐지 |
| 핵심 목표 | - ESLint가 못 잡는 런타임 오류 탐지<br>- 클로저 스코프 체인 오류 분석<br>- 메모리 누수 패턴 탐지 (이벤트, 타이머, DOM)<br>- XSS 취약점 검사 (innerHTML, dangerouslySetInnerHTML) |
| 톤앤매너 | 전문가, 교육자 스타일. Before/After로 명확히 제시 |
| 제약 사항 | - 코드 수정 불가 (제안만)<br>- 오류 없어도 휴리스틱 체크 수행 |

#### 2.4.2 워크플로우

**Step 1: Static Analysis (ESLint)**
```
1. ESLint 실행
   eslint_runner(file_path, config=".eslintrc.json")
2. 결과 파싱
   errors: [
     {"rule": "no-undef", "message": "'userData' is not defined", "line": 45},
     ...
   ]
```

**Step 2: LLM Deep Analysis (토큰 최적화: Structure + Function Window)**
```
If ESLint errors found:
  # 경로 A: Error-Focused
  # 토큰 최적화: 파일 전체 대신 구조 요약 + 에러 포함 함수만 전달
  structure_summary = _build_structure_summary(file_context)
    # - imports/includes (Phase 1 file_context에서 추출)
    # - 함수 시그니처 목록 (ast-grep)
    # - 전역 변수/상수
  error_functions = _extract_error_functions(file_content, eslint_errors)
    # - 에러 라인을 포함하는 함수 전체 추출
    # - 함수 밖 에러는 에러 주변 ±20줄 추출

  prompt = f"""
  당신은 JavaScript 전문가입니다.

  [ESLint 오류]
  {eslint_errors}

  [구조 요약]
  {structure_summary}

  [에러 관련 함수 전체 코드]
  {error_functions}

  [파일 컨텍스트]
  {file_context}

  다음을 수행하세요:
  1. ESLint 오류 정밀 분석
  2. 관련 추가 오류 탐지 (메모리 누수, XSS, Race Condition)
  3. Before/After 코드 제시 (1-3줄)
  4. 수정 방법 한국어로 설명

  출력 형식: JSON (AnalysisResult 스키마)
  """

Else:
  # 경로 B: Heuristic
  # 토큰 최적화: 500줄 이하 전체, 초과 시 head+tail+구조요약
  file_content_optimized = _optimize_file_content(file_content, file_context)
    # - ≤500줄: 전체 코드
    # - >500줄: head(200줄) + "...(중략)..." + tail(100줄) + 구조 요약

  prompt = f"""
  당신은 JavaScript 전문가입니다.

  [파일 코드]
  {file_content_optimized}

  [휴리스틱 체크리스트]
  - 메모리 누수 (이벤트 리스너, 타이머, DOM 참조)
  - XSS 취약점 (innerHTML, dangerouslySetInnerHTML, eval)
  - Race Condition (비동기 흐름)
  - NULL/undefined 체크 누락
  - N+1 쿼리 패턴

  위 항목에서 발견된 이슈만 반환하세요. 문제 없으면 빈 배열 반환.
  출력 형식: JSON (AnalysisResult 스키마)
  """

LLM 실행 → AnalysisResult 수신
```

#### 2.4.3 상태 관리

- **Stateless**: 단일 파일 분석 후 결과 반환

#### 2.4.4 도구(Tools)

| 도구명 | 기능 설명 | 입력 | 출력 |
|--------|----------|------|------|
| file_reader | 파일 읽기 | path: str | content: str, lines: int |
| eslint_runner | ESLint 정적 분석 | file: str, config: str | errors: List[Dict], warnings: List[Dict] |
| ast_grep_search | AST 패턴 검색 (클로저) | pattern: str, file: str | matches: List[Dict] |
| lsp_client | 심볼 정의/참조 탐색 | action: str, file: str, line: int | location: Dict |

#### 2.4.5 메모리 전략

- **메모리 유형**: None (파일 단위 독립 분석)
- **저장 전략**: AnalysisResult만 반환
- **RAG**: 2차 PoC 예정 (resources/knowledge_base/javascript/)

#### 2.4.6 기술 스택

| 구분 | 선정 기술 | 사유 |
|------|----------|------|
| LLM Model | gpt-4o (No Fallback) | 복잡한 클로저 분석, 128k 컨텍스트 필요 |
| Agent Framework | Python Class | 순차 실행 (ESLint → LLM) |
| Prompt Strategy | CoT (Chain of Thought) + Few-Shot | 단계별 추론 필요 |
| Output Parsing | JSON Mode (AnalysisResult 스키마) | 구조화된 이슈 반환 |
| Monitoring | Python logging + Token counting | 128k 컨텍스트 사용량 추적 |

---

### Agent 5: CAnalyzerAgent

#### 2.5.1 페르소나 (Identity)

| 항목 | 정의 내용 |
|------|----------|
| Agent 이름 | CAnalyzerAgent |
| 주요 역할 | C 언어 파일 전문 분석. clang-tidy 정적 분석 + LLM 심층 분석(메모리 안전성, 포인터 오류)을 결합하여 크래시 가능성 높은 이슈 탐지 |
| 핵심 목표 | - 버퍼 오버플로우 탐지 (strcpy, sprintf)<br>- NULL 포인터 역참조 탐지<br>- 메모리 누수 패턴 탐지 (malloc/free 짝)<br>- Use-after-free, Double-free 검사 |
| 톤앤매너 | 시스템 엔지니어, 보안 전문가 스타일. 크래시 영향도 명시 |
| 제약 사항 | - 코드 수정 불가<br>- 동적 분석 불가 (정적 분석만) |

#### 2.5.2 워크플로우

**Step 1: Static Analysis (clang-tidy)**
```
clang_tidy_runner(
  file=file_path,
  checks="-*,clang-analyzer-*,bugprone-*"
)
결과:
  warnings: [
    {"check": "strcpy", "severity": "warning", "line": 234, "message": "..."},
    ...
  ]
```

**Step 2: LLM Analysis (토큰 최적화: Structure + Function Window)**
```
경로 A (Error-Focused, warnings 있을 때):
  # 토큰 최적화: 파일 전체 대신 구조 요약 + 에러 포함 함수만 전달
  structure_summary = _build_structure_summary(file_context)
  error_functions = _extract_error_functions(file_content, warnings)

  prompt = """
  C 메모리 안전성 전문가입니다.

  [clang-tidy 경고]
  {warnings}

  [구조 요약]
  {structure_summary}

  [에러 관련 함수 전체 코드]
  {error_functions}

  [파일 컨텍스트]
  {file_context}

  분석:
  1. clang-tidy 경고 정밀 분석
  2. malloc/free 짝 검사
  3. 포인터 안전성 검사
  4. 스레드 안전성 (mutex)

  출력: JSON (AnalysisResult 스키마)
  """

경로 B (Heuristic, warnings 없을 때):
  # 토큰 최적화: 500줄 이하 전체, 초과 시 head+tail+구조요약
  file_content_optimized = _optimize_file_content(file_content, file_context)

  prompt = """
  [파일 코드]
  {file_content_optimized}

  [휴리스틱 체크]
  - Use-after-free (복잡한 포인터)
  - 이중 해제 (double free)
  - 스레드 안전성 (mutex 누락)
  - 타이밍 기반 메모리 누수

  발견된 이슈만 반환, 없으면 빈 배열.
  """
```

#### 2.5.3 상태 관리

- **Stateless**: 파일 단위 분석

#### 2.5.4 도구(Tools)

| 도구명 | 기능 설명 | 입력 | 출력 |
|--------|----------|------|------|
| file_reader | 파일 읽기 | path: str | content: str |
| clang_tidy_runner | clang-tidy 정적 분석 | file: str, checks: str | warnings: List[Dict] |
| grep | malloc/free 패턴 검색 | pattern: str, file: str | matches: List[Dict] |
| lsp_client | 포인터 타입 확인 | action: str, file: str, line: int | type_info: Dict |

#### 2.5.5 메모리 전략

- **메모리 유형**: None
- **저장 전략**: AnalysisResult만 반환
- **RAG**: 2차 PoC 예정 (resources/knowledge_base/c/)

#### 2.5.6 기술 스택

| 구분 | 선정 기술 | 사유 |
|------|----------|------|
| LLM Model | gpt-4o (No Fallback) | 복잡한 포인터 분석, 128k |
| Agent Framework | Python Class | 순차 실행 |
| Prompt Strategy | CoT + Few-Shot | 단계별 추론 |
| Output Parsing | JSON Mode | 구조화된 이슈 |
| Monitoring | logging + token counting | 비용 추적 |

---

### Agent 6: ProCAnalyzerAgent

#### 2.6.1 페르소나 (Identity)

| 항목 | 정의 내용 |
|------|----------|
| Agent 이름 | ProCAnalyzerAgent |
| 주요 역할 | Pro*C 파일 전문 분석. Oracle proc 프리컴파일러 + LLM 심층 분석(EXEC SQL, SQLCA, INDICATOR)을 결합하여 데이터 무결성 위협 이슈 탐지 |
| 핵심 목표 | - SQLCA 에러 체크 누락 탐지<br>- INDICATOR 변수 누락 탐지 (NULL 처리)<br>- 커서 라이프사이클 검증 (OPEN/CLOSE 짝)<br>- 트랜잭션 무결성 검사 (COMMIT/ROLLBACK) |
| 톤앤매너 | DB 전문가, 데이터 관리자 스타일. 트랜잭션 영향도 설명 |
| 제약 사항 | - DB 연결 불가 (정적 분석만)<br>- EXPLAIN PLAN 실행 불가 |

#### 2.6.2 워크플로우

**Step 1: Static Analysis (Oracle proc)**
```
proc_runner(
  file=file_path,
  include_dirs=["/usr/include/oracle"]
)
결과:
  errors: [
    {"line": 89, "message": "SQLCA check missing after UPDATE"},
    ...
  ]
```

**Step 2: EXEC SQL Extraction**
```
sql_extractor(file_path)
결과:
  sql_blocks: [
    {
      "id": 0,
      "sql": "UPDATE ORDERS SET STATUS = :status WHERE ORDER_ID = :id",
      "host_variables": [":status", ":id"],
      "indicator_variables": [],
      "has_sqlca_check": false
    },
    ...
  ]
```

**Step 3: LLM Analysis (토큰 최적화: Structure + Function Window)**
```
경로 A (errors 있거나 sqlca_check 누락):
  # 토큰 최적화: 파일 전체 대신 구조 요약 + 에러 포함 함수만 전달
  structure_summary = _build_structure_summary(file_context)
  error_functions = _extract_error_functions(file_content, proc_errors)

  prompt = """
  Pro*C 전문가입니다.

  [proc 오류]
  {proc_errors}

  [EXEC SQL 블록]
  {sql_blocks}

  [구조 요약]
  {structure_summary}

  [에러 관련 함수 전체 코드]
  {error_functions}

  [파일 컨텍스트]
  {file_context}

  분석:
  1. SQLCA 체크 누락 (각 EXEC SQL 후)
  2. INDICATOR 변수 누락 (NULL 가능 컬럼)
  3. 커서 누수 (CLOSE 누락)
  4. 트랜잭션 흐름 (예외 경로)

  출력: JSON (AnalysisResult 스키마)
  """

경로 B (Heuristic):
  # 토큰 최적화: 500줄 이하 전체, 초과 시 head+tail+구조요약
  file_content_optimized = _optimize_file_content(file_content, file_context)

  prompt = """
  [파일 코드]
  {file_content_optimized}

  [EXEC SQL 블록]
  {sql_blocks}

  [휴리스틱]
  - 트랜잭션 무결성 (예외 경로)
  - 커서 누수 (복잡한 흐름)
  - 데드락 가능성
  - 데이터 정합성 (비즈니스 룰)

  발견된 이슈만 반환.
  """
```

#### 2.6.3 상태 관리

- **Stateless**: 파일 단위 분석

#### 2.6.4 도구(Tools)

| 도구명 | 기능 설명 | 입력 | 출력 |
|--------|----------|------|------|
| file_reader | 파일 읽기 | path: str | content: str |
| proc_runner | Oracle proc 프리컴파일 | file: str, include_dirs: List[str] | errors: List[Dict], success: bool |
| sql_extractor | EXEC SQL 블록 추출 | file: str | sql_blocks: List[Dict] |
| grep | SQLCA, INDICATOR 패턴 검색 | pattern: str, file: str | matches: List[Dict] |

#### 2.6.5 메모리 전략

- **메모리 유형**: None
- **저장 전략**: AnalysisResult만 반환
- **RAG**: 2차 PoC 예정 (resources/knowledge_base/proc/)

#### 2.6.6 기술 스택

| 구분 | 선정 기술 | 사유 |
|------|----------|------|
| LLM Model | gpt-4o (No Fallback) | EXEC SQL 복잡 분석, 128k |
| Agent Framework | Python Class | 순차 실행 |
| Prompt Strategy | CoT + Few-Shot | 단계별 추론, SQLCA 예시 |
| Output Parsing | JSON Mode | 구조화된 이슈 |
| Monitoring | logging + token counting | 비용 추적 |

---

### Agent 7: SQLAnalyzerAgent

#### 2.7.1 페르소나 (Identity)

| 항목 | 정의 내용 |
|------|----------|
| Agent 이름 | SQLAnalyzerAgent |
| 주요 역할 | SQL 파일 전문 분석. 정적 패턴 분석 + LLM 심층 분석(인덱스 억제, Full Scan)을 결합하여 성능 저하 패턴 탐지 |
| 핵심 목표 | - Full Table Scan 탐지<br>- 인덱스 억제 패턴 탐지 (함수, OR 조건)<br>- N+1 쿼리 패턴 탐지<br>- SELECT * 사용 경고 |
| 톤앤매너 | DBA, 성능 튜너 스타일. 응답 시간 영향도 수치화 |
| 제약 사항 | - DB 연결 불가 (EXPLAIN PLAN 실행 불가)<br>- 정적 분석만 (실제 실행 X) |

#### 2.7.2 워크플로우

**Step 1: Static Pattern Analysis**
```
정적 패턴 검색:
  1. SELECT * 사용
  2. WHERE 절에 함수 사용 (YEAR(), UPPER() 등)
  3. OR 조건 다중 사용
  4. JOIN 없이 서브쿼리
  5. LIKE '%keyword%' (앞 와일드카드)
결과:
  patterns: [
    {"pattern": "index_suppression", "line": 15, "detail": "YEAR() function"},
    ...
  ]
```

**Step 2: LLM Analysis (토큰 최적화: Structure + Function Window)**
```
경로 A (patterns 있을 때):
  # 토큰 최적화: 파일 전체 대신 구조 요약 + 패턴 매치된 SQL 문 전체 추출
  structure_summary = _build_structure_summary(file_context)
  error_queries = _extract_error_queries(file_content, patterns)
    # - 패턴이 매치된 SQL 문(SELECT/INSERT/UPDATE/DELETE) 전체 추출
    # - 프로시저/함수 내 패턴이면 해당 프로시저/함수 전체 추출

  prompt = """
  SQL 성능 전문가입니다.

  [정적 패턴]
  {patterns}

  [구조 요약]
  {structure_summary}

  [패턴 매치된 SQL 문 전체]
  {error_queries}

  분석:
  1. 패턴별 성능 영향도 (응답 시간)
  2. 인덱스 추천
  3. 쿼리 재작성 제안

  출력: JSON (AnalysisResult 스키마)
  """

경로 B (Heuristic):
  # SQL 파일은 보통 소형이므로 전체 유지, 대형 파일만 truncation
  file_content_optimized = _optimize_file_content(file_content, file_context)

  prompt = """
  [SQL 파일]
  {file_content_optimized}

  [휴리스틱]
  - 인덱스 억제 패턴
  - Full Table Scan 가능성
  - N+1 쿼리
  - 비효율적 JOIN

  발견된 이슈만 반환.
  """
```

#### 2.7.3 상태 관리

- **Stateless**: 파일 단위 분석

#### 2.7.4 도구(Tools)

| 도구명 | 기능 설명 | 입력 | 출력 |
|--------|----------|------|------|
| file_reader | 파일 읽기 | path: str | content: str |
| grep | SQL 패턴 검색 | pattern: str, file: str | matches: List[Dict] |

#### 2.7.5 메모리 전략

- **메모리 유형**: None
- **저장 전략**: AnalysisResult만 반환
- **RAG**: 2차 PoC 예정 (resources/knowledge_base/sql/)

#### 2.7.6 기술 스택

| 구분 | 선정 기술 | 사유 |
|------|----------|------|
| LLM Model | gpt-4o-mini (Fallback: gpt-4o) | 정적 패턴 분석, 경량 모델 충분 |
| Agent Framework | Python Class | 순차 실행 |
| Prompt Strategy | CoT + Few-Shot | 성능 영향도 추론, 최적화 예시 |
| Output Parsing | JSON Mode | 구조화된 이슈 |
| Monitoring | logging | 비용 추적 |

---

### Agent 8: ReporterAgent

#### 2.8.1 페르소나 (Identity)

| 항목 | 정의 내용 |
|------|----------|
| Agent 이름 | ReporterAgent |
| 주요 역할 | Phase 2의 모든 AnalysisResult를 통합하여 3개 리포트(IssueList, Checklist, Summary) 생성 |
| 핵심 목표 | - 심각도별 분류 (Critical/High/Medium/Low)<br>- Before/After 코드 1-3줄 추출<br>- 검증 명령어 체크리스트 생성<br>- 기본 통계 요약 (파일별, 심각도별) |
| 톤앤매너 | 분석가, 보고서 작성자 스타일. 객관적, 팩트 기반 |
| 제약 사항 | - 전체 파일 Diff 생성 금지 (비용 절감)<br>- Before/After 1-3줄만 추출<br>- 체크리스트에 "checked" 필드 없음 (개발자 수동) |

#### 2.8.2 워크플로우

**Step 1: Severity Classification**
```
For each AnalysisResult:
  1. 심각도 기준 적용
     Critical: 즉시 크래시, 데이터 손상, 보안 취약점
     High:     특정 조건에서 장애
     Medium:   성능 저하
     Low:      코드 품질
  2. 분류
     issues_by_severity = {
       "critical": [...],
       "high": [...],
       "medium": [...],
       "low": [...]
     }
```

**Step 2: Code Extraction**
```
For each issue:
  1. Before 코드 추출 (1-3줄)
     line_start = issue.location.line_start
     line_end = min(line_start + 2, issue.location.line_end)
     before_code = file_lines[line_start:line_end+1]
  2. After 코드 (LLM 제공)
     after_code = issue.fix.after  # 이미 1-3줄
  3. IssueList에 포함
```

**Step 3: Checklist Generation**
```
checklist_generator Tool 호출:
For each severity in ["critical", "high"]:
  For each issue in issues[severity]:
    {
      "id": f"CHECK-{n}",
      "category": issue.category,
      "description": f"모든 {issue.pattern} 수정 완료",
      "related_issues": [issue.issue_id],
      "verification_command": generate_command(issue),
      "expected_result": "..."
    }

verification_command 예시:
  - C strcpy: "grep -n 'strcpy' service/calc.c"
  - ProC SQLCA: "proc batch/process.pc"
  - JS cleanup: "grep 'removeEventListener' ui/order.js"
```

**Step 4: Summary Generation**
```json
{
  "analysis_metadata": {...},
  "issue_summary": {
    "total": "len(all_issues)",
    "by_severity": {...},
    "by_category": {...},
    "by_layer": {...}
  },
  "risk_assessment": {
    "deployment_risk": "HIGH if critical > 0",
    "blocking_issues": ["critical_issue_ids"],
    "deployment_allowed": "critical == 0"
  },
  "estimated_effort": {
    "critical_fixes_minutes": "sum(...)",
    "total_minutes": "sum(...)"
  }
}
```

#### 2.8.3 상태 관리

- **Stateless**: 모든 AnalysisResult → 3개 JSON 파일

#### 2.8.4 도구(Tools)

| 도구명 | 기능 설명 | 입력 | 출력 |
|--------|----------|------|------|
| checklist_generator | 이슈 기반 체크리스트 자동 생성 | results: List[AnalysisResult] | checklist: Dict |

#### 2.8.5 메모리 전략

- **메모리 유형**: None
- **저장 전략**: 3개 JSON 파일만 생성
- **RAG**: 사용 안 함 (AnalysisResult 통합만)

#### 2.8.6 기술 스택

| 구분 | 선정 기술 | 사유 |
|------|----------|------|
| LLM Model | gpt-4o-mini (temp 0.3, Fallback: gpt-4o) | 간단한 요약, 한국어 |
| Agent Framework | Python Class | 순차 처리 |
| Prompt Strategy | Instruction-based (금지 사항 명시) | "전체 Diff 생성 금지" 강조 |
| Output Parsing | JSON Mode (3개 스키마) | IssueList, Checklist, Summary |
| Monitoring | logging | 생성 시간 추적 |

---

## 3. 개발 환경

### 3.1 필수 의존성 (1차 PoC)

```txt
# Python 패키지
openai>=1.0.0
httpx==0.28.0
pydantic==2.10.0
rich==13.9.0              # 터미널 UI (Progress Bar)

# 정적 분석 도구 (Portable 바이너리)
node-v20 (ESLint 포함)
clang-tidy-18
oracle-proc-21c
```

### 3.2 2차 PoC 추가 의존성

```txt
# RAG 관련
langchain==0.3.0
chromadb==0.5.0
sentence-transformers==3.3.0

# Context 압축
tiktoken
```

### 3.3 프로젝트 구조 (1차 PoC)

```
mider/
├── agents/
│   ├── __init__.py
│   ├── orchestrator.py          # OrchestratorAgent
│   ├── task_classifier.py       # TaskClassifierAgent
│   ├── context_collector.py     # ContextCollectorAgent
│   ├── js_analyzer.py           # JavaScriptAnalyzerAgent
│   ├── c_analyzer.py            # CAnalyzerAgent
│   ├── proc_analyzer.py         # ProCAnalyzerAgent
│   ├── sql_analyzer.py          # SQLAnalyzerAgent
│   └── reporter.py              # ReporterAgent
├── tools/
│   ├── __init__.py
│   ├── file_io/
│   │   └── file_reader.py
│   ├── search/
│   │   ├── grep.py
│   │   ├── glob.py
│   │   └── ast_grep_search.py
│   ├── static_analysis/
│   │   ├── eslint_runner.py
│   │   ├── clang_tidy_runner.py
│   │   └── proc_runner.py
│   ├── lsp/
│   │   └── lsp_client.py
│   └── utility/
│       ├── sql_extractor.py
│       ├── checklist_generator.py
│       ├── task_planner.py
│       └── dependency_resolver.py
├── models/
│   ├── __init__.py
│   ├── execution_plan.py        # ExecutionPlan 스키마
│   ├── file_context.py          # FileContext 스키마
│   ├── analysis_result.py       # AnalysisResult 스키마
│   └── report.py                # IssueList, Checklist, Summary
├── config/
│   ├── settings.yaml
│   └── prompts/
│       ├── orchestrator.txt
│       ├── js_analyzer_error_focused.txt
│       ├── js_analyzer_heuristic.txt
│       ├── c_analyzer_error_focused.txt
│       ├── c_analyzer_heuristic.txt
│       ├── proc_analyzer_error_focused.txt
│       ├── proc_analyzer_heuristic.txt
│       ├── sql_analyzer_error_focused.txt
│       └── sql_analyzer_heuristic.txt
├── resources/
│   ├── binaries/                # ESLint, clang-tidy, proc portable
│   └── lint-configs/
│       ├── .eslintrc.json
│       └── .clang-tidy
├── output/                      # 분석 결과 출력 디렉토리
├── main.py                      # 프로그램 진입점
├── requirements.txt
└── README.md
```

### 3.4 LLM Model 요약

| Agent | Primary Model | Fallback | Temperature |
|-------|--------------|----------|-------------|
| OrchestratorAgent | gpt-4o | gpt-4-turbo | 0.3 |
| TaskClassifierAgent | gpt-4o-mini | gpt-4o | 0.0 |
| ContextCollectorAgent | gpt-4o-mini | gpt-4o | 0.0 |
| JavaScriptAnalyzerAgent | gpt-4o | - | 0.0 |
| CAnalyzerAgent | gpt-4o | - | 0.0 |
| ProCAnalyzerAgent | gpt-4o | - | 0.0 |
| SQLAnalyzerAgent | gpt-4o-mini | gpt-4o | 0.0 |
| ReporterAgent | gpt-4o-mini | gpt-4o | 0.3 |

---

## 4. PoC 로드맵

### 1차 PoC (현재)
- 정적 분석 (ESLint, clang-tidy, proc) + LLM 하이브리드
- 7개 Agent (Orchestrator + 6 Sub-agents)
- CLI 기반 실행
- 폐쇄망 실행파일 패키징
- 토큰 최적화 (Structure + Function Window)

### 2차 PoC (예정)
- Session Resume (세션 저장/복구, Checkpoint)
- RAG (Knowledge Base + ChromaDB + sentence-transformers)
- KnowledgeRetrieverAgent 추가
- LangFlow 서버 연동
