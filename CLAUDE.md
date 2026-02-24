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
├── sessions/        # 세션 저장
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

### Task 완료 시 규칙

**모든 task가 완료되면 반드시 아래 절차를 수행한다:**

1. **커밋**: 변경사항을 커밋 메시지 규칙에 맞게 커밋 (현재 브랜치에서)
2. **Push**: `git push`로 원격에 반영

**PR은 사용자가 명시적으로 요청할 때만 생성한다.**

### PR 생성 규칙 (사용자 요청 시)

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
- .gitignore에 output/, sessions/, __pycache__/, .env 포함 필수
