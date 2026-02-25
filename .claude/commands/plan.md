사용자의 요청을 분석하여 작업 계획을 수립합니다. 아래 절차를 순서대로 수행하세요.

## Step 1: 요청 분석
1. 사용자의 요청을 파악한다
2. 관련 문서를 읽는다:
   - docs/manuals/index.md (목차 먼저)
   - 해당 챕터 매뉴얼
   - docs/TECH_SPEC.md (필요 시)
   - docs/DATA_SCHEMA.md (필요 시)

## Step 2: Task 분해
1. 요청을 Task 단위로 나눈다 (T1, T2, T3...)
2. 각 Task를 Subtask로 분해한다 (T1.1, T1.2...)
3. Task 간 의존성을 파악한다

## Step 3: 3개 문서 생성

### docs/worklog/plan.md
```
# 작업 계획서

## 개요
[요청 내용 1-2줄 요약]

## Task 목록

### T1: [task 제목]
- T1.1: [subtask] → 대상 파일: [파일 경로]
- T1.2: [subtask] → 대상 파일: [파일 경로]

### T2: [task 제목] (depends: T1)
- T2.1: [subtask] → 대상 파일: [파일 경로]
```

### docs/worklog/context.md
```
# 맥락 노트

## 설계 결정
- [결정 1]: [이유]
- [결정 2]: [이유]

## 참조 문서
- docs/TECH_SPEC.md: [참조한 섹션]
- docs/DATA_SCHEMA.md: [참조한 스키마]

## 주의사항
- [주의 1]
- [주의 2]
```

### docs/worklog/checklist.md
```
# 체크리스트

- [ ] T1: [task 제목]
  - [ ] T1.1: [subtask]
  - [ ] T1.2: [subtask]
- [ ] T2: [task 제목]
  - [ ] T2.1: [subtask]
```

## Step 4: TaskCreate 등록
각 Task를 TaskCreate tool로 등록한다.

## Step 5: 사용자 승인
계획서를 사용자에게 보여주고 승인을 받는다.
승인되면 "/task로 첫 번째 task를 시작하세요"라고 안내한다.
