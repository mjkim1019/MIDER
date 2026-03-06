# Issue #001: 대형 C 파일 중간 코드 분석 누락

|             |                                      |                                                                                           |
| ----------- | ------------------------------------ | ----------------------------------------------------------------------------------------- |
| **이슈 구분**   | **문제 상황 및 원인**                       | **리서치 및 해결 과정 (Reference & Solution)**                                                    |
| **Problem** | clang-tidy 미설치 시 Heuristic 경로에서 2932줄 파일의 앞 200줄 + 뒤 100줄만 LLM에 전달. 중간 2383~2462줄의 `svc_cnt` 초기화 누락 버그를 탐지하지 못함 | - **리서치:** Error-Focused 경로는 정적분석 결과 기반으로 해당 함수만 추출하여 LLM에 전달 → 커버리지 100%. 그러나 clang-tidy가 없으면 Heuristic 경로로 빠지며 head+tail만 전달 → 중간 코드 사각지대 발생 - **원인:** `optimize_file_content()`이 파일 구조를 고려하지 않고 단순 head(200)+tail(100)로 자름 |
| **도구 연동**   | clang-tidy는 시스템 바이너리(LLVM)로, 폐쇄망에서 미설치 가능성 높음. resources/binaries/에 넣어야 하나 배포 전까지 비어있음 | - **리서치:** clang-tidy 대체로 regex 기반 C 패턴 스캐너 구현 가능. 전체 파일을 빠르게 스캔하여 위험 함수를 특정한 뒤 Error-Focused 경로로 연결 - **적용:** 아래 해결 방안 참조 |
| **성능/기타**   | 함수 단위 청크로 LLM 다중 호출 시 비용/시간 증가 우려 | - **리서치:** 2-Pass 전략으로 해결. Pass 1(gpt-4o-mini, 저비용)에서 위험 함수 선별 → Pass 2(gpt-4o)에서 선별된 함수만 심층 분석. 전체 파일을 gpt-4o로 분석하는 것보다 효율적 |

---

## 해결 방안: C Heuristic Pre-Scanner + Few-Shot 기반 2-Pass 분석

### 현재 흐름 (문제)

```
대형 C 파일 (2932줄)
  → clang-tidy 없음
  → Heuristic 경로
  → optimize_file_content(): 앞 200줄 + 뒤 100줄만 추출
  → LLM 분석: 중간 2632줄 미분석
  → svc_cnt 초기화 누락 등 버그 미탐지
```

### 개선 흐름

```
대형 C 파일 (2932줄)
  → clang-tidy 없음
  → [NEW] Pass 1: Heuristic Pre-Scanner (전체 파일 스캔)
      ├── regex 기반 위험 패턴 탐지 (전체 파일, 비용 0)
      ├── 함수 경계 추출 (_find_function_boundaries)
      └── 위험 함수 목록 + few-shot 예시 → gpt-4o-mini로 선별
  → [NEW] Pass 2: Error-Focused 심층 분석
      ├── Pass 1에서 선별된 함수 전문(full body) 추출
      └── few-shot 예시와 함께 gpt-4o로 심층 분석
  → 이슈 탐지 (svc_cnt 초기화 누락 등)
```

### Pass 1: Heuristic Pre-Scanner

**목적**: 전체 파일에서 위험 함수를 빠르게 선별

**Step 1 - Regex 기반 위험 패턴 탐지** (비용 0, 즉시 실행)

전체 파일을 스캔하여 아래 패턴이 포함된 라인 번호를 수집:

| 패턴 ID | 탐지 대상 | regex 예시 |
|---------|----------|-----------|
| `UNINIT_VAR` | 초기화 없는 지역 변수 선언 | `^\s+(int\|long\|char\|double\|float\|short)\s+\w+\s*;` (= 없이 ; 으로 끝남) |
| `UNSAFE_FUNC` | 위험 함수 사용 | `strcpy\|sprintf\|strcat\|gets\|scanf` |
| `BOUNDED_FUNC` | 경계 검사 필요 함수 | `strncpy\|memcpy\|memset\|memmove` |
| `NULL_DEREF` | NULL 체크 없는 포인터 사용 | 포인터 선언 후 NULL 비교 없이 `->` 사용 |
| `UNCHECKED_RET` | 반환값 미검증 | 함수 호출 결과를 변수에 저장하지 않음 |
| `BUFFER_ACCESS` | 배열 인덱스 접근 | `\w+\[\w+\]` (변수 인덱스 접근) |

**Step 2 - 위험 함수 선별** (gpt-4o-mini, 저비용)

- 함수 시그니처 + 위험 패턴 요약을 gpt-4o-mini에 전달
- few-shot 예시로 "이런 패턴은 위험하다"를 학습시킴
- LLM이 심층 분석이 필요한 함수를 선택

**Few-Shot 예시 (사용자 제공):**

```
[예시 1: 초기화 누락]
함수: c400_get_rcv_chgreq_possible
패턴: long svc_cnt; (초기화 없음) → svc_cnt를 배열 인덱스로 사용 → 미정의 동작
판정: 위험 — 심층 분석 필요

[예시 2: strncpy null-terminator 누락]
함수: c100_init
패턴: strncpy(dst, src, MAX_LEN); → null-terminator 미보장
판정: 위험 — 심층 분석 필요

[예시 3: 안전한 함수]
함수: main
패턴: memset(&ctx, 0x00, sizeof(ctx)); → sizeof 대상 일치, 초기화 즉시 수행
판정: 안전 — 생략
```

### Pass 2: Error-Focused 심층 분석

- Pass 1에서 선별된 함수의 **전체 코드**를 추출
- few-shot 예시 + 함수 코드를 gpt-4o에 전달
- 기존 `c_analyzer_error_focused` 프롬프트 경로 재활용

### 구현 대상 파일

| 파일 | 변경 내용 |
|------|----------|
| `mider/tools/static_analysis/c_heuristic_scanner.py` | [NEW] regex 기반 C 패턴 스캐너 |
| `mider/agents/c_analyzer.py` | Heuristic 경로에서 Pre-Scanner 호출 → 2-Pass 분석 |
| `mider/config/prompts/c_prescan_fewshot.txt` | [NEW] Pass 1 few-shot 프롬프트 |
| `mider/config/prompts/c_analyzer_heuristic.txt` | Pass 2 few-shot 예시 추가 |
| `mider/tools/utility/token_optimizer.py` | `optimize_file_content` → 함수 단위 추출 지원 |

### 기대 효과

| 항목 | Before | After |
|------|--------|-------|
| 코드 커버리지 | ~10% (300/2932줄) | 100% (전체 함수 스캔) |
| svc_cnt 초기화 누락 | 미탐지 | 탐지 |
| LLM 비용 | gpt-4o 1회 | gpt-4o-mini 1회 + gpt-4o 1회 (위험 함수만) |
| 분석 시간 | ~22초 | ~30초 (Pass 1 추가) |
