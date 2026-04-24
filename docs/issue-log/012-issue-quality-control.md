# 012 — 이슈 품질 관리 (진짜 오류 분리 + OOB 근본 해결)

## 문제 상황

사용자 피드백 (2026-04-23):
> "지금 오류 탐지도 너무 많이 하잖아. 진짜 오류만 탐지하도록 하고. 개선사항 같은건 오류로 탐지하지 못하도록 해줘. 특히 Out of Bound는 헤더에 있는 파일의 사이즈보면 오류가 날 것 같지 않거든? 그래서 [그런] 경우에는 in out 헤더 파일 사용자에게 달라고 하는게 어때?"

두 개의 구조적으로 다른 문제가 섞여 있음:

1. **Issue type 혼동**: 개선사항(strcpy→strlcpy 권장, 매직 넘버, 방어적 NULL 체크)이 `medium/low severity` 오류로 분류되어 진짜 오류를 파묻음
2. **OOB 오탐**: 헤더에 정의된 buffer size 상수(CUST_NAME_LEN 등)를 모르면 LLM이 "OOB 가능성"으로 안전빵 보고

## 원인 분석

### 문제 1: LLM의 과잉 보고

현재 Mider가 LLM에게 "버그 찾아줘"라고 요청 → LLM이 "개선 가능한 모든 것"으로 해석.

LLM 특성:
- 정확도를 높이려고 "가능성" 수준까지 보고
- "이건 오류" / "이건 개선" 구분 지시 없음
- Scanner의 "이미 탐지됨" 앵커링 + severity 체계에 의존

결과: medium/low severity 슬롯이 개선 제안으로 오염 → 진짜 critical/high가 희석됨.

### 문제 2: 분석 컨텍스트의 정보 부족

OOB 판단은 **buffer size + write length 관계**. 둘 중 하나라도 모르면 판단 불가.

```c
#include "cust_dbio.h"  // CUST_NAME_LEN = 50 정의
char name[CUST_NAME_LEN];
memcpy(name, src, CUST_NAME_LEN);  // ← 헤더 없으면 크기 모름
```

현재 파이프라인은 대상 파일만 LLM에 전달. 헤더 없으면 LLM은:
- 안전빵 "OOB 가능성" 보고 (오탐)
- 또는 추측으로 "괜찮을 것 같다" (미탐)

근본 원인은 LLM의 억측이 아니라 **정보 부재**.

## 리서치 결과

### 해결 프레임워크

**두 문제를 분리한 Task 2개**:

| Task | 대상 | 해결 메커니즘 |
|------|------|---------------|
| T72 | 이슈 타입 혼동 | `issue_type` 필드로 스키마 레벨 강제 분리 + Skill frontmatter 통합 |
| T73 | 헤더 의존 OOB | 트리거 조건 만족 시 사용자에게 헤더 요청 인터랙션 |

### T72 설계 핵심

- `issue_type: error | suggestion | info` (severity와 직교 축)
- 5개 Analyzer 프롬프트에 분류 기준 명시 ("애매하면 suggestion, error는 확신 있는 것만")
- Skill frontmatter에 `issue_type` 필드 → Skill 참조 시 자동 분류
- Reporter에서 `# 🔴 오류` / `# 🟡 개선 제안` 섹션 분리
- `--include-suggestions` 옵션 (기본 off)

### T73 설계 핵심

**트리거 조건 (3 AND)** — 무분별한 질문 방지:
1. 미해결 include 존재
2. 분석 대상 연산(memcpy/strcpy/배열/구조체)에 그 헤더 심볼 사용
3. 해당 연산이 OOB/타입/크기 관련

**사용자 선택지 3가지**:
1. 헤더 경로 입력 → 정확한 분석
2. 이 분석 skip → OOB/크기 이슈 보고 안 함
3. 세션 전체 skip → 이번 실행 동안 재질문 없음

**캐싱 전략**:
- v1: `.mider_cache/header_decisions.json`
- v2: T60 완료 후 SQLite 마이그레이션
- 영구: 프로젝트 루트 `.mider_headers.yaml` (팀 공유 가능)

### 피처 간 충돌 / 조정

