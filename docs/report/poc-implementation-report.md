# 핵심 구현 내용

이번 PoC 단계에서 실제 코드로 구현된 핵심 기능들을 동작 원리와 사용 기술 중심으로 상세히 기술합니다.

## 1.1 에이전트 워크플로우 (Agent Workflow)

### 1.1.1 전체 워크플로우 제어 (OrchestratorAgent)

**구현 기능:**
- 4-Phase 파이프라인 순차 실행 및 Sub-Agent 호출 관리
- 세션 관리 (UUID 기반 session_id), 진행률 콜백 (Rich Progress Bar 연동)
- 파일 검증: glob 패턴 확장, 중복 제거, 확장자/권한 검증
- 언어별 Analyzer 자동 라우팅 및 인스턴스 캐싱

**동작 원리:**
```
입력: 분석 대상 파일 경로 리스트 (glob 패턴 포함 가능)
  ↓
[파일 검증] glob 패턴 확장 → 중복 제거 → 절대경로 정규화 → 확장자/권한 검증
  ↓
[Phase 0] TaskClassifierAgent 호출
  → 파일 분류 + 의존성 분석 + 실행 순서 결정 → ExecutionPlan 생성
  ↓
[Phase 1] ContextCollectorAgent 호출
  → import/include 추출 + 함수 호출 매핑 + 코드 패턴 탐지 → FileContext 수집
  ↓
[Phase 2] 언어별 AnalyzerAgent 호출 (JS/C/Pro*C/SQL/XML)
  → _LANGUAGE_AGENT_MAP으로 자동 라우팅
  → 정적분석 + LLM 하이브리드 분석 → AnalysisResult[] 반환
  ↓
[Phase 3] ReporterAgent 호출
  → 4개 JSON 리포트 생성 (IssueList, Checklist, Summary, DeploymentChecklist)
  → 위험도 판정 (RiskAssessment)
```

**주요 기술:**
- Python `asyncio` 기반 비동기 실행
- `ProgressCallback` 프로토콜: 각 Phase마다 콜백 호출하여 Rich Progress Bar와 연동
- `_LANGUAGE_AGENT_MAP` 딕셔너리: 언어→Analyzer 매핑 + 인스턴스 캐싱 (동일 언어 재사용)
- Pydantic v2 Structured Output: 모든 Phase 간 데이터를 Pydantic 모델로 타입 안전하게 전달

### 1.1.2 파일 분류 및 실행 계획 (TaskClassifierAgent — Phase 0)

**구현 기능:**
- 파일 확장자 기반 언어 분류 + 의존성 그래프 구축 (토폴로지 정렬)
- LLM(gpt-4o-mini) 우선순위 보정: 파일 내용 분석 → 위험도 높은 파일 우선 분석
- ExecutionPlan 생성 (sub_tasks, dependencies, estimated_time)

**동작 원리:**
```
입력: 파일 경로 리스트
  ↓
[Step 1] DependencyResolver → import/include 기반 의존성 edges 추출
  ↓
[Step 2] TaskPlanner → 토폴로지 정렬 + 메타데이터 생성 → sub_tasks[]
  ↓
[Step 3] LLM 우선순위 보정 (gpt-4o-mini)
  → 파일 내용 샘플링 (500줄 초과 시 head 250 + tail 250)
  → critical 패턴 탐지 → priority 재배열
  → 실패 시 Graceful degradation (Tool 결과만 사용)
  ↓
출력: ExecutionPlan {sub_tasks[], dependencies, total_files, estimated_time_seconds}
```

**주요 기술:**
- gpt-4o-mini (비용 절감) + fallback: gpt-4o
- 토폴로지 정렬: 순환 의존성 감지 시 warnings 배열에 기록
- 대형 파일 샘플링: head(250줄) + tail(250줄)로 토큰 절약

### 1.1.3 컨텍스트 수집 (ContextCollectorAgent — Phase 1)

