# AI 개발 자동화 시스템 설계서

> Mider 프로젝트의 Claude Code 기반 AI 개발 자동화 시스템 전체 아키텍처와 설계 근거를 기술한다.

---

## 1. 시스템 개요

### 1.1 해결하려는 문제

AI 코딩 어시스턴트(Claude Code)를 활용한 개발에서 다음과 같은 문제가 반복적으로 발생한다:

| 문제 | 증상 | 영향 |
|------|------|------|
| **맥락 소실** | 세션이 바뀌면 이전 작업 내용을 잊음 | 같은 실수를 반복, 설계 결정 이유 불명 |
| **컨벤션 미준수** | 프로젝트 규칙을 매번 알려줘야 함 | print() 사용, 타입 힌트 누락, 스키마 불일치 |
| **품질 편차** | 리뷰 없이 코드가 커밋됨 | 버그, 보안 취약점이 그대로 반영 |
| **작업 추적 불가** | 무엇을 수정했고 왜 그랬는지 기록 없음 | 디버깅 시 변경 이력 추적 불가능 |
| **수동 반복 작업** | 매 Task마다 브랜치 생성, 커밋, 테스트, PR을 수동 지시 | 사용자 피로도 증가 |

### 1.2 설계 철학

```
"AI가 스스로 규칙을 찾아 읽고, 스스로 품질을 점검하고, 스스로 기록을 남기게 한다."
```

**핵심 원칙:**

1. **자동화 우선**: 사람이 지시하지 않아도 hook이 자동 실행
2. **문서 기반**: 모든 규칙은 파일로 존재 (하드코딩 X)
3. **추적 가능**: 모든 변경은 자동으로 기록
4. **최소 명령**: 사용자는 `/task` → `/done` 두 명령만으로 개발 사이클 완료

### 1.3 시스템 구성

```
┌─────────────────────────────────────────────────────────┐
│                    AI 개발 자동화 시스템                    │
├────────────┬──────────────┬──────────────┬──────────────┤
│ 1. 자동     │ 2. 작업      │ 3. 품질      │ 4. 전문가    │
│    매뉴얼   │    기억      │    자동 관리  │    에이전트  │
│    시스템   │    시스템     │    시스템     │    협업      │
├────────────┼──────────────┼──────────────┼──────────────┤
│ PreToolUse │ plan.md      │ PostToolUse  │ /plan        │
│ Hook       │ context.md   │ Hook         │ /task        │
│ (자동)     │ checklist.md │ Stop Hook    │ /done        │
│            │              │ (자동)       │ /review      │
│            │              │              │ /status      │
└────────────┴──────────────┴──────────────┴──────────────┘
        ↑ 자동 실행                  ↑ 자동 실행     ↑ 사용자 실행
```

---

## 2. 시스템 1: 자동 매뉴얼 시스템

### 2.1 왜 필요한가?

**문제**: AI는 프로젝트 전체 규칙을 한꺼번에 기억하지 못한다. 컨텍스트 윈도우에 모든 매뉴얼을 넣으면 토큰이 낭비되고, 안 넣으면 규칙을 모른 채 코딩한다.

**해결**: 수정하려는 파일의 경로와 내용을 분석하여, 지금 필요한 매뉴얼만 자동으로 추천한다.

### 2.2 동작 원리

```
[AI가 Edit/Write 호출]
        │
        ▼
  PreToolUse Hook 발동
  (auto_manual.py)
        │
        ├── 1. 파일 경로 분석 ──→ "/agents/" → agents.md
        │                         "/models/" → models.md
        │
        └── 2. 코드 패턴 분석 ──→ "malloc" → security.md
                                   "BaseModel" → models.md
        │
        ▼
  "index.md 먼저 읽고 → 해당 챕터로 이동하세요" 안내
```

### 2.3 감지 규칙

#### 2.3.1 경로 기반 규칙

