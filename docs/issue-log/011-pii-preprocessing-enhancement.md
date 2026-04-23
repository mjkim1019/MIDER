# 011 — PII 전처리 강화 (AICA 필터 차단 근본 해결)

## 문제 상황

SOC 반입 전 반복 테스트 중 AICA LLM 호출이 다음과 같이 차단/재시도 발생:

```
[14:56:11] WARNING AICA PII 검출 (choices): PASSPORT(NT0000074)
           INFO    PII 검출 → 마스킹 후 재시도 (1/2): PASSPORT(NT0000074)
[14:56:45] WARNING AICA PII/콘텐츠 필터 차단 (content): [Error: Connection error.]
           WARNING C prescan LLM 오류 응답: [Error: Connection error.]
[14:56:49] WARNING AICA PII 검출 (choices): EMAIL(cyber@skbroadband.com),
                    EMAIL(cyber@sktelecom.com), PHONE(16002000)
           INFO    PII 검출 → 마스킹 후 재시도 (1/2)
[14:57:16] WARNING AICA PII/콘텐츠 필터 차단 (content): [Error: Connection error.]
           WARNING C heuristic LLM 오류 응답: [Error: Connection error.]
```

재시도 후에도 Connection error로 분석 실패.

## 원인 분석

### 현재 파이프라인

1. `CommentRemover` — 주석 제거
2. `PIDScanner` (로컬) — 주민/전화/카드/여권/운전면허/외국인등록 6종만 탐지 (Mider 자체 리포팅용)
3. **원본 코드가 AICA로 전송됨** (로컬 선마스킹 없음)
4. AICA 3단 레이어(정규식+사전+libkma)가 PII 검출 → `AICAError + detects` 반환
5. Mider가 `_mask_pii_in_messages()`로 마스킹 후 재시도 (최대 2회)
6. 2회 재시도 후에도 콘텐츠 필터 걸리면 Connection error

### 로컬 PIDScanner 결함 (실측 3건)

| 탐지 미스 | 원본 값 | 로컬 패턴 | 문제 |
|----------|---------|-----------|------|
| 여권번호 | `NT0000074` | `\b[A-Z]\d{8}\b` | 1자+8자만 커버. **2자+7자 miss** |
| 이메일 | `cyber@skbroadband.com` | 없음 | **이메일 패턴 부재** |
| 대표번호 | `16002000` | `01[016789]\|02\|0[3-9]\d` | 01X/02/0X만 커버. **1XXX miss** |

### 추가 갭 (탐정 분석)

Mider가 SKB 통신사 코드를 분석하는 맥락에서 추가로 취약한 영역:

- **통신사 식별자**: IMSI/IMEI/ICCID (LONGDIGIT로만 잡히고 타입 미상)
- **Secret/API key**: AWS/GitHub/JWT 등 (PII 아니지만 유출 시 더 치명적)
- **로마자 한글 이름**: KMA 형태소 분석기는 한글만 처리, 영문 표기 miss
- **안심번호 0507**: SKB가 제공하는 가상번호, 누락

## 리서치 결과

### 해결 방향

**"로컬 선탐지 + 선마스킹 → AICA 깨끗한 텍스트 전송"**

- 원본 → 로컬 PIDScanner (확장) → `_mask_center()` 마스킹 → AICA
- AICA는 이미 마스킹된 텍스트 수신 → 재시도 없음 → Connection error 소멸

### 설계 원칙

- **Over-mask 선호**: under-mask(PII 유출) > over-mask(LLM 정확도 약간 하락)
- **오탐 허용**: 이메일 broad, 로마자 이름 휴리스틱 → 오탐 있어도 마스킹 안전
- **AICA 엔진 신뢰 유지**: 로컬 1차, AICA 2차. 로컬 패턴이 과도하게 복잡해질 필요 없음

## 해결 과정

### 1. 패턴 확장 (T71.1, T71.4, T71.6)

- 여권: `\b[A-Z]{1,2}\d{7,8}\b`
- 대표번호: 15XX/16XX/18XX 19개 프리픽스 + 4자리
- 이메일: `[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}`
- 안심번호: `\b0507[-\s]?\d{3,4}[-\s]?\d{4}\b`
- IMSI: `\b450\d{12}\b` (한국 MCC 450)
- IMEI: 15자리 + Luhn 체크
- ICCID: `\b89\d{17,19}\b`
- 로마자 이름: 주요 성씨 50개 + 3~5자 영문

### 2. 선마스킹 파이프라인 (T71.2)

- `LLMClient._chat_aica()` 진입 시 PIDScanner 실행
- 탐지된 `detect_str`를 기존 `_mask_center()`로 마스킹
- 길이 보존 (AICA 위치 분석 깨지지 않도록)

### 3. 에러 로그 분리 (T71.3)

- 현재 "AICA PII/콘텐츠 필터 차단"으로 묶여 구분 불가
- 분리: PII 필터 / 콘텐츠 필터(유해 코드) / 네트워크/타임아웃
- AICA 응답의 `status_code`, `reason` 개별 로깅

### 4. Secret Scanner 신설 (T71.5)

- 별도 모듈 `mider/tools/preprocessing/secret_scanner.py`
- `detect-secrets` 패턴 포팅 (외부 의존성 없이 정규식만)
- AWS/GitHub/JWT/Google/Stripe/Hardcoded PW/DB URL

## 예상 효과

- AICA 재시도 0회 (네트워크 이슈 제외)
- SOC 반복 테스트 실행 시간 예측 가능
- 통신사 식별자 + Secret 유출 근본 차단
- 테스트 실패/디버깅 시간 대폭 감소

## 관련 파일

- `docs/worklog/plan.md` — 피처 F / T71 전체 계획
- `docs/worklog/checklist.md` — T71.1~T71.7 세부 Subtask
- `docs/worklog/context.md` — 설계 결정 상세
- 영향 받는 코드:
  - `mider/tools/static_analysis/pid_scanner.py` — 패턴 확장 주 대상
  - `mider/config/llm_client.py` — 선마스킹 파이프라인, 에러 로그 분리
  - `mider/tools/preprocessing/secret_scanner.py` — 신규
  - `mider/agents/base_agent.py` — 선마스킹 대안 위치
  - `tests/tools/static_analysis/test_pid_scanner.py` — 회귀 테스트

## 주의사항

- **`_mask_center()` 길이 보존**: AICA 문맥 분석 위치 정보가 깨지지 않도록
- **Luhn false positive 허용**: IMEI 외 15자리도 over-mask될 수 있음 (안전 우선)
- **테스트 fixture 보호**: `tests/fixtures/sample_skb/` 는 커밋 금지 원칙 유지 (로컬 마스킹은 추가 방어선)
- **regex 컴파일**: 모듈 로드 시 1회, 실행 시 비용 없음
- **로깅 투명성**: debug 레벨로 어떤 PII가 로컬에서 마스킹됐는지 추적 가능해야 운영 중 오탐 디버깅 가능

## 상태

- **날짜**: 2026-04-23
- **상태**: 계획 확정, 구현 미착수
- **우선순위**: **P0 최우선** (SOC 반복 테스트 블로커)
- **의존성**: 없음 (즉시 착수 가능)
