# 개발기 vs 운영기 — Mider 운영 가이드

Mider는 두 환경에서 다르게 동작합니다. 각 환경의 세팅/실행 방법과 리소스 커스터마이징 절차를 정리합니다.

---

## 한눈에 보기

| 영역 | 개발기 (dev) | 운영기 (prod, SKT 폐쇄망) |
|------|-------------|-------------------------|
| **배포 형태** | 소스 저장소 + Python venv | PyInstaller 단일 실행파일 (`mider.exe` / `mider`) |
| **mider 설치** | `pip install -e .` | 불필요 (exe에 번들) |
| **실행 명령** | `python -m mider.main <args>` 또는 `mider <args>` | `./mider <args>` |
| **LLM Backend** | OpenAI 또는 AICA (선택) | **AICA only** (폐쇄망) |
| **인증 방식** | API key (OpenAI/AICA) | SSO (`--sso`, Chrome CDP + Selenium) |
| **프롬프트 수정** | `mider/config/prompts/*.txt` 직접 편집 | `mider_prompts/`를 exe 옆에 drop |
| **룰/Skill 수정** | `mider/config/rules/` / `mider/config/skills/` 직접 편집 | `mider_rules/` / `mider_skills/`를 exe 옆에 drop |
| **환경변수 설정** | `.env` 파일 또는 shell export | 실행 전 shell export 또는 `.env` 동봉 |
| **테스트** | `pytest` | 해당 없음 (dev기에서 검증) |
| **로깅 레벨** | verbose 플래그 활성화 가능 | INFO 기본 (settings.yaml로 조정) |

---

## 1. 리소스 경로 해석 (T64 공통)

Mider는 **3단계 우선순위**로 프롬프트/룰/Skill 파일을 해석합니다 (`mider/config/resource_path.py`):

```
1. 환경변수     (MIDER_PROMPTS_PATH / MIDER_RULES_PATH / MIDER_SKILLS_PATH)
       ↓ (해당 파일 없으면)
2. exe 옆       (mider_prompts/ / mider_rules/ / mider_skills/)   ← 운영기에서만
       ↓ (해당 파일 없으면)
3. 번들 fallback (mider/config/prompts|rules|skills)               ← 항상 존재
```

- **개발기**: PyInstaller frozen 환경이 아니라 2단계(exe 옆)는 자동 skip → `환경변수 → 번들(소스트리)` 순
- **운영기**: exe 옆 커스텀 디렉토리가 있으면 번들보다 우선 적용

---

## 2. 개발기 워크플로우

### 2.1 환경 세팅

```bash
cd /path/to/Mider
python -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -r requirements-dev.txt   # pytest, ruff 등
```

### 2.2 환경변수 (.env)

```bash
# OpenAI 사용 시
OPENAI_API_KEY=sk-...

# AICA 사용 시
AICA_API_KEY=your-api-key
AICA_ENDPOINT=https://aica.sktelecom.com:3000
AICA_USER_ID=your-user-id

# 선택: 특정 리소스 디렉토리 override
# MIDER_PROMPTS_PATH=/path/to/custom/prompts
```

### 2.3 실행

```bash
# 기본
python -m mider.main analyze ./input

# AICA + SSO
python -m mider.main --sso analyze ./input

# Verbose 로그
python -m mider.main -v analyze ./input
```

### 2.4 프롬프트/Skill 커스터마이징 (개발기)

소스 트리에서 직접 편집:
```bash
vim mider/config/prompts/reporter.txt
vim mider/config/skills/UNSAFE_FUNC.md
```

변경 즉시 반영 (`pip install -e .` 편집 가능 모드 기준).

### 2.5 테스트

```bash
pytest                                      # 전체
pytest tests/test_config/test_prompt_loader.py -v    # 개별
pytest -m "not slow"                        # 빠른 테스트만
```

### 2.6 운영기용 exe 빌드

```bash
python scripts/build_exe.py
# 결과: dist/mider (단일 실행파일)
```

---

## 3. 운영기 워크플로우 (SKT 폐쇄망)

### 3.1 배포 파일

