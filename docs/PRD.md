# PRD: Mider - 장애 예방 및 품질 강화를 위한 폐쇄망 소스코드 분석 CLI

## 1. 배경 및 문제 정의

### 현재 상황 (As-Is)
- 폐쇄망 환경에서 운영되는 통신 도메인 운영팀은 외부 인터넷 접속이 차단된 환경에서 코드 품질 검증을 수행해야 함
- IDE가 Eclipse 기반 Proframe으로 문법 체크 기능이 다소 부족

### Pain Point
1. **수동 코드 리뷰**: 시니어 개발/운영자가 코드를 일일이 검토하여 개발 기간이 길어짐
2. **배포 후 발견되는 장애**: 메모리 누수, 버퍼 오버플로우, SQLCA 에러 체크 누락 등이 프로덕션(운영기)에서 발견되어 장애로 이어짐

### 기회 요인
- LLM이 legacy system의 구조를 학습하여 변수 초기화, 오버플로우 등 장애로 이어질 수 있는 문제를 사전에 예방 가능

---

## 2. 목표 사용자 (Target User)

### Primary User: 폐쇄망 환경의 통신 도메인 개발자/운영자
- **주니어 및 신규 인력**: 시니어의 도움 없이 코드 선리뷰 가능
- **시니어 개발자 및 운영자**: 코드 리뷰에 소모하는 시간 감소, 반복적인 오류 패턴 지적 불필요

### Secondary User
- Proframe을 사용하는 공공기관/금융 등 타 시스템의 개발자/운영자

---

## 3. 프로젝트 목표

### 정성적 목표

#### 1) 배포 전 장애 예방
- Critical 이슈(크래시, 데이터 손상, 보안 취약점)를 배포 전 95% 이상 탐지
- 프로덕션 긴급 패치 건수 50% 감소

#### 2) 개발자 생산성 향상
- 코드 리뷰 대기 시간 70% 단축
- 주니어 개발자의 자율적 코드 개선 능력 향상

#### 3) 코드 품질 표준화
- 일관된 기준으로 모든 개발자 코드 검증
- 메모리 안전성, 데이터 무결성, 에러 처리 등 핵심 품질 지표 준수
- 품질 기준 학습 기회 제공

### 정량적 목표 (KPI)

| 성과 지표 | 목표 수치 | 측정 방법 | 현재 수준 |
|-----------|----------|----------|----------|
| 프로덕션 장애 감소율 | 50% 감소 | 월별 긴급 패치 건수 비교 (1년) | 확인필요 |
| 코드 리뷰 시간 단축 | 70% 단축 | 1개 파일 검토 소요 시간 측정 (파트별) | 평균 30분/파일 |
| 프로덕션 장애 해결 리드타임 감소율 | 30% 감소 | 긴급 패치(RTA) 소요 시간 측정 | 확인필요 |
| Critical 이슈 탐지율 | 95% 이상 | AI가 탐지한 Critical 이슈 중 실제 장애 유발 가능성 개발자가 추가 검증 | N/A (신규) |
| 사용자 도구 만족도 | NPS 50 이상 | 월 1회 설문 조사 | N/A (신규) |

---

## 4. 핵심 기능 정의

### 4.1 Multi-Agent 아키텍처

Orchestration Agent와 7개의 SubAgents:

| Agent | 역할 | 분석 내용 |
|-------|------|----------|
| TaskClassifierAgent | 작업 분류 | 파일 언어 식별, 우선순위 결정 |
| ContextCollectorAgent | 컨텍스트 수집 | import/include 추적, 의존성 매핑 |
| JavaScriptAnalyzerAgent | JS 전문가 | 클로저 오류, 메모리 누수, XSS 취약점 |
| CAnalyzerAgent | C 전문가 | 버퍼 오버플로우, 포인터 안전성, 메모리 누수 |
| ProCAnalyzerAgent | Pro*C 전문가 | EXEC SQL 오류, SQLCA 체크, INDICATOR 누락 |
| SQLAnalyzerAgent | SQL 전문가 | 인덱스 억제 패턴, Full Table Scan, N+1 쿼리 |
| ReporterAgent | 리포트 생성 | 한국어 설명, Before/After 코드, 체크리스트 |

### Tool Lists

```
File I/O (1개)
├── file_reader

Search (3개)
├── grep
├── glob
└── ast_grep_search

Static Analysis (3개)
├── eslint_runner
├── clang_tidy_runner
└── proc_runner

LSP (1개)
└── lsp_client

Utility (4개)
├── sql_extractor
├── checklist_generator
├── task_planner
└── dependency_resolver
```

### 4.2 선택된 파일 분석 기능
- 개발자가 지정한 파일들만 정밀 분석 (프로젝트 전체 스캔 X)
- 언어별 전문 Agent 자동 할당 (JS/C/Pro*C/SQL)
- 정적 분석(ESLint, clang-tidy, proc) 먼저 한 후에, LLM 정밀 분석 (하이브리드)

### 4.3 구체적인 코드 수정 제안 기능
- Before/After 코드 수정 제안
- 심각도별 분류 (Critical/High/Medium/Low)
- 검증 체크리스트 제공 (사용자가 리스트 확인 후 수동 체크 필요)

### 4.4 Session Resume 기능
- CLI 명령어를 통해 LLM 분석 재개 가능
- 자동 체크포인트: Phase 0, 1, 각 파일 분석 완료 시점 저장

### 4.5 폐쇄망 환경 최적화
- 외부 인터넷 없이 즉시 실행 가능한 실행파일(.exe) 형태로 개발
- 제로 설정: pip install, npm install 불필요

---

## 5. 기대 효과

### 업무 효율화
- 코드 리뷰 시간 70% 단축: 코드 구문 자동 분석을 통해 비즈니스 로직 검토에만 집중 가능
- 재작업 시간 50% 감소: Before/After 코드 제공으로 수정 방법 명확

### 품질 향상
- Critical 이슈 95% 이상 사전 탐지
- 일관된 코드 품질 기준 적용

### 비용 절감
- 프로덕션 장애 50% 감소: 월 10건 → 5건

---

## 6. 범위 및 제약사항

### In Scope

#### 분석대상
- 선택한 파일만
- 4개 언어 지원: JavaScript, C, Pro*C, SQL
- 정적 분석 + LLM 하이브리드
  - ESLint (JavaScript)
  - clang-tidy (C)
  - Oracle proc (Pro*C)
  - 정적 패턴 분석 (SQL)

#### 분석범위
- 메모리 안전성 (오버플로우), 변수 초기화, 변수 데이터 포맷 체크 등
- 데이터 무결성 (SQLCA 에러 체크)
- 에러 처리 (Promise rejection, 예외 처리 누락)

### Out of Scope
- 프로젝트 전체 스캔 불가 (현재 운영 시스템의 파일 수정 방식이 단건 파일 수정 방식)
- 추가 언어 미지원
- 동적 분석 미지원 (실제 코드 실행 X)
- DB 연동 X
- Eclipse Plugin 미제공
- RAG 연동

### 제약사항
- 폐쇄망 환경으로 정해진 LLM model만 사용 가능 (현재는 GPT-4o)
- Linux/Windows 지원
