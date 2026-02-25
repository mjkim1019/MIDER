현재 Task를 완료합니다. 아래 절차를 **반드시 순서대로** 수행하세요.

## Step 1: 셀프 체크
1. docs/quality/review-checklist.md를 읽고 모든 항목을 점검한다
2. 점검 결과를 보고한다

## Step 2: 커밋
1. git status로 변경 파일 확인
2. 변경 파일을 git add (민감 정보 포함 파일 제외)
3. CLAUDE.md의 커밋 메시지 규칙에 맞게 커밋

## Step 3: 테스트 실행
```bash
pytest tests/ -v
```
- 모든 테스트 통과 → Step 5로 이동
- 실패 있음 → Step 4로 이동

## Step 4: 자동 수정 (테스트 실패 시)
1. 실패한 테스트의 에러 메시지를 분석한다
2. 원인을 파악하고 코드를 수정한다
3. 수정 내용을 커밋한다
4. Step 3으로 돌아가 테스트를 재실행한다
5. **최대 3회 반복**. 3회 실패 시 사용자에게 보고하고 중단한다

## Step 5: 코드 리뷰 (필수)
1. `git diff main...HEAD`로 전체 변경 내역을 수집한다
2. Task tool (subagent_type: general-purpose)로 리뷰 에이전트를 호출한다
   - 리뷰 관점: 버그, 보안, 성능, 컨벤션, 스키마 일치
   - 출력: 심각도(CRITICAL/HIGH/MEDIUM/LOW) + 파일:라인 + 문제 + 제안
3. **Critical/High 이슈가 있으면 반드시 수정 후 재커밋한다**
4. 수정 후 Step 3(테스트)부터 다시 수행한다
5. Critical/High 이슈가 없으면 다음 Step으로 진행한다

## Step 6: 체크리스트 업데이트
1. docs/worklog/checklist.md에서 완료된 Task/Subtask를 [x]로 변경한다
2. TaskUpdate로 Task를 completed로 변경한다

## Step 7: Push & PR
1. `git push -u origin {현재 브랜치}`
2. `gh pr create`로 PR 생성:
   - base: main
   - title: 커밋 메시지의 type + description
   - body: 아래 템플릿 사용

```
## Summary
- [변경 사항 1-3줄 요약]

## Changed Files
- [파일 목록]

## Test Results
- 통과: {N}건 / 실패: 0건

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

## Step 8: 완료 보고

**반드시 아래 3가지를 포함**하여 보고한다:

```
## T{번호} 완료 보고

### 1. 발견한 것 (What I Found)
- [작업 중 발견한 이슈, 패턴, 주의점]
- [기존 코드에서 발견한 문제점]

### 2. 수정한 것 (What I Changed)
- [파일명]: [변경 내용]
- [파일명]: [변경 내용]

### 3. 판단 근거 (Why I Decided)
- [이 방식을 선택한 이유]
- [대안이 있었다면 왜 이 방식이 나은지]

### 테스트 결과
- 통과: {N}건 / 실패: 0건

### PR
- {PR URL}

### 다음 Task
- T{다음번호}: {제목}
- "/task로 다음 task를 시작하세요"
```
