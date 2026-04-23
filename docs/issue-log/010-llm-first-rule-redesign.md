# 010 — LLM-first 룰 시스템 재설계

## 문제 상황

피처 A(룰 YAML 외부화) 계획 수립 과정에서 3가지 근본 문제가 드러남:

1. **SOC 배포 시 사용자 변조 우려**: YAML을 외부 파일로 내보내면 사용자(운영자 아닌 일반 사용자)가 룰을 수정 가능. exe를 SOC로 반입하는 특성상 통제 필요.
2. **Heuristic Scanner 역할 혼재**: Scanner가 "이건 버그다(Detection)"와 "이 함수 살펴봐라(Navigation)" 두 역할을 섞고 있음.
3. **실측 오탐률 55%**: ordsb0100010t01.c 분석에서 11건 중 6건 오탐. ProFrame 프레임워크 보장사항(a000 초기화, DBIO reccnt, LEN_* 자동생성, ctx malloc+memset)을 LLM이 모르는 게 원인. 구 T67로 프롬프트 땜빵 계획했으나 구조적 해결 아님.

## 원인 분석

### Scanner의 Detection/Navigation 혼재

기존 Scanner는 regex 매칭 결과를 그대로 "이슈 후보"로 취급하고 LLM에게 전달. LLM은 "이미 찾았으니 일단 보고" 앵커링에 빠져 `strcpy(buf, literal)` 같은 명백한 안전 케이스도 이슈로 보고. 프롬프트에 "~하지 마라" 규칙을 추가해도 구체 예시 없이는 LLM이 무시하기 쉬움.

### YAML만으론 "왜 이건 안전하고 왜 저건 버그인가" 맥락 부족

YAML regex는 "어디서 탐지할지"만 표현. "왜 이 패턴은 위험하고 다른 건 안전한가"의 맥락은 LLM이 판단해야 하는데, 그 판단을 돕는 구체 예시가 없음. 사내 특화 패턴(SKB_SAFE_* 매크로, ProFrame DBIO)은 코드 수정으로만 반영 가능.

## 리서치 결과

### LLM 역할 확장의 장단점

**장점**:
- 맥락 인식: `strcpy(buf, literal)` 안전 판단 가능
- 의미 이해: 사내 안전 매크로 판별 가능
- 설명 가능: WHY를 제시 (regex는 WHAT만)
- few-shot 학습: 정답/오답 예시로 판단 품질 향상

**단점**:
- 비결정성: 같은 코드 재분석 시 ±10% 편차
- 토큰 비용: 큰 파일 통째 전달 시 폭발
- 큰 파일에서 누락: LLM이 대충 훑고 놓칠 수 있음

### 해결 방향

**"Scanner = Navigator, LLM = Judge"로 역할 분리**:
- Scanner: 큰 파일에서 LLM에게 보여줄 함수/라인 좁히기 (navigation)
- LLM: Skills의 정답/오답 예시 참고하여 최종 판단 (detection)

## 해결 과정

### 1. 피처 통합 재설계

기존 3개 피처(A: 룰 외부화 / C: Skills / D: ProFrame)를 **피처 A(재설계): LLM-first 룰 시스템**으로 통합.

### 2. Task 재정렬

```
T64 (외부 경로 레이어)
  → T65 (Skill 포맷 + Navigator 강등) ← ProFrame 4개 Skill 흡수
  → T57 (YAML 축소, navigation 키워드만)
  → T58 (Scanner 리팩토링)
  → T66 (문서화)
  → T59 (PyInstaller + Ed25519 서명 + dev_mode)
```

### 3. 핵심 구조 변경

- `ScannerFinding` → `NavigationHint` 타입 변경, 단독 이슈 보고 경로 제거
- YAML에서 severity/post_check 등 판단 필드 제거 → Skills로 이동
- 5개 Analyzer `_build_messages()`에 Skill few-shot 자동 주입 (pattern_id 기반)
- 구 T67 ProFrame 대응 = T65.3에서 Skill 4개로 표현
  - `PROFRAME_A000_INIT.md`, `PROFRAME_DBIO_RECCNT.md`, `PROFRAME_LEN_CONSTANT.md`, `PROFRAME_CTX_ALLOCATED.md`

### 4. SOC 배포 방식 결정

**사용자 변조 방지**: Ed25519 서명
- 개인키는 운영자(권한자)만 보유
- exe에 공개키 번들, 외부 Skill/Rule 파일 로드 시 서명 검증
- 검증 실패 시 번들 기본값 fallback + 경고 로그
- 사용자는 파일을 **읽을 수는 있음** (투명성, SOC 감사 통과) → **수정은 불가** (서명 깨짐)

**SOC 반복 테스트 대응**: `MIDER_DEV_MODE=1` 환경변수
- 서명 검증 skip → 테스트 단계에서 YAML/Skill 직접 수정 후 즉시 테스트
- 배포 exe에서는 dev_mode 차단 가능

**암호화 대신 서명 선택 이유**:
- 복호화 키는 exe에서 추출 가능하므로 암호화는 실질 방어력 낮음
- 암호화된 설정 파일은 SOC 보안 감사에서 의심받기 쉬움
- 서명은 가시성 + 무결성 둘 다 확보

## 예상 효과

| 단계 | 예상 FP율 (ordsb0100010t01.c 기준) |
|------|----|
| 현재 | 55% |
| 프롬프트만 강화 (구 T67) | ~30-35% |
| 재설계 (T64 + T65 + ProFrame Skills 4개) | 15-20% |
| 운영 피드백 수개월 축적 | 10% 이하 가능 |

### 메커니즘

1. Few-shot 코드 예시의 LLM 규칙 순응률 >> 프롬프트 문장
2. Scanner 앵커링 제거로 LLM 독립 판단
3. Skill 파일 1개 = 오탐 한 패턴 교정 → 운영 중 SOC 내 피드백 루프 가능

## 관련 파일

- `docs/worklog/plan.md` — 피처 A 재설계 전체 계획
- `docs/worklog/checklist.md` — T64/T65/T57/T58/T66/T59 세부 Subtask
- `docs/worklog/context.md` — 설계 결정 상세
- 영향 받는 코드:
  - `mider/tools/static_analysis/c_heuristic_scanner.py` (Navigator 강등)
  - `mider/tools/static_analysis/js_heuristic_scanner.py`
  - `mider/tools/static_analysis/proc_heuristic_scanner.py`
  - `mider/tools/search/ast_grep_search.py`
  - `mider/agents/*.py` (5개 Analyzer `_build_messages()`)
  - `mider.spec` (번들 구성)

## 주의사항

- **Skill 커버리지 갭**: 예시 없는 프레임워크는 FP 남음 → 초기 10개 Skill(C 3 + ProFrame 4 + JS/SQL 3) 필수
- **과잉 억제 위험**: 오답 예시 과다 시 진짜 버그 놓침 → 회귀 테스트로 감시
- **LLM 비결정성**: temperature 고정 + seed + few-shot으로 완화
- **토큰 예산**: severity=high 우선, 초과 시 오답 예시부터 제외(정답 유지)
- **서명 개인키 분실 대응**: 재배포만 하면 됨. 개인키 분실 자체는 치명적이지 않음

## 상태

- **날짜**: 2026-04-23
- **상태**: 계획 확정, 구현 미착수
- **착수 시점**: 사용자 승인 시 T64부터 시작
