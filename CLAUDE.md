# Mider - 프로젝트 지침

> 폐쇄망 소스코드 분석 CLI (Multi-Agent, 정적분석 + LLM 하이브리드)

---

## 프로젝트 개요

- **서비스명**: Mider
- **목적**: 폐쇄망 환경에서 JS/C/Pro*C/SQL 코드의 장애 유발 패턴을 사전 탐지
- **아키텍처**: OrchestratorAgent + 7개 Sub-Agent (Multi-Agent)
- **Phase 흐름**: Phase 0 (분류) → Phase 1 (컨텍스트) → Phase 2 (분석) → Phase 3 (리포트)

## 기술 스택

- **언어**: Python 3.11+
- **LLM**: OpenAI API (gpt-4o, gpt-4o-mini)
- **패키지**: openai, httpx, pydantic, rich
- **정적 분석**: ESLint (JS), clang-tidy (C), Oracle proc (Pro*C)
- **출력 형식**: JSON (Structured Output)

## 프로젝트 구조

```
mider/
├── agents/          # Agent 구현 (orchestrator, analyzer 등)
├── tools/           # Tool 구현 (file_io, search, static_analysis, lsp, utility)
├── models/          # Pydantic 데이터 스키마 (ExecutionPlan, AnalysisResult 등)
├── config/          # settings.yaml, prompts/*.txt
├── resources/       # 정적 분석 바이너리, lint 설정
├── output/          # 분석 결과 출력
└── main.py          # 프로그램 진입점
```

## 코딩 컨벤션

### Python
- Python 3.11+ 문법 사용
- 타입 힌트 필수 (모든 함수 시그니처)
- Pydantic v2 모델로 데이터 스키마 정의
- f-string 사용 (format() 금지)
- 클래스명: PascalCase, 함수/변수명: snake_case
- 상수: UPPER_SNAKE_CASE
- 들여쓰기: 4 spaces

### 파일 구성
- 1 Agent = 1 파일 (agents/ 디렉토리)
- 1 Tool = 1 파일 (tools/ 하위 디렉토리)
- 1 Schema = 1 파일 (models/ 디렉토리)
- import 순서: stdlib → third-party → local

### 에러 처리
- Agent 내부 에러: try-except 후 AnalysisResult에 error 필드로 반환
- Tool 실행 실패: ToolExecutionError 예외 raise
- LLM API 실패: 최대 3회 재시도 후 fallback 모델 사용

### 금지 패턴
- print() 사용 금지 → rich 또는 logging 사용
- global 변수 사용 금지
- *args, **kwargs 남용 금지 (명시적 파라미터 선호)
- Agent가 직접 코드를 수정하는 기능 금지 (제안만)
- 프로젝트 전체 디렉토리 탐색 금지 (선택된 파일만)

## 문서 참조

- PRD: `docs/PRD.md`
- Agent 상세 설계: `docs/TECH_SPEC.md`
- 데이터 스키마: `docs/DATA_SCHEMA.md`
- CLI 명세: `docs/CLI_SPEC.md`
- AI 자동화 시스템: `docs/AI_SYSTEM_DESIGN.md`

## 매뉴얼 시스템

> **규칙**: 매뉴얼은 반드시 `docs/manuals/index.md` (목차)를 먼저 읽고, 필요한 챕터로 이동한다.
> 한 번에 2개 이상 챕터를 읽지 않는다.

### 매뉴얼 자동 로딩 규칙

| 작업 대상 경로/패턴 | 로드할 매뉴얼 |
|---------------------|---------------|
| `/agents/` 하위 파일 | `docs/manuals/agents.md` |
| `/tools/` 하위 파일 | `docs/manuals/tools.md` |
| `/models/` 하위 파일 | `docs/manuals/models.md` |
| `/tests/` 또는 `test_` 파일 | `docs/manuals/testing.md` |
| `/config/prompts/` 하위 파일 | `docs/manuals/agents.md` |

### 코드 패턴 감지 규칙

| 코드 패턴 | 로드할 매뉴얼 |
|-----------|---------------|
| `malloc`, `strcpy`, `free`, `buffer` | `docs/manuals/security.md` |
| `innerHTML`, `eval(`, `document.write` | `docs/manuals/security.md` |
| `EXEC SQL`, `WHENEVER` | `docs/manuals/agents.md` |
| `BaseModel`, `Field(`, `model_validate` | `docs/manuals/models.md` |
| `openai`, `ChatCompletion` | `docs/manuals/agents.md` |
| `pytest`, `@pytest.fixture` | `docs/manuals/testing.md` |