| 파일 경로 패턴 | 추천 매뉴얼 | 이유 |
|---------------|------------|------|
| `/agents/*.py` | agents.md | Agent 구현 시 BaseAgent 패턴, LLM 호출 규칙 필요 |
| `/tools/*.py` | tools.md | Tool 구현 시 BaseTool 인터페이스, ToolExecutionError 패턴 필요 |
| `/models/*.py` | models.md | 스키마 수정 시 Pydantic v2 규칙, 키 매칭 규칙 필요 |
| `/tests/*.py` 또는 `test_*` | testing.md | 테스트 작성 시 LLM mock 패턴, fixture 규칙 필요 |
| `/config/prompts/*.txt` | agents.md | 프롬프트 수정 시 Agent 워크플로우 이해 필요 |

#### 2.3.2 코드 패턴 감지 규칙

| 코드 패턴 | 추천 매뉴얼 | 이유 |
|-----------|------------|------|
| `malloc`, `strcpy`, `free`, `buffer`, `sprintf` | security.md | C 메모리 안전성 규칙 확인 필요 (오버플로우 방지) |
| `innerHTML`, `eval(`, `document.write` | security.md | XSS/Injection 방지 규칙 확인 필요 |
| `EXEC SQL`, `WHENEVER`, `sqlca`, `INDICATOR` | agents.md | Pro*C 분석 Agent의 SQLCA/INDICATOR 체크 로직 확인 필요 |
| `BaseModel`, `Field(`, `model_validate` | models.md | Pydantic v2 문법, 스키마 일치 규칙 확인 필요 |
| `openai`, `ChatCompletion` | agents.md | LLM 호출 패턴 (재시도, fallback, JSON Mode) 확인 필요 |
| `pytest`, `@pytest.fixture`, `def test_` | testing.md | 테스트 컨벤션, mock 패턴 확인 필요 |

### 2.4 매뉴얼 구조

```
docs/manuals/
├── index.md        ← 반드시 먼저 읽는 목차 (게이트웨이)
├── agents.md       ← Ch.1: BaseAgent, LLM 호출, Phase별 역할
├── tools.md        ← Ch.2: BaseTool, 정적 분석 도구, 에러 처리
├── models.md       ← Ch.3: Pydantic v2, 스키마 요약, 키 매칭
├── testing.md      ← Ch.4: pytest 구조, LLM mock, Tool 테스트
└── security.md     ← Ch.5: C/JS/ProC 보안 체크리스트
```

**왜 index.md를 먼저 읽게 하는가?**

- AI가 불필요한 매뉴얼을 읽는 것을 방지 (컨텍스트 윈도우 절약)
- 목차에서 필요한 챕터만 선택적으로 로드
- 한 번에 2개 이상 챕터를 읽지 않는 규칙으로 토큰 효율 극대화

### 2.5 구현 파일

| 파일 | 역할 |
|------|------|
| `.claude/settings.json` | Hook 이벤트 바인딩 (PreToolUse → auto_manual.py) |
| `.claude/scripts/auto_manual.py` | 경로/패턴 분석 및 매뉴얼 추천 로직 |
| `docs/manuals/*.md` | 실제 매뉴얼 콘텐츠 (5개 챕터) |

### 2.6 이점

| 이점 | 설명 |
|------|------|
| **규칙 자동 학습** | AI가 코딩 전에 관련 규칙을 읽으므로 컨벤션 위반이 줄어듦 |
| **선택적 로딩** | 전체 매뉴얼 대신 필요한 챕터만 로드하여 토큰 절약 |
| **무의식적 실행** | 사용자가 지시하지 않아도 hook이 자동으로 안내 |
| **확장 용이** | PATH_RULES, PATTERN_RULES에 항목만 추가하면 새 규칙 반영 |

---

## 3. 시스템 2: 작업 기억 시스템 (Work Memory)

### 3.1 왜 필요한가?

**문제**: AI는 세션이 바뀌면 이전 대화를 잊는다. "왜 이 방식으로 구현했지?", "다음에 뭘 해야 하지?", "어디까지 했지?" 같은 질문에 답할 수 없다.

**해결**: 계획, 맥락, 진행 상태를 파일로 저장하여 세션 간 기억을 유지한다.

### 3.2 3개 문서 구조

