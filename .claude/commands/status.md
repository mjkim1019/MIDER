현재 작업 상태를 확인합니다.

## 확인 항목

1. docs/worklog/checklist.md를 읽어 전체 진행률을 파악한다
2. TaskList로 현재 Task 상태를 확인한다
3. git branch --show-current로 현재 브랜치를 확인한다
4. git status로 변경 파일을 확인한다
5. docs/quality/changelog.md에서 최근 수정 이력을 확인한다

## 출력 형식

```
## 작업 현황

진행률: [완료 task 수] / [전체 task 수]
현재 Task: T{번호} - {제목} ({상태})
브랜치: {현재 브랜치}
변경 파일: {N}개 (미커밋)

### 체크리스트
[checklist.md 내용 그대로 출력]

### 최근 수정 (최근 5건)
[changelog.md에서 최근 5건]
```