운영기에는 dev기에서 빌드한 단일 exe만 전달:
```
dist/mider              ← 실행파일 (Python 미포함 단독 실행)
dist/.env               ← API 키/엔드포인트 (선택, exe에 번들 가능)
```

### 3.2 환경변수 설정

운영기 배포 전 exe 옆에 `.env` 또는 shell에 export:
```bash
export AICA_API_KEY=prod-key
export AICA_ENDPOINT=https://aica.sktelecom.com:3000
export AICA_USER_ID=prod-user
```

### 3.3 실행

```bash
./mider --sso analyze /path/to/src
```

### 3.4 프롬프트/Skill 커스터마이징 (운영기)

**원칙**: exe 내부 파일은 수정 불가. 외부 디렉토리에 커스텀 파일을 drop하면 번들보다 우선 적용됨.

**3.4.1 커스터마이징 기본 파일 export** (dev기 또는 운영기에서 최초 1회)

```bash
# 운영기에서 직접 (exe가 이미 배포된 상태)
./mider-export-resources --output ./

# 또는 dev기에서 미리 export해서 운영기에 전달
python scripts/export_default_resources.py --output ./dist/
```

결과:
```
./mider_prompts/*.txt   ← 13개 기본 프롬프트
./mider_rules/*.yaml    ← 룰 파일 (T57 이후)
./mider_skills/*.md     ← Skill 파일 (T65 이후)
```

**3.4.2 원하는 파일 수정 후 exe 옆에 배치**

```
/deploy/
├── mider                      ← 실행파일
├── mider_prompts/             ← 커스텀 프롬프트 (번들보다 우선)
│   └── reporter.txt           ← 수정본
├── mider_rules/               ← 커스텀 룰 (옵션)
└── mider_skills/              ← 커스텀 Skill (옵션)
```

**3.4.3 일부 파일만 override**

`mider_prompts/` 폴더 안에 `reporter.txt`만 있고 다른 프롬프트가 없어도 됩니다. 없는 프롬프트는 번들에서 자동 fallback.

**3.4.4 환경변수로 임시 override (디버깅용)**

```bash
export MIDER_PROMPTS_PATH=/tmp/debug_prompts
./mider analyze ./src
```

---

## 4. dev/prod 차이 감지 방법

코드 내에서 환경을 확인해야 할 때:

```python
import sys

if getattr(sys, "frozen", False):
    # 운영기 (PyInstaller bundled exe)
    ...
else:
    # 개발기 (Python 직접 실행)
    ...
```

이미 `resource_path.py`의 `_get_exe_dir()`이 이 분기를 처리합니다.

---

## 5. 트러블슈팅

### 5.1 운영기에서 커스텀 프롬프트가 적용 안 됨

확인 순서:
1. `mider_prompts/` 폴더가 **exe와 같은 디렉토리**에 있는지 (하위 폴더 아님)
2. 파일명이 정확한지 (`reporter.txt` vs `Reporter.txt`)
3. 환경변수가 덮어쓰고 있지 않은지 (`echo $MIDER_PROMPTS_PATH`)

### 5.2 개발기에서 exe 옆 override가 안 적용됨

정상 동작입니다. `sys.frozen == False`이므로 2단계(exe 옆)를 건너뜁니다.
환경변수로 override하세요: `export MIDER_PROMPTS_PATH=/path/to/custom/prompts`

### 5.3 "프롬프트 파일을 찾을 수 없습니다" 에러

번들에도 해당 프롬프트가 없을 때 발생. 파일명 확인 또는 번들 재빌드.

---

## 6. 체크리스트

### 개발기 초기 세팅
- [ ] Python 3.11+ venv 생성
- [ ] `pip install -e .` + dev 의존성
- [ ] `.env` 파일 생성 (API 키)
- [ ] `pytest` 통과 확인

### 운영기 배포
- [ ] dev기에서 `python scripts/build_exe.py` 빌드
- [ ] 단일 exe 파일 운영기로 전달
- [ ] 운영기에 `.env` 배치 (AICA 설정)
- [ ] `./mider --healthcheck` 동작 확인 (있는 경우)
- [ ] 커스텀 리소스 `mider_prompts/` 등 필요 시 exe 옆에 배치
- [ ] 실제 소스코드 1건으로 통합 실행 검증
