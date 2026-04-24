"""외부 리소스 경로 해석 레이어.

프롬프트/룰/Skill 파일을 3단계 우선순위로 해석한다:

1. **환경변수** — `MIDER_PROMPTS_PATH` / `MIDER_RULES_PATH` / `MIDER_SKILLS_PATH`
2. **exe 옆 디렉토리** — PyInstaller 배포 시 실행파일 옆의 `mider_prompts/`, `mider_rules/`, `mider_skills/`
3. **번들 fallback** — `mider/config/prompts`, `mider/config/rules`, `mider/config/skills` (패키지 내장)

각 레이어에서 해당 파일이 존재하면 그 경로 반환. 없으면 다음 레이어 시도.
최종 fallback은 번들 경로 (파일 존재 여부 무관).

## 사용 예시

```python
from mider.config.resource_path import get_prompt_path, get_rule_path, get_skill_path

prompt_path = get_prompt_path("reporter")             # .../prompts/reporter.txt
rule_path = get_rule_path("c_rules")                  # .../rules/c_rules.yaml
skill_path = get_skill_path("UNSAFE_FUNC")            # .../skills/UNSAFE_FUNC.md
```

## 배포/커스터마이징

운영자는 exe 옆 `mider_prompts/`에 커스텀 프롬프트를 두면 번들보다 우선 적용된다.
환경변수로 임시 오버라이드도 가능 (디버깅/테스트).
"""

import os
import sys
from pathlib import Path

# ─────────────────────────────────────────────────────────
# 환경변수 이름
# ─────────────────────────────────────────────────────────
ENV_PROMPTS = "MIDER_PROMPTS_PATH"
ENV_RULES = "MIDER_RULES_PATH"
ENV_SKILLS = "MIDER_SKILLS_PATH"

# ─────────────────────────────────────────────────────────
# exe 옆 디렉토리명 (PyInstaller frozen 환경에서 사용)
# ─────────────────────────────────────────────────────────
EXE_PROMPTS_DIRNAME = "mider_prompts"
EXE_RULES_DIRNAME = "mider_rules"
EXE_SKILLS_DIRNAME = "mider_skills"

# ─────────────────────────────────────────────────────────
# 번들 기본 경로 (mider 패키지 기준)
# ─────────────────────────────────────────────────────────
_CONFIG_DIR = Path(__file__).parent  # mider/config
BUNDLED_PROMPTS_DIR = _CONFIG_DIR / "prompts"
BUNDLED_RULES_DIR = _CONFIG_DIR / "rules"
BUNDLED_SKILLS_DIR = _CONFIG_DIR / "skills"


def _get_exe_dir() -> Path | None:
    """PyInstaller 실행파일 옆 디렉토리를 반환한다.

    frozen 환경이 아니면 `None`.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return None


def _resolve_file(
    name: str,
    extension: str,
    env_var: str,
    exe_subdir: str,
    bundled_dir: Path,
) -> Path:
    """3단계 우선순위로 리소스 파일 경로를 해석한다.

    Args:
        name: 리소스명 (확장자 제외).
        extension: 확장자 (예: ``"txt"``, ``"yaml"``, ``"md"``).
        env_var: 환경변수 이름.
        exe_subdir: exe 옆 디렉토리명.
        bundled_dir: 번들 fallback 디렉토리 경로.

    Returns:
        해석된 Path. 최종 fallback으로 번들 경로를 반환하며,
        파일이 존재하지 않아도 그대로 반환 (호출자가 FileNotFoundError 처리).
    """
    filename = f"{name}.{extension}"

    # 1. 환경변수
    env_path = os.environ.get(env_var)
    if env_path:
        candidate = Path(env_path) / filename
        if candidate.exists():
            return candidate

    # 2. exe 옆
    exe_dir = _get_exe_dir()
    if exe_dir:
        candidate = exe_dir / exe_subdir / filename
        if candidate.exists():
            return candidate

    # 3. 번들 fallback (존재 여부 무관 반환)
    return bundled_dir / filename


def _resolve_dir(
    env_var: str,
    exe_subdir: str,
    bundled_dir: Path,
) -> Path:
    """리소스 디렉토리 경로를 3단계 우선순위로 해석한다.

    환경변수 또는 exe 옆 경로가 **디렉토리로 존재**하면 그 경로,
    없으면 번들 디렉토리 반환.
    """
    env_path = os.environ.get(env_var)
    if env_path:
        candidate = Path(env_path)
        if candidate.is_dir():
            return candidate

    exe_dir = _get_exe_dir()
    if exe_dir:
        candidate = exe_dir / exe_subdir
        if candidate.is_dir():
            return candidate

    return bundled_dir


# ─────────────────────────────────────────────────────────
# 공개 API: 파일 경로
# ─────────────────────────────────────────────────────────

def get_prompt_path(name: str) -> Path:
    """프롬프트 파일(`.txt`) 경로를 해석한다.

    Args:
        name: 프롬프트 이름 (확장자 제외). 예: ``"reporter"``, ``"js_analyzer"``.
    """
    return _resolve_file(name, "txt", ENV_PROMPTS, EXE_PROMPTS_DIRNAME, BUNDLED_PROMPTS_DIR)


def get_rule_path(name: str) -> Path:
    """룰 YAML(`.yaml`) 파일 경로를 해석한다.

    Args:
        name: 룰 이름 (확장자 제외). 예: ``"c_rules"``, ``"js_rules"``.
    """
    return _resolve_file(name, "yaml", ENV_RULES, EXE_RULES_DIRNAME, BUNDLED_RULES_DIR)


def get_skill_path(pattern_id: str) -> Path:
    """Skill 파일(`.md`) 경로를 해석한다.

    Args:
        pattern_id: Skill 패턴 ID. 예: ``"UNSAFE_FUNC"``, ``"PROFRAME_A000_INIT"``.
    """
    return _resolve_file(pattern_id, "md", ENV_SKILLS, EXE_SKILLS_DIRNAME, BUNDLED_SKILLS_DIR)


# ─────────────────────────────────────────────────────────
# 공개 API: 디렉토리 경로
# ─────────────────────────────────────────────────────────

def get_prompts_dir() -> Path:
    """프롬프트 디렉토리 경로를 해석한다 (우선순위 탐색)."""
    return _resolve_dir(ENV_PROMPTS, EXE_PROMPTS_DIRNAME, BUNDLED_PROMPTS_DIR)


def get_rules_dir() -> Path:
    """룰 디렉토리 경로를 해석한다."""
    return _resolve_dir(ENV_RULES, EXE_RULES_DIRNAME, BUNDLED_RULES_DIR)


def get_skills_dir() -> Path:
    """Skill 디렉토리 경로를 해석한다."""
    return _resolve_dir(ENV_SKILLS, EXE_SKILLS_DIRNAME, BUNDLED_SKILLS_DIR)
