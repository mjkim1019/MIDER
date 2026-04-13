# 작업 계획서

## 개요
memset sizeof 타입 불일치 탐지 — Scanner regex 패턴 추가 + LLM 프롬프트 few-shot 예시 추가. `memset(&var_u0010_in, 0, sizeof(var_s0009_in_t))` 같은 복붙 버그를 정적+LLM 양쪽에서 잡는다.

---

## Task 목록

### T55: memset sizeof 타입 불일치 탐지
- T55.1: Scanner `MEMSET_SIZE_MISMATCH` 패턴 추가 → `mider/tools/static_analysis/c_heuristic_scanner.py`
  - regex: `memset(&변수명, ..., sizeof(타입명))` 에서 변수명 접두사 ≠ 타입명 접두사 탐지
  - 변수명에서 `_in`, `_out`, `_io` 등 접미사 제거 후 비교
  - severity: high (잘못된 크기로 memset → 데이터 오염 또는 오버플로우)
- T55.2: LLM 프롬프트에 memset 타입 불일치 체크 항목 + few-shot 예시 추가 → `mider/config/prompts/c_analyzer_error_focused.txt`, `c_analyzer_heuristic.txt`
  - Error-Focused: Step 2 심층 패턴 분석에 memset sizeof 불일치 항목 추가
  - Heuristic: 체크리스트에 memset sizeof 불일치 항목 추가
  - 양쪽 모두 실제 버그 예시(u0010 vs s0009) 포함
- T55.3: 단위 테스트 → `tests/test_tools/test_c_heuristic_scanner.py`
  - 불일치 케이스: `memset(&var_u0010_in, 0, sizeof(var_s0009_in_t))` → 탐지
  - 정상 케이스: `memset(&var_s0009_in, 0, sizeof(var_s0009_in_t))` → 미탐지
  - 구조체 멤버 접근: `memset(&ctx->var, 0, sizeof(var_t))` → 미탐지 (멤버는 타입 추론 불가)

---

## 설계 결정

| 결정 | 이유 |
|------|------|
| **변수명 접두사 vs sizeof 타입명 접두사 비교** | ProFrame 코드 네이밍 규칙: `zord_abn_sale_spc_u0010_in` → 타입은 `zord_abn_sale_spc_u0010_in_t`. 접미사(`_in`, `_out`, `_io`) 제거 + `_t` 추가가 타입명. 접두사가 다르면 복붙 버그 |
| **Scanner + 프롬프트 양쪽 모두 추가** | Scanner는 확실한 패턴만 잡고(높은 정밀도), LLM은 문맥 기반으로 추가 탐지(높은 재현율). 양쪽 보완 |
| **구조체 멤버(`ctx->var`)는 Scanner에서 제외** | 멤버 접근은 변수명만으로 타입 추론 불가. LLM에 위임 |
| **severity: high** | sizeof 크기 불일치는 오버플로우 또는 불완전 초기화 — critical까지는 아니지만 런타임 데이터 오염 확실 |

## 의존성

| Task | 의존 | 비고 |
|------|------|------|
| T55 | 없음 | Scanner 패턴 + 프롬프트 수정, 외부 인터페이스 변경 없음 |