하드 충돌 없음. 같은 영역 건드리는 Task 조정:

| Task 쌍 | 조정 방법 |
|---------|-----------|
| T65 + T72 | 동시 진행. Skill frontmatter에 `issue_type` 필드 포함 |
| T70 + T72 | reporter.py 공동 작업. 템플릿에 error/suggestion 섹션 분리 |
| T65.3 + T73 | Skills = 1차(framework별), T73 = 2차(general). 역할 분담 |
| T58 + T68 | T58.5 패턴 상수 제거, T68.1 전수 검증. 순서 엄수 |
| T73.4 + T60 | JSON → SQLite 마이그레이션 경로 |
| T64 + T73 | 경로 해석 패턴 유사하나 대상 다름. 독립 구현 |
| T65.4 + issue_merger | Navigator 강등 → merger 변경 → issue_type 전파 순차 |

## 해결 과정

### 1. 피처 G 신규 추가 + 우선순위 재정렬

5개 피처 구조:
- F (PII, 최우선)
- A (LLM-first 룰)
- **G (이슈 품질, 신규)**
- E (Reporter 속도)
- B (캐싱, 중장기)

### 2. Task 번호 부여

- T72 (P0, T65 동시 진행) — 이슈 타입 분류
- T73 (P1, depends T72) — Header 의존성 해결

### 3. 착수 순서

1. T71 (단독 최우선)
2. T64 기반 + T70 병렬
3. **T65 + T72 동시** (프롬프트/스키마 공유)
4. T57 → T58 → **T73** (OOB 근본 해결)
5. T66 → T68 → T59
6. 피처 B 별도 병행

## 예상 효과

| 단계 | 예상 FP율 / 이슈 수 |
|------|---------------------|
| 현재 | FP 55%, medium/low에 개선사항 다수 혼입 |
| T65 (Skills) 완료 | FP 15~20% |
| **T72 (이슈 분류) 완료** | **보고 이슈 수 50~70% 감소** (suggestion 분리) |
| **T73 (Header 해결) 완료** | **OOB 오탐 추가 감소, 헤더 제공 시 정확 탐지** |
| 누적 효과 | 사용자가 받는 "진짜 오류 목록"이 크게 정제됨. FP 10% 이하 가능 |

## 관련 파일

- `docs/worklog/plan.md` — 피처 G / T72, T73 전체 계획 + 충돌 조정 가이드
- `docs/worklog/checklist.md` — T72.1~T72.7, T73.1~T73.7 세부 Subtask
- `docs/worklog/context.md` — 설계 결정 + 참조 문서
- 영향 받는 코드:
  - `mider/models/analysis_result.py` — T72.1 issue_type 필드
  - `mider/agents/*.py` (5개 Analyzer) — T72.2 프롬프트
  - `mider/config/prompts/*.txt` — T72.2
  - `mider/config/skills/_SCHEMA.md` — T72.3 frontmatter
  - `mider/agents/reporter.py` — T72.4 (T70과 통합)
  - `mider/tools/issue_merger.py` — T72.6
  - `mider/tools/preprocessing/include_resolver.py` (신규) — T73.1
  - `mider/main.py` — T73.3/T73.6 CLI 인터랙션
  - `.mider_cache/header_decisions.json` (런타임) — T73.4

## 주의사항

- **T72 과소보고 위험**: "애매하면 suggestion"으로 밀면 진짜 error가 강등될 수 있음 → Skills 정답 예시에 확실한 error 명시로 완화
- **T73 사용자 피로도**: 트리거 조건 엄격히(3 AND) + 세션 캐시로 반복 질문 방지
- **SOC 배치 실행**: T73 `--assume-headers-missing` 또는 `--no-interactive`로 블로킹 회피
- **Skills와 T73 경계**: framework-specific 규칙은 Skills, 일반 사내 매크로는 T73으로 명확히 분리

## 상태

- **날짜**: 2026-04-23
- **상태**: 계획 확정, 구현 미착수
- **우선순위**: T72 P0 (T65 동시), T73 P1 (T72 후)
- **의존성**: T72 없음 / T73은 T72 필요
