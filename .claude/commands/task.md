다음 Task를 시작합니다. 아래 절차를 순서대로 수행하세요.

## Step 1: 현재 상태 확인
1. docs/worklog/checklist.md를 읽고 다음 미완료 Task를 확인한다
2. docs/worklog/plan.md에서 해당 Task의 상세 내용을 확인한다
3. docs/worklog/context.md에서 관련 맥락을 확인한다

## Step 2: 매뉴얼 로드
1. docs/manuals/index.md (목차)를 읽는다
2. 해당 Task에 맞는 챕터 매뉴얼을 읽는다:
   - Agent 구현 → docs/manuals/agents.md
   - Tool 구현 → docs/manuals/tools.md
   - 스키마 → docs/manuals/models.md
   - 테스트 → docs/manuals/testing.md
   - 보안 관련 → docs/manuals/security.md

## Step 3: Git 브랜치 생성
```bash
git checkout main
git pull origin main
git checkout -b feat/T{번호}-{task-slug}
```
task-slug는 Task 제목을 영문 소문자+하이픈으로 변환 (예: orchestrator-agent)

## Step 4: Task 상태 변경
TaskUpdate로 해당 Task를 in_progress로 변경한다.

## Step 5: 작업 시작
"T{번호} 시작: {task 제목}" 메시지를 출력하고 코딩을 시작한다.

### 작업 중 규칙
- Subtask 단위로 작업한다
- 각 Subtask 완료 시 docs/worklog/checklist.md에서 해당 항목을 [x]로 변경한다
- 코딩 중 설계 변경이 필요하면 docs/worklog/context.md에 이유를 기록한다
- 작업이 끝나면 "/done으로 완료 처리하세요"라고 안내한다