**구현 기능:**
- 언어별 정규식으로 import/include 관계 추출 (JS: import/require, C: #include, Pro*C: EXEC SQL INCLUDE)
- 함수 호출 매핑: 호출 함수명 + 대상 파일 추정
- 코드 패턴 탐지: error_handling, logging, memory_management, transaction, event_binding
- LLM 보정: Tool 결과 + LLM 분석 결과 병합

**동작 원리:**
```
입력: ExecutionPlan + 파일 목록
  ↓
[Step 1] Tool 기반 수집 (파일별 병렬)
  → _extract_imports(): 언어별 정규식으로 import 문 추출 + _resolve_path()로 경로 매칭
  → _extract_calls(): 함수 호출 패턴 추출 (예약어 필터링: if, for, sizeof 등)
  → _detect_patterns(): 위험 패턴 매칭 (C: malloc/free/strcpy, JS: innerHTML/eval)
  ↓
[Step 2] 공통 패턴 집계 → _aggregate_patterns() → Counter
  ↓
[Step 3] LLM 보정 (gpt-4o-mini)
  → Tool 결과 + 파일 내용 → LLM에 전달
  → _merge_results(): Tool 우선 + LLM 보강 (target_file 추가, 새 패턴 보완)
  ↓
출력: FileContext {file_contexts[], dependencies, common_patterns}
```

**주요 기술:**
- 언어별 정규식 패턴 (JS: `import\s+.*\s+from\s+['"](.+)['"]`, C: `#include\s*[<"](.+)[>"]`)
- `_CALL_SKIP_KEYWORDS` 집합: 예약어 필터링으로 false positive 방지
- XML 전용: `scwin.funcName` 패턴에서 이벤트 핸들러 추출

### 1.1.4 C 코드 2-Pass 분석 (CAnalyzerAgent — Phase 2)

**구현 기능:**
- clang-tidy 정적분석 + LLM 심층분석 하이브리드
- Heuristic Pre-Scanner: regex 6종 패턴으로 전체 파일 사전 스캔 (비용 0)
- 2-Pass 전략: 위험 함수 선별(Pass 1) → 함수별 개별 LLM 호출(Pass 2)
- asyncio.gather + Semaphore(3)로 병렬 LLM 호출

**동작 원리:**
```
경로 분기:
  IF clang-tidy 결과 있음 → Error-Focused 경로
  ELIF 파일 > 500줄 AND clang-tidy 없음 → 2-Pass 전략
  ELSE → Heuristic 단일 패스

[2-Pass 전략 상세]
Pass 1-a: CHeuristicScanner (regex, 비용 0)
  → 6종 패턴 스캔: UNINIT_VAR, UNSAFE_FUNC, BOUNDED_FUNC,
                    NULL_DEREF, UNCHECKED_RET, BUFFER_ACCESS
  → 함수별 위험 패턴 요약 생성
  ↓
Pass 1-b: gpt-4o-mini 호출
  → 함수 시그니처 + 위험 패턴 요약 전달 → risky_functions[] 선별
  ↓
Pass 2: 선별된 함수별 개별 gpt-4o 호출 (최대 3개 병렬)
  → _analyze_single_function(): 함수 코드 추출 → 경고 그룹화 → LLM 심층분석
  → 결과 합산 → issue_id 재번호 (C-001부터 순차)
```

**주요 기술:**
- `find_function_boundaries()`: C 함수 경계 식별 (중괄호 카운팅, 문자열/주석 무시)
- `build_structure_summary()`: 헤더 + 타입 정의만 추출하여 구조 요약 생성
- `asyncio.gather()` + `asyncio.Semaphore(3)`: 동시 LLM 호출 수 제한
- `_build_grouped_warnings()`: HIGH 우선순위 패턴 선별 (clang-tidy 대체)

### 1.1.5 SQL 성능 분석 + Explain Plan 연동 (SQLAnalyzerAgent — Phase 2)

**구현 기능:**
- SQL 문법 검증 (sqlparse) + 정적 패턴 검색 + LLM 분석
- Oracle Explain Plan 파싱 → 튜닝 포인트 자동 추출
- 정적 이슈 자동 생성: CARTESIAN JOIN(Critical), Full Table Scan(High), PK 인덱스 비효율(High)
- LLM 이슈와 정적 이슈 병합 (중복 제거)

**동작 원리:**
```
입력: SQL 파일 + (선택) Explain Plan 파일
  ↓
[Step 1] 파일 읽기 + 토큰 추정 (> 100K 토큰 warning)
  ↓
[Step 2] SQLSyntaxChecker → syntax_errors/warnings 추출
  ↓
[Step 3] AstGrepSearch → 정적 패턴 검색
  → select_star, function_in_where, like_wildcard, subquery 등
  ↓
[Step 4] ExplainPlanParser → steps[] + tuning_points[] 추출
  → 대형 Plan(100+ steps): Cost≥50만 필터링, 상위 20개만 프롬프트에 포함
  ↓
[Step 5] LLM 분석 (Error-Focused 또는 Heuristic)
  ↓
[Step 6] _generate_static_issues(): 튜닝 포인트 → 정적 이슈 자동 생성
  → MERGE JOIN CARTESIAN → Critical
  → TABLE ACCESS FULL → High
  → PK 인덱스 RANGE SCAN (Cost≥100) → High + 인덱스 힌트 제안
  ↓
[Step 7] _merge_issues(): LLM 이슈 ∪ (정적 이슈 \ 중복)
  → 같은 object 발견 시 LLM 우선, issue_id 재번호 SQL-001부터
```

**주요 기술:**
- `sqlparse` 라이브러리: SQL 문법 검증
- `ExplainPlanParser`: 텍스트 덤프 → 구조화된 step + tuning_point 파싱
- `_extract_join_columns()`: Predicate에서 조인 컬럼 자동 추출 → 인덱스 힌트 생성
- `_merge_issues()`: 객체 이름 베이스명 추출 (PK 접미사 제거) → 중복 제거

### 1.1.6 리포트 생성 및 위험도 판정 (ReporterAgent — Phase 3)

**구현 기능:**
- 4개 통합 JSON 리포트: IssueList, Checklist, Summary, DeploymentChecklist
- 위험도 자동 판정 (RiskAssessment): critical 존재 → 배포 차단
- LLM으로 한국어 위험도 설명 생성

**동작 원리:**
```
입력: AnalysisResult[] (Phase 2 결과)
  ↓
[이슈 통합] _collect_all_issues() → 심각도 + 파일명순 정렬
  ↓
[IssueList] 심각도별 카운트, IssueListItem 배열
[Checklist] ChecklistGenerator Tool → 분석별 체크항목
[Summary] 메타데이터 + 이슈 요약 + RiskAssessment
  → critical > 0 → 배포 불가
  → high ≥ 3 → 배포 불가
  → high ≥ 1 → 주의 필요
  → 그 외 → 배포 가능
[DeploymentChecklist] 배포 전 사전 점검 항목 자동 생성
```

**주요 기술:**
- Pydantic v2 모델: IssueList, Checklist, Summary, DeploymentChecklist
- Graceful degradation: LLM 실패 → `_default_risk_description()` 사용

---

## 1.2 도구(Tool) 및 함수 연동

### 1.2.1 파일 읽기 (FileReader)

**구현 기능:**
- 파일 내용 읽기 및 메타데이터(라인 수, 인코딩, 파일 크기) 반환
- 이중 인코딩 자동 감지 (UTF-8 → EUC-KR 폴백)

**동작 원리:**
```
입력: file (파일 경로)
  ↓
[Step 1] Path 존재/권한 확인
  ↓
[Step 2] 인코딩 시도: UTF-8 → UnicodeDecodeError 발생 시 EUC-KR 자동 전환
  ↓
[Step 3] 메타데이터 계산: line_count, file_size, encoding
  ↓
출력: ToolResult {content, line_count, encoding, file_size}
```

**주요 기술:**
- 폐쇄망 한글 대응: EUC-KR 자동 폴백 (chardet 등 외부 라이브러리 불필요)
- BaseTool 상속: ToolExecutionError 표준 예외 처리

### 1.2.2 정적 분석 도구 연동

| 도구 | 대상 | 연동 방식 |
|------|------|---------|
| ESLint | JavaScript | `subprocess` 호출 → JSON 출력 파싱 |
| clang-tidy | C | `subprocess` 호출 → 텍스트 출력 파싱 |
| Oracle proc | Pro\*C | `subprocess` 호출 → PCC 에러 코드 파싱 |
| sqlparse | SQL | Python 라이브러리 직접 호출 |
| CHeuristicScanner | C | 내장 regex 6종 패턴 스캔 (비용 0) |
| XMLParser | XML | ElementTree 기반 파싱 (XXE 방어 포함) |

### 1.2.3 코드 검색 도구

| 도구 | 기능 | 기술 |
|------|------|------|
| GlobTool | 파일 패턴 매칭 | `pathlib.Path.glob()` |
| GrepTool | 코드 내 텍스트 검색 | `re` 정규식 매칭 |
| AstGrepSearch | AST 기반 패턴 검색 | 규칙 기반 패턴 매칭 (SQL: select_star, function_in_where 등) |

---

## 1.3 데이터 및 메모리

### 1.3.1 Pydantic 데이터 스키마

| 모델 | Phase | 역할 |
|------|-------|------|
| ExecutionPlan | 0→1 | 파일 분류, 의존성, 실행 순서 |
| FileContext | 1→2 | import/call/pattern 컨텍스트 |
| AnalysisResult | 2→3 | 분석 이슈 목록, 토큰 사용량 |
| Report (IssueList, Summary 등) | 3→출력 | 최종 리포트 4종 |

### 1.3.2 LLM Context 관리

**구현 기능:**
- OpenAI / Azure OpenAI API 통합 래퍼 (LLMClient)
- JSON Mode (Structured Output) 지원
- 토큰 최적화: 대형 파일 분할 전략 + 구조 요약

**동작 원리:**
```
[클라이언트 자동 선택]
  IF AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT → AsyncAzureOpenAI
  ELIF OPENAI_API_KEY → AsyncOpenAI
  ELSE → EnvironmentError

[토큰 최적화 전략]
  1. 구조 요약 (Structure Summary)
     → 파일 전체 대신 헤더 + 타입 정의 + 함수 시그니처만 전송 (~80% 토큰 절감)
  2. 에러 함수 추출 (Error Functions)
     → 정적분석 경고가 있는 함수만 전체 코드 전송
  3. 대형 파일 샘플링
     → 500줄 초과: head(200) + tail(100) + 구조 요약
  4. 함수별 개별 호출 (2-Pass)
     → 한 번에 전체 코드 전송 대신 함수 단위 분할 호출

[모델 사용 전략]
  → 분류/컨텍스트/리포트: gpt-4o-mini (비용 절감)
  → 심층 분석: gpt-4o (정확도 우선)
  → 실패 시 fallback 모델 자동 전환
```

**주요 기술:**
- `httpx` + `openai` 비동기 클라이언트 (AsyncOpenAI / AsyncAzureOpenAI)
- `response_format = {"type": "json_object"}`: Structured Output 보장
- `response.usage.total_tokens`: 토큰 사용량 추적 → Summary에 누적 기록

---

# 주요 문제 해결 및 기술 리서치

구현 과정에서 마주친 기술적 문제와 이를 해결하기 위해 찾아본 자료(리서치) 및 적용한 방법을 기록합니다.

| 이슈 구분 | 문제 상황 및 원인 | 리서치 및 해결 과정 (Reference & Solution) |
|----------|----------------|----------------------------------------|
| **Problem** (C분석 누락) | clang-tidy 미설치 시 Heuristic 경로에서 2932줄 파일의 앞 200줄 + 뒤 100줄만 LLM에 전달. 중간 2383~2462줄의 `svc_cnt` 초기화 누락 버그를 탐지하지 못함 | **2-Pass 분석 전략 도입**: (1) regex 6종 패턴으로 전체 파일 사전 스캔(비용 0) → 위험 함수 목록 추출, (2) gpt-4o-mini로 위험 함수 선별(Pass 1), (3) 선별된 함수만 gpt-4o로 심층 분석(Pass 2). 코드 커버리지 ~10% → 100%로 개선 |
| **Problem** (LLM 지배) | Pass 2에서 4개 함수(c100: 636줄, c200: 1115줄, c400: 127줄, c700: 164줄)를 한 번에 전달 시, 대형 함수가 LLM attention을 지배하여 소형 함수(c400)의 이슈 누락. **Lost-in-the-Middle 현상** | **함수별 개별 LLM 호출**로 근본 해결: 각 함수를 별도 프롬프트로 분리 → `asyncio.gather()` + `Semaphore(3)`로 병렬 호출 → 결과 합산 후 issue_id 재번호. 입력 토큰 총량 동일하면서 정확도 향상 |
| **도구 연동** (clang-tidy 한계) | clang-tidy가 `#include <pfmcom.h>` 등 헤더 미존재 시 fatal error → Level 2 데이터 흐름 분석 불가 (메모리 안전성 이슈 탐지 0건). Level 1 텍스트 패턴만 44건 탐지 | **clang-tidy + Heuristic 병행**: clang-tidy가 있어도 항상 Heuristic Scanner를 함께 실행 → 결과 합산 + 중복 제거. Level 1(텍스트 패턴) + regex(메모리 패턴) 결합으로 커버리지 확대 |
| **Problem** (SQL 인덱스) | gpt-4o-mini가 PK 인덱스 비효율 패턴(INDEX RANGE SCAN, Cost=148)을 탐지하지 못함. 94개 튜닝 포인트 중 CARTESIAN에만 집중 | **3단계 해결**: (1) ExplainPlanParser에 PK 고비용 RANGE SCAN 자동 탐지 추가 (Cost≥100 임계값), (2) 튜닝 포인트 상위 20개만 LLM에 전달, (3) SQL Analyzer 기본 모델을 gpt-4o로 변경 → 6개 이슈 탐지 + 인덱스 힌트 제안 성공 |
| **성능/토큰** | 대형 파일(2932줄 C, 8109줄 XML) 전체를 LLM에 전달 시 토큰 초과. 128K 컨텍스트 내에서 효율적 분석 필요 | **Structure Summary + Error Functions 전략**: 파일 전체 대신 (1) 헤더+타입 정의 구조 요약, (2) 에러 포함 함수만 전체 코드 전송 → ~80% 토큰 절감. 대형 Explain Plan은 고비용 step만 필터링 |
| **보안** (XXE 공격) | `xml.etree.ElementTree.fromstring()`이 DOCTYPE/ENTITY 선언 처리 시 XXE/Billion Laughs 공격 가능. Python 3.13에서는 `parser.entity` 속성이 readonly라 기존 방어 코드 동작 불가 | DOCTYPE/ENTITY **문자열 사전 검사 방식**으로 방어: `if "<!DOCTYPE" in content or "<!ENTITY" in content` → 파싱 거부. defusedxml 미사용 (폐쇄망 의존성 최소화) |
| **도구 연동** (LSP) | LSP 서버가 initialize 핸드셰이크 없이 요청 거부. URI 경로에 공백/특수문자 포함 시 파싱 실패. 멀티 JSON-RPC 메시지 단순 split 실패 | (1) initialize→initialized→didOpen→request→shutdown 전체 시퀀스 구현, (2) `urllib.parse.urlparse` + `unquote` 사용, (3) Content-Length 기반 멀티메시지 파싱 + request_id 매칭 |

---

# 핵심 동작 검증

위에서 구현한 기능이 의도대로 동작하는지 보여주는 대표적인 실행 결과를 첨부합니다.

## 검증 시나리오 1: C 코드 2-Pass 분석 (대형 파일 위험 함수 탐지)

* **입력**: 2932줄 C 파일 (clang-tidy 미설치 환경)
* **에이전트 동작**:
    1. CHeuristicScanner 실행 (regex 6종 패턴) → 411개 위험 패턴 발견 (4개 함수에 집중)
    2. Pass 1: gpt-4o-mini 호출 → `risky_functions = [c100, c200, c400, c700]` 선별
    3. Pass 2: 4개 함수별 개별 gpt-4o 호출 (Semaphore(3)으로 3개 병렬)
       - c100(636줄) → 5개 이슈
       - c200(1115줄) → 5개 이슈
       - c400(127줄) → 2개 이슈 (**svc_cnt 미초기화 탐지 성공**)
       - c700(164줄) → 2개 이슈
    4. 결과 합산 → issue_id 재번호 (C-001 ~ C-014)
* **최종 결과**: 14개 이슈 탐지 (기존 단일 호출 방식: 10개, c400/c700 누락)

## 검증 시나리오 2: SQL Explain Plan 자동 이슈 생성

* **입력**: SQL 파일 + Oracle Explain Plan (94개 step, 33개 tuning point)
* **에이전트 동작**:
    1. ExplainPlanParser 실행 → tuning_points 추출
       - MERGE JOIN CARTESIAN (Critical) 탐지
       - ZORD_WIRE_SVC_DC_PK INDEX RANGE SCAN (Cost=148, High) 탐지
    2. _generate_static_issues() → 정적 이슈 2개 자동 생성
       - SQL-S001: CARTESIAN JOIN 성능 저하 (Critical)
       - SQL-S002: PK 인덱스 비효율 + `/*+ INDEX(alias (svc_mgmt_num)) */` 힌트 자동 제안 (High)
    3. gpt-4o LLM 분석 → 6개 이슈 탐지
    4. _merge_issues() → LLM 이슈 + 정적 이슈 병합 (중복 제거)
* **최종 결과**: 7개 이슈 (LLM 6개 + 정적 1개), 구체적 인덱스 힌트 포함

## 검증 시나리오 3: 전체 파이프라인 E2E (JS/C/SQL 혼합)

* **입력**: `files = ['app.js', 'calc.c', 'orders.sql']`
* **에이전트 동작**:
    1. **Phase 0** (TaskClassifier): 3개 파일 분류 → ExecutionPlan 생성 (JS→C→SQL 순서)
    2. **Phase 1** (ContextCollector): import/call/pattern 추출 → FileContext 생성
    3. **Phase 2** (Analyzer):
       - `app.js` → JSAnalyzerAgent → innerHTML XSS 이슈 등
       - `calc.c` → CAnalyzerAgent → 메모리 누수, 버퍼 오버플로우 등
       - `orders.sql` → SQLAnalyzerAgent → SELECT *, 인덱스 비효율 등
    4. **Phase 3** (Reporter): 4개 JSON 리포트 생성
       - IssueList: 심각도별 분류 (critical/high/medium/low)
       - Summary: RiskAssessment (critical > 0 → 배포 불가)
       - DeploymentChecklist: 배포 전 점검 항목
* **최종 결과**:
```json
{
  "session_id": "abc123...",
  "risk_assessment": {
    "overall_risk": "HIGH",
    "deployment_allowed": false,
    "risk_description": "메모리 관련 Critical 이슈가 존재하여 배포 전 반드시 수정 필요"
  },
  "total_issues": 14,
  "by_severity": {"critical": 2, "high": 5, "medium": 4, "low": 3}
}
```

## 테스트 수행 결과

| 카테고리 | 테스트 수 | 결과 |
|---------|---------|------|
| Agent 단위 테스트 | 214개 | 전체 통과 |
| Tool 단위 테스트 | 307개 | 전체 통과 |
| Schema 검증 | 43개 | 전체 통과 |
| Config 검증 | 67개 | 전체 통과 |
| CLI 검증 | 39개 | 전체 통과 |
| **합계** | **670개** | **전체 통과** |