```
docs/worklog/
├── plan.md         ← "무엇을" 할지 (Task 분해, 의존성, 대상 파일)
├── context.md      ← "왜" 그렇게 할지 (설계 결정, 주의사항, 변경 이력)
└── checklist.md    ← "어디까지" 했는지 (Task/Subtask 체크박스)
```

### 3.3 각 문서의 역할과 필요성

#### 3.3.1 plan.md (계획서)

**왜 필요한가**: Task 간 의존성과 대상 파일을 명시하지 않으면, AI가 순서를 잘못 잡거나 엉뚱한 파일을 수정할 수 있다.

```markdown
# 작업 계획서

## Task 목록
### T1: Project Scaffold
- T1.1: 디렉토리 구조 생성 → 대상: mider/
- T1.2: requirements.txt → 대상: requirements.txt

### T2: Data Models (depends: T1)
- T2.1: execution_plan.py → 대상: mider/models/execution_plan.py
```

**핵심 가치:**
- Task별 "대상 파일"을 명시하여 AI가 정확한 파일을 수정하도록 유도
- `depends` 표기로 의존성 순서를 강제 → 빌드 실패 방지
- `/plan` 명령으로 자동 생성되므로 사용자가 직접 작성할 필요 없음

#### 3.3.2 context.md (맥락 노트)

**왜 필요한가**: "이 코드를 왜 이렇게 짰지?"라는 질문에 답할 수 없으면 나중에 리팩토링이나 디버깅이 불가능하다. 특히 AI가 세션마다 동일한 판단을 내리는 보장이 없으므로, 이전 결정을 기록해야 한다.

```markdown
# 맥락 노트

## 설계 결정
- Bottom-up 구현 순서: 기반 → 스키마 → 인프라 → Tool → Agent → CLI
  → 이유: 상위 레이어가 하위 레이어에 의존하므로 역순 구현은 mock이 과다해짐
- LSP Tool은 선택적 기능
  → 이유: 1차 PoC에서 LSP 서버를 폐쇄망에 배포하기 어려움

## 주의사항
- print() 금지 → rich/logging 사용
- Agent는 코드 수정 불가 (제안만)

## 변경 이력
| 날짜 | 내용 | 이유 |
|------|------|------|
| 2026-02-24 | Session Resume 2차 PoC로 분리 | 핵심 기능 먼저 구현 |
```

**핵심 가치:**
- **설계 결정**: AI가 다른 방식을 제안하려 할 때, 이전에 왜 이 방식을 선택했는지 확인 가능
- **주의사항**: 매 세션마다 반복 지시하지 않아도 됨
- **변경 이력**: 설계가 바뀐 시점과 이유를 추적 가능

#### 3.3.3 checklist.md (체크리스트)

**왜 필요한가**: 15개 Task, 70+ Subtask를 머리로 추적할 수 없다. AI도 마찬가지로 "어디까지 했는지"를 파일로 관리해야 정확한 다음 작업을 선택할 수 있다.

```markdown
# 체크리스트

- [x] T1: Project Scaffold
  - [x] T1.1: 디렉토리 구조 생성
  - [x] T1.2: requirements.txt
  - [ ] T1.3: settings.yaml        ← 여기까지 완료, 다음은 이것
- [ ] T2: Data Models
  - [ ] T2.1: execution_plan.py
```

**핵심 가치:**
- `/task` 명령이 이 파일을 읽고 다음 미완료 Task를 자동 선택
- `/status` 명령이 이 파일로 진행률 계산 (예: 3/15 = 20%)
- `/done` 명령이 완료 시 자동으로 [x] 체크 → 수동 관리 불필요
- Subtask 단위 추적으로 Task 도중 세션이 끊겨도 정확히 이어서 작업 가능

### 3.4 이점

| 이점 | 설명 |
|------|------|
| **세션 간 연속성** | 새 대화에서 plan/context/checklist를 읽으면 이전 맥락 즉시 복구 |
| **판단 일관성** | context.md의 설계 결정이 AI의 판단 기준으로 작동 |
| **진행률 가시화** | checklist.md로 정확한 완료율 확인 가능 |
| **자동 생성/업데이트** | /plan이 생성, /done이 업데이트 → 수동 작업 제로 |

