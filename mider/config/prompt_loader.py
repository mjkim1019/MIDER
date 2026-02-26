"""PromptLoader: 프롬프트 파일 로드 유틸리티.

config/prompts/ 디렉토리에서 프롬프트 템플릿을 읽고 변수를 치환한다.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# config/prompts/ 디렉토리 경로
PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str, **variables: str) -> str:
    """프롬프트 파일을 로드하고 변수를 치환한다.

    Args:
        name: 프롬프트 파일명 (확장자 제외). 예: "js_analyzer_error_focused"
        **variables: f-string 치환 변수. 예: file_content="...", eslint_errors="..."

    Returns:
        변수가 치환된 프롬프트 문자열

    Raises:
        FileNotFoundError: 프롬프트 파일이 존재하지 않을 때
    """
    prompt_path = PROMPTS_DIR / f"{name}.txt"

    if not prompt_path.exists():
        raise FileNotFoundError(
            f"프롬프트 파일을 찾을 수 없습니다: {prompt_path}"
        )

    template = prompt_path.read_text(encoding="utf-8")
    logger.debug(f"프롬프트 로드: {name}")

    if variables:
        try:
            return template.format(**variables)
        except KeyError as e:
            logger.error(f"프롬프트 변수 누락: {e}")
            raise

    return template