## 작업 기억 시스템 (Work Memory)

- **계획서**: `docs/worklog/plan.md` — Task 분해, 의존성, 대상 파일
- **맥락 노트**: `docs/worklog/context.md` — 설계 결정, 주의사항, 변경 이력
- **체크리스트**: `docs/worklog/checklist.md` — Task/Subtask 진행 상태 추적

### context.md 변경 이력 기록 규칙

**Task 완료(`/done`) 시 반드시 `docs/worklog/context.md`의 변경 이력 테이블을 업데이트한다.**

- 해당 Task에서 발생한 설계 변경, 버그 수정, 리뷰 반영 사항을 모두 기록
- 형식: `| 날짜 | 내용 | 이유 |`
- "내용"은 **무엇을** 변경했는지, "이유"는 **왜** 그렇게 판단했는지 작성
- 작업 중간에도 설계 변경이 발생하면 즉시 기록

## 품질 자동 관리

- **변경 이력**: `docs/quality/changelog.md` — PostToolUse hook이 자동 기록
- **리뷰 체크리스트**: `docs/quality/review-checklist.md` — Task 완료 전 셀프 체크
- **완료 보고 형식**: 1) 발견한 것 2) 수정한 것 3) 판단 근거

## Skills (슬래시 명령)

| 명령 | 설명 |
|------|------|
| `/plan` | 요청 분석 → Task 분해 → 계획서/체크리스트 생성 |
| `/task` | 다음 미완료 Task 시작 (매뉴얼 로드 → 브랜치 생성 → 코딩) |
| `/done` | Task 완료 (셀프체크 → 커밋 → 테스트 → 리뷰 → 체크리스트 업데이트 → Push → PR) |
| `/review` | 코드 리뷰 에이전트 호출 (버그/보안/성능/컨벤션/스키마 검사) |
| `/status` | 현재 진행률, Task 상태, 변경 파일 확인 |

---

## Git 워크플로우

### 브랜치 전략
- `main`: 안정 브랜치 (직접 push 금지)
- `develop`: 개발 통합 브랜치
- `feat/<task-name>`: 기능 개발 브랜치
- `fix/<issue-name>`: 버그 수정 브랜치
- `docs/<doc-name>`: 문서 작업 브랜치

### 커밋 메시지 규칙
```
<type>: <description>

<body (optional)>

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
```

**type 종류:**
- `feat`: 새 기능
- `fix`: 버그 수정
- `docs`: 문서 추가/수정
- `refactor`: 리팩토링
- `test`: 테스트 추가/수정
- `chore`: 빌드, 설정 변경

**예시:**
```
feat: OrchestratorAgent 세션 저장/복구 구현

Phase 완료 시 자동 체크포인트 저장 및 --resume 옵션 지원

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
```

### Subtask 커밋 규칙

**Subtask 하나를 완료할 때마다 즉시 커밋한다.**
- 커밋 메시지: `feat: T{번호}.{서브번호} {subtask 설명}`
- 예시: `feat: T3.1 BaseAgent ABC 구현`
- 여러 Subtask를 모아서 한 번에 커밋하지 않는다

### Task 완료 시 규칙

**모든 task가 완료되면 반드시 아래 절차를 수행한다:**

1. **커밋**: 남은 변경사항이 있으면 커밋 메시지 규칙에 맞게 커밋 (현재 브랜치에서)
2. **Push**: `git push`로 원격에 반영
3. **PR 생성**: `gh pr create`로 PR 생성 (base: main)

### PR 생성 규칙

1. `gh pr create`로 PR 생성
   - base 브랜치: `main`
   - PR 제목: 커밋 메시지의 type + description
   - PR 본문: Summary + 변경 파일 목록
2. PR URL을 사용자에게 공유

**PR 본문 템플릿:**
```markdown
## Summary
- <변경 사항 1-3줄 요약>

## Changed Files
- `path/to/file1`
- `path/to/file2`

## Test Plan
- [ ] 테스트 항목

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

### 주의사항
- `main` 브랜치에 직접 push 금지
- force push 금지
- 커밋 전 민감 정보 (.env, API 키) 포함 여부 확인
- .gitignore에 output/, __pycache__/, .env 포함 필수