---

## 4. 시스템 3: 품질 자동 관리 시스템

### 4.1 왜 필요한가?

**문제**: AI가 빠르게 코드를 생성하지만, 품질 검증 없이 커밋되면 기술 부채가 빠르게 쌓인다. 사람이 매번 "타입 힌트 빠졌어", "print() 쓰지 마" 같은 피드백을 반복해야 한다.

**해결**: 3개의 자동 메커니즘으로 품질을 보장한다.

### 4.2 구성 요소

```
품질 자동 관리
├── 4.2.1 변경 이력 자동 기록  (PostToolUse Hook)     ← 모든 수정 추적
├── 4.2.2 셀프 체크 리마인더   (Stop Hook)              ← 작업 종료 시 점검
└── 4.2.3 리뷰 체크리스트      (docs/quality/)         ← Task 완료 전 셀프 리뷰
```

### 4.2.1 변경 이력 자동 기록 (PostToolUse Hook)

**왜 필요한가**: "이 파일을 언제, 어떤 브랜치에서 수정했지?"를 알 수 없으면 디버깅이 불가능하다. Git log는 커밋 단위이지만, changelog는 파일 수정 단위로 기록하여 더 세밀한 추적이 가능하다.

**동작:**

```
[AI가 Edit/Write 실행 완료]
        │
        ▼
  PostToolUse Hook 발동
  (log_change.py)
        │
        ├── 시스템 파일인가? (changelog, worklog, .claude/) → 건너뜀
        │
        └── 아니면 → changelog.md에 기록
            - `2026-02-24 15:30:00` [feat/T1-scaffold] **Edit**: `mider/main.py`
```

**출력 예시** (`docs/quality/changelog.md`):

```markdown
# 변경 이력

- `2026-02-24 14:00:01` [feat/T1-scaffold] **Write**: `mider/agents/__init__.py`
- `2026-02-24 14:00:03` [feat/T1-scaffold] **Write**: `mider/tools/__init__.py`
- `2026-02-24 14:05:22` [feat/T2-data-models] **Write**: `mider/models/execution_plan.py`
- `2026-02-24 14:12:45` [feat/T2-data-models] **Edit**: `mider/models/execution_plan.py`
```

**핵심 가치:**
- **커밋보다 세밀한 추적**: Git은 커밋 단위, changelog는 파일 수정 단위
- **브랜치 컨텍스트 포함**: 어떤 Task에서 수정했는지 즉시 확인
- **자동 실행**: 사용자가 기록을 지시할 필요 없음
- **시스템 파일 제외**: changelog 자체나 worklog 수정은 기록하지 않아 노이즈 방지

### 4.2.2 셀프 체크 리마인더 (Stop Hook)

**왜 필요한가**: AI가 작업을 마칠 때 "나 다 했어"라고 할 수 있지만, 타입 힌트 누락, print() 사용, 체크리스트 미업데이트 같은 실수가 남아 있을 수 있다. 마지막 순간에 한 번 더 점검하게 한다.

**동작:**

```
[AI가 응답 생성 완료 (Stop 이벤트)]
        │
        ▼
  Stop Hook 발동
  (self_check.sh)
        │
        ▼
  "[셀프 체크 리마인더] 작업 종료 전 아래 항목을 확인하세요:
   - 타입 힌트가 모든 함수에 있는가?
   - 에러 처리가 누락되지 않았는가?
   - print() 대신 logging/rich를 사용했는가?
   - 하드코딩된 값이 없는가?
   - checklist.md를 업데이트했는가?"
```

**핵심 가치:**
- **최후의 안전망**: 코드 리뷰 전 기본적인 실수를 미리 잡음
- **반복 알림 자동화**: 사용자가 "타입 힌트 확인했어?"를 매번 물어볼 필요 없음
- **Mider 프로젝트 맞춤**: 일반적인 체크가 아닌, 이 프로젝트의 금지 패턴(print, 하드코딩) 포함

### 4.2.3 리뷰 체크리스트

