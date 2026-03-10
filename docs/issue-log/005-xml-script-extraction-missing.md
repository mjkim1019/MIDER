# 이슈 #005: XML `<script>` 미추출 및 토큰 비효율

## 발견일
2026-03-10

## 발견 경위
T19 구현 후 실제 WebSquare XML 파일(8,109줄)로 테스트하면서 발견.

## 문제 상황

### 1. `<script>` 내 JS 코드를 추출하지 않음
- WebSquare XML의 `<script>` 태그에 CDATA로 감싼 인라인 JS가 핵심 비즈니스 로직
- 실제 파일: 5,951줄, 85개 함수, ~65K tokens
- **현재 XMLParser는 `<script>` 내용을 아예 추출하지 않음**
- 실제 장애 유발 패턴은 대부분 이 JS 코드에서 발생

### 2. dataList 전체를 LLM에 전송하는 토큰 비효율
- 108개 dataList, 888개 컬럼 → ~23K tokens (프롬프트의 76%)
- dataList는 스키마 정의일 뿐, 버그 발생 가능성 낮음
- 대부분의 토큰이 분석 가치가 낮은 데이터에 소비됨

### 3. `<body>` UI 컴포넌트 정보 부족
- 현재 ID만 추출, 컴포넌트 속성(바인딩, 유효성 검사 등)은 미추출

## 실제 파일 분석 결과

| 영역 | 크기 | 현재 파서 | 버그 가능성 |
|------|------|-----------|-------------|
| `<head>` dataList | ~23K tokens | 전체 추출 | 낮음 |
| `<body>` 컴포넌트 | 126개 | ID만 추출 | 중간 |
| `<script>` JS | ~65K tokens, 85함수 | **미추출** | **높음** |

## 제안 해결 방향

### Phase 1: script 추출 추가
- XMLParser에 `<script>` 태그 내 JS 코드 추출 기능 추가
- 함수 단위로 분리 (scwin.funcName = function 패턴)

### Phase 2: 토큰 최적화 (C분석 T16/T21 패턴 적용)
1. **구조 요약** (~2K tokens): dataList 이름+컬럼수, body 태그별 카운트, 이벤트 목록
2. **script JS 코드** (핵심): 함수별 분할 후 개별 LLM 호출 또는 에러 함수 선별
3. **이슈 데이터** (조건부): duplicate IDs, missing handlers

### Phase 3: 중복 ID 스코프 개선
- 현재: 전체 XML에서 중복 검출 → 서로 다른 dataList 간 동명 컬럼도 중복 보고
- 개선: dataList 내부 스코프에서만 중복 검출

## 관련
- T16: 토큰 최적화 (Structure + Function Window) — 동일 패턴 적용 가능
- T21: Pass 2 함수별 개별 LLM 호출 — script 함수별 호출에 참고
