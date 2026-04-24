"""PromptLoader: 프롬프트 파일 로드 유틸리티.

`mider.config.resource_path.get_prompt_path()`를 사용하여 3단계 우선순위로 해석:
환경변수(`MIDER_PROMPTS_PATH`) > exe 옆(`mider_prompts/`) > 번들(`mider/config/prompts/`).
"""

import logging

from mider.config.resource_path import BUNDLED_PROMPTS_DIR, get_prompt_path

logger = logging.getLogger(__name__)

# 번들 경로 alias (기존 코드 및 번들 파일 존재 검증 테스트 호환)
PROMPTS_DIR = BUNDLED_PROMPTS_DIR


def load_prompt(name: str, **variables: str) -> str:
    """프롬프트 파일을 로드하고 변수를 치환한다.

    Args:
        name: 프롬프트 파일명 (확장자 제외). 예: ``"js_analyzer_error_focused"``.
        **variables: f-string 치환 변수. 예: ``file_content="..."``, ``eslint_errors="..."``.

    Returns:
        변수가 치환된 프롬프트 문자열.

    Raises:
        FileNotFoundError: 프롬프트 파일이 존재하지 않을 때.
    """
    prompt_path = get_prompt_path(name)

    if not prompt_path.exists():
        raise FileNotFoundError(
            f"프롬프트 파일을 찾을 수 없습니다: {prompt_path}"
        )

    template = prompt_path.read_text(encoding="utf-8")
    logger.debug(f"프롬프트 로드: {name} ({prompt_path})")

    if variables:
        try:
            return template.format(**variables)
        except KeyError as e:
            logger.error(f"프롬프트 변수 누락: {e}")
            raise

    return template