**왜 필요한가**: `/done` 프로세스의 Step 1(셀프 체크)에서 사용되는 상세 점검 목록이다. 셀프 체크 리마인더가 5줄짜리 요약이라면, 이것은 30항목의 정밀 체크리스트이다.

**구성** (`docs/quality/review-checklist.md`):

| 카테고리 | 점검 항목 수 | 예시 |
|---------|------------|------|
| 코드 품질 | 5개 | 타입 힌트, print() 금지, 에러 처리, import 순서 |
| 스키마 일치 | 3개 | DATA_SCHEMA.md 일치, Pydantic v2 문법, 필수 필드 |
| Agent 규칙 | 3개 | 코드 수정 불가(제안만), LLM 재시도, JSON Mode |
| 보안 | 3개 | API 키 하드코딩 금지, 입력 검증, 로그 민감정보 |
| 테스트 | 3개 | 테스트 존재, LLM mock, 경계값 테스트 |
| 문서 | 2개 | checklist.md 업데이트, context.md 변경 기록 |

**핵심 가치:**
- **프로젝트 맞춤 규칙**: 일반적인 린팅이 아닌, Mider 프로젝트 특유의 규칙(Agent 제안만, DATA_SCHEMA 일치 등)
- **카테고리 분류**: 코드/스키마/Agent/보안/테스트/문서 6개 관점으로 체계적 점검
- **`/done` 자동 실행**: Step 1에서 AI가 이 파일을 읽고 모든 항목을 점검

### 4.3 이점

| 이점 | 설명 |
|------|------|
| **변경 추적** | changelog.md로 모든 파일 수정 이력을 시간순 확인 |
| **실수 방지** | Stop Hook이 매 작업 종료 시 기본 규칙 리마인드 |
| **체계적 점검** | 6개 카테고리 30항목으로 품질을 정량적으로 점검 |
| **완전 자동** | Hook은 사용자 지시 없이 자동 실행 |

---

## 5. 시스템 4: 전문가 에이전트 협업 (Skills)

### 5.1 왜 필요한가?

**문제**: AI에게 "Task 시작해", "커밋하고 테스트 돌리고 PR 올려줘" 같은 복잡한 지시를 매번 자연어로 설명해야 한다. 빠뜨리는 단계가 생기고, 실행 순서가 일관되지 않는다.

**해결**: 자주 사용하는 워크플로우를 슬래시 명령(`/plan`, `/task`, `/done`, `/review`, `/status`)으로 캡슐화하여 한 단어로 실행한다.

### 5.2 전체 워크플로우

```
┌──────────┐     ┌──────────┐     ┌──────────────────────────┐
│  /plan   │ ──→ │  /task   │ ──→ │  코딩 (AI 자동 진행)      │
│ 계획 수립 │     │ Task시작  │     │  - 매뉴얼 hook 자동 안내   │
│ 승인 대기 │     │ 브랜치생성 │     │  - changelog hook 자동 기록│
└──────────┘     └──────────┘     └────────────┬─────────────┘
                                                │
                                                ▼
┌──────────┐     ┌──────────┐     ┌──────────────────────────┐
│ /status  │     │ /review  │ ←── │  /done                   │
│ 진행 확인 │     │ 중간 리뷰 │     │  셀프체크→커밋→테스트     │
│ (선택)   │     │ (선택)   │     │  →리뷰→체크리스트→Push→PR │
└──────────┘     └──────────┘     └──────────────────────────┘
```

### 5.3 각 Skill 상세

#### 5.3.1 `/plan` — 요구사항 분석 및 계획 수립

**왜 필요한가**: 코딩을 시작하기 전에 요구사항을 분석하고 Task를 분해하지 않으면, AI가 무계획으로 코딩하여 나중에 대규모 수정이 필요해진다.

**실행 절차:**

| Step | 동작 | 산출물 |
|------|------|--------|
| 1 | 사용자 요청 분석 + 관련 문서 읽기 (TECH_SPEC, DATA_SCHEMA 등) | 요구사항 이해 |
| 2 | Task 분해 (T1, T1.1, T1.2...) + 의존성 파악 | Task 트리 |
| 3 | 3개 문서 생성 (plan.md, context.md, checklist.md) | 작업 기억 |
| 4 | TaskCreate로 Task 등록 | Task 트래커 |
| 5 | 사용자 승인 대기 | "승인 후 /task로 시작하세요" |

**핵심 가치:**
- **코드 작성 없이 분석만**: 계획 단계에서 코드를 작성하지 않으므로 잘못된 방향으로 개발 시작하는 것을 방지
- **사용자 승인 게이트**: 사용자가 계획을 검토하고 수정할 수 있는 기회 제공
- **3개 문서 자동 생성**: plan/context/checklist를 수동으로 작성할 필요 없음

#### 5.3.2 `/task` — Task 시작

**왜 필요한가**: Task를 시작할 때 "체크리스트 확인 → 매뉴얼 로드 → 브랜치 생성 → 코딩 시작"의 4단계를 매번 수동으로 지시하는 것은 비효율적이다.

**실행 절차:**

| Step | 동작 | 왜 필요한가 |
|------|------|------------|
| 1 | checklist.md에서 다음 미완료 Task 확인 | 작업 순서를 자동으로 결정 |
| 2 | plan.md에서 Task 상세 내용 확인 | 대상 파일과 Subtask 파악 |
| 3 | context.md에서 맥락 확인 | 이전 설계 결정과 주의사항 파악 |
| 4 | index.md → 해당 매뉴얼 읽기 | Task에 맞는 코딩 규칙 학습 |
| 5 | `git checkout -b feat/T{N}-{slug}` | 독립적인 브랜치에서 작업 |
| 6 | TaskUpdate → in_progress | Task 상태 추적 |
| 7 | 코딩 시작 | Subtask 단위로 진행 |

**핵심 가치:**
- **한 명령으로 4단계 자동화**: 체크리스트 확인 + 매뉴얼 로드 + 브랜치 생성 + 상태 변경
- **작업 중 규칙**: Subtask 완료마다 checklist.md 자동 업데이트, 설계 변경 시 context.md 기록
- **일관된 브랜치 네이밍**: `feat/T{번호}-{slug}` 형식으로 자동 생성

#### 5.3.3 `/done` — Task 완료

**왜 필요한가**: Task 완료 시 "셀프 체크 → 커밋 → 테스트 → 리뷰 → 체크리스트 → Push → PR → 보고"의 8단계를 빠짐없이 수행해야 한다. 하나라도 빠지면 품질 저하 또는 추적 불가.

**실행 절차 (8 Steps):**

```
Step 1: 셀프 체크
  └→ review-checklist.md의 30항목 점검
       왜: 기본적인 품질 문제를 커밋 전에 차단

Step 2: 커밋
  └→ git add + git commit (커밋 메시지 규칙 준수)
       왜: 일관된 커밋 이력 유지

Step 3: 테스트
  └→ pytest tests/ -v
       왜: 코드 변경이 기존 기능을 깨뜨리지 않았는지 확인

Step 4: 자동 수정 (테스트 실패 시)
  └→ 에러 분석 → 수정 → 재커밋 → 재테스트 (최대 3회)
       왜: 사용자 개입 없이 자동으로 테스트 통과 시도

Step 5: 코드 리뷰 (필수)
  └→ 리뷰 에이전트가 버그/보안/성능/컨벤션/스키마 5개 관점 검사
  └→ Critical/High 발견 시 → 수정 후 Step 3부터 재수행
       왜: 사람 리뷰 없이도 체계적인 품질 게이트 확보

Step 6: 체크리스트 업데이트
  └→ checklist.md [x] 표기 + TaskUpdate completed
       왜: 진행률 정확 추적, 다음 /task가 올바른 Task 선택

Step 7: Push & PR
  └→ git push + gh pr create
       왜: 코드를 원격에 안전하게 백업하고 리뷰 이력 생성

Step 8: 완료 보고
  └→ 3가지 필수 포함: 발견한 것 / 수정한 것 / 판단 근거
       왜: "무엇을 왜 수정했는지" 기록 → 디버깅/회고 시 핵심 자료
```

**핵심 가치:**
- **8단계 완전 자동화**: `/done` 한 번으로 커밋부터 PR까지 모든 절차 수행
- **자동 수정 루프**: 테스트 실패 시 3회까지 자동 시도 → 사용자 개입 최소화
- **필수 리뷰 게이트**: Critical/High 이슈가 있으면 PR 전에 반드시 수정
- **3부 보고서**: 발견/수정/판단근거를 남겨 프로젝트 지식 축적

#### 5.3.4 `/review` — 코드 리뷰

**왜 필요한가**: `/done`에 리뷰가 포함되어 있지만, 코딩 중간에도 리뷰를 받고 싶을 때가 있다. 방향이 맞는지 중간 점검할 수 있다.

**리뷰 관점 (5가지):**

| 관점 | 검사 항목 | 왜 필요한가 |
|------|----------|------------|
| 버그 | NULL 체크, 에러 처리, 경계값, 타입 불일치 | 런타임 크래시 방지 |
| 보안 | API 키 노출, injection, 하드코딩된 비밀 정보 | 보안 사고 방지 |
| 성능 | 불필요한 반복, 대용량 파일 처리, 메모리 사용 | 성능 저하 방지 |
| 컨벤션 | CLAUDE.md의 코딩 규칙 준수 여부 | 코드 일관성 유지 |
| 스키마 | DATA_SCHEMA.md와 일치하는지 | Agent 간 데이터 계약 보장 |

**출력 형식:**

```
### 발견 사항
1. [CRITICAL]
   - 파일: mider/agents/c_analyzer.py:45
   - 문제: LLM 응답을 JSON 파싱할 때 예외 처리 없음
   - 제안: try-except로 JSONDecodeError 처리 추가
   - 근거: 잘못된 LLM 응답 시 전체 파이프라인이 중단됨

### 총평
- 전체 코드 품질 평가: 4/5
- Critical 이슈 1건 수정 권고
```

#### 5.3.5 `/status` — 상태 확인

**왜 필요한가**: "지금 몇 퍼센트 완료됐지?", "현재 어떤 Task를 하고 있지?", "커밋 안 한 파일이 있나?"를 한눈에 확인할 수 있어야 한다.

**출력 형식:**

```
## 작업 현황

진행률: 3 / 15
현재 Task: T4 - File I/O & Search Tools (in_progress)
브랜치: feat/T4-file-io-search
변경 파일: 5개 (미커밋)

### 체크리스트
- [x] T1: Project Scaffold
- [x] T2: Data Models
- [x] T3: Base Infrastructure
- [ ] T4: File I/O & Search Tools  ← 현재
  - [x] T4.1: file_reader.py
  - [ ] T4.2: grep.py
  ...

### 최근 수정 (최근 5건)
- 2026-02-24 15:30 [feat/T4] Edit: mider/tools/file_io/file_reader.py
- 2026-02-24 15:28 [feat/T4] Write: mider/tools/file_io/__init__.py
...
```

### 5.4 이점

| 이점 | 설명 |
|------|------|
| **최소 명령** | `/task` → 코딩 → `/done` 두 명령으로 전체 사이클 완료 |
| **8단계 자동화** | /done이 셀프체크~PR까지 모든 절차를 빠짐없이 수행 |
| **품질 게이트** | 테스트 + 리뷰를 통과해야만 PR 생성 → 결함 유입 차단 |
| **지식 축적** | 완료 보고의 "발견/수정/판단근거"가 프로젝트 지식으로 축적 |
| **상태 가시화** | `/status`로 진행률, 변경사항, 최근 이력 즉시 확인 |

---

## 6. Hook 시스템 기술 상세

### 6.1 Hook 이벤트 매핑

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "command": "python3 .claude/scripts/auto_manual.py"
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "command": "python3 .claude/scripts/log_change.py"
      }
    ],
    "Stop": [
      {
        "command": "bash .claude/scripts/self_check.sh"
      }
    ]
  }
}
```

### 6.2 Hook 입출력 스펙

| Hook | 트리거 | stdin 입력 | 출력 방식 |
|------|--------|-----------|----------|
| PreToolUse (auto_manual.py) | Edit 또는 Write 호출 직전 | `{"tool_name": "Edit", "tool_input": {"file_path": "..."}}` | stdout으로 안내 메시지 |
| PostToolUse (log_change.py) | Edit 또는 Write 실행 완료 후 | `{"tool_name": "Edit", "tool_input": {"file_path": "..."}}` | changelog.md에 append |
| Stop (self_check.sh) | AI 응답 생성 완료 시 | 없음 | stdout으로 리마인더 |

### 6.3 Hook 안전장치

| 안전장치 | 구현 | 이유 |
|---------|------|------|
| 시스템 파일 제외 | log_change.py에서 `.claude/`, `docs/worklog/`, `changelog.md` 건너뜀 | 무한 루프 방지 (changelog 수정 → hook 발동 → changelog 수정...) |
| 파일 크기 제한 | auto_manual.py에서 `f.read(10000)` (10KB만) | 대용량 파일 분석으로 인한 지연 방지 |
| 에러 무시 | try-except로 모든 예외 처리 | hook 실패가 본 작업을 막으면 안 됨 |
| 인코딩 대응 | `encoding="utf-8", errors="ignore"` | 비표준 인코딩 파일에서 hook 에러 방지 |

---

## 7. 파일 구조 총정리

```
.claude/
├── settings.json              # Hook 이벤트 바인딩
├── scripts/
│   ├── auto_manual.py         # PreToolUse: 매뉴얼 자동 추천
│   ├── log_change.py          # PostToolUse: 변경 이력 자동 기록
│   └── self_check.sh          # Stop: 셀프 체크 리마인더
└── commands/
    ├── plan.md                # /plan: 요구사항 분석 → 계획 수립
    ├── task.md                # /task: Task 시작 → 브랜치 생성 → 코딩
    ├── done.md                # /done: 셀프체크 → 커밋 → 테스트 → 리뷰 → Push → PR
    ├── review.md              # /review: 코드 리뷰 에이전트 호출
    └── status.md              # /status: 진행률 + 상태 확인

docs/
├── manuals/
│   ├── index.md               # 매뉴얼 목차 (게이트웨이)
│   ├── agents.md              # Ch.1: Agent 구현 규칙
│   ├── tools.md               # Ch.2: Tool 구현 규칙
│   ├── models.md              # Ch.3: 데이터 스키마 규칙
│   ├── testing.md             # Ch.4: 테스트 규칙
│   └── security.md            # Ch.5: 보안 체크리스트
├── worklog/
│   ├── plan.md                # 작업 계획서
│   ├── context.md             # 맥락 노트
│   └── checklist.md           # 체크리스트
└── quality/
    ├── changelog.md           # 변경 이력 (자동 생성)
    └── review-checklist.md    # 셀프 리뷰 체크리스트

CLAUDE.md                      # 프로젝트 지침 (시스템 참조 포함)
```

---

## 8. 사용자 워크플로우 요약

```
사용자                          AI (Claude Code)
──────                          ──────────────
/plan                     →     문서 분석 → Task 분해 → 계획서 생성
"승인"                    →     (대기)

/task                     →     checklist 확인 → 매뉴얼 로드
                                → 브랜치 생성 → 코딩 시작
                                (hook: 매뉴얼 자동 추천)
                                (hook: 변경 이력 자동 기록)

/done                     →     셀프 체크 (30항목)
                                → 커밋
                                → 테스트 (실패 시 자동 수정 ×3)
                                → 코드 리뷰 (Critical/High → 수정)
                                → 체크리스트 업데이트
                                → Push → PR 생성
                                → 완료 보고 (발견/수정/판단근거)

/task                     →     다음 Task 자동 시작 (반복)
...

/status (선택)            →     진행률 + 변경파일 + 최근 이력
/review (선택)            →     중간 리뷰
```

**사용자가 실제로 입력하는 것**: `/plan` → 승인 → `/task` → `/done` → `/task` → `/done` → ...

**나머지는 전부 자동.**
