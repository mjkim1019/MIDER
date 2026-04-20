"""settings_loader: settings.yaml에서 설정을 로드한다.

Agent별 모델 설정을 중앙 관리하여 하드코딩을 제거한다.
"""

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_SETTINGS_PATH = Path(__file__).parent / "settings.yaml"
_settings_cache: dict[str, Any] | None = None


def _load_settings() -> dict[str, Any]:
    """settings.yaml을 로드하고 캐싱한다."""
    global _settings_cache
    if _settings_cache is not None:
        return _settings_cache

    if not _SETTINGS_PATH.exists():
        logger.warning(f"settings.yaml 없음: {_SETTINGS_PATH}")
        _settings_cache = {}
        return _settings_cache

    with open(_SETTINGS_PATH, encoding="utf-8") as f:
        _settings_cache = yaml.safe_load(f) or {}

    return _settings_cache


def get_agent_model(agent_name: str) -> str:
    """Agent의 기본 모델명을 반환한다.

    Args:
        agent_name: settings.yaml의 agents 하위 키
                    (예: "xml_analyzer", "c_analyzer")

    Returns:
        모델명 (설정 없으면 primary_model, 그것도 없으면 "gpt-5")
    """
    settings = _load_settings()
    llm = settings.get("llm", {})
    agents = llm.get("agents", {})
    agent_cfg = agents.get(agent_name, {})
    return agent_cfg.get("model", llm.get("primary_model", "gpt-5"))


def get_agent_fallback_model(agent_name: str) -> str | None:
    """Agent의 fallback 모델명을 반환한다.

    Args:
        agent_name: settings.yaml의 agents 하위 키

    Returns:
        fallback 모델명 (설정 없으면 글로벌 fallback_model)
    """
    settings = _load_settings()
    llm = settings.get("llm", {})
    agents = llm.get("agents", {})
    agent_cfg = agents.get(agent_name, {})
    return agent_cfg.get("fallback", llm.get("fallback_model"))


def get_agent_temperature(agent_name: str) -> float:
    """Agent의 temperature를 반환한다.

    Args:
        agent_name: settings.yaml의 agents 하위 키

    Returns:
        temperature (설정 없으면 0.0)
    """
    settings = _load_settings()
    llm = settings.get("llm", {})
    agents = llm.get("agents", {})
    agent_cfg = agents.get(agent_name, {})
    return float(agent_cfg.get("temperature", 0.0))


def get_mini_model() -> str:
    """경량 모델명을 반환한다 (Pass 1 선별 등에 사용).

    Returns:
        mini_model (설정 없으면 "gpt-5-mini")
    """
    settings = _load_settings()
    llm = settings.get("llm", {})
    return llm.get("mini_model", "gpt-5-mini")


def get_proc_grouping_config() -> tuple[int, int]:
    """proc_analyzer의 dispatch 그룹핑 줄 수 기준을 반환한다.

    Returns:
        (target_lines, hard_cap_lines)
    """
    settings = _load_settings()
    llm = settings.get("llm", {})
    agents = llm.get("agents", {})
    cfg = agents.get("proc_analyzer", {})
    target = int(cfg.get("group_target_lines", 1000))
    hard_cap = int(cfg.get("group_hard_cap_lines", 1200))
    return target, hard_cap


def get_js_grouping_config() -> tuple[int, int]:
    """js_analyzer의 청크 분할 줄 수 기준을 반환한다.

    Returns:
        (target_lines, hard_cap_lines)
    """
    settings = _load_settings()
    llm = settings.get("llm", {})
    agents = llm.get("agents", {})
    cfg = agents.get("js_analyzer", {})
    target = int(cfg.get("group_target_lines", 2000))
    hard_cap = int(cfg.get("group_hard_cap_lines", 2400))
    return target, hard_cap


def get_stub_extra_types() -> list[str]:
    """clang-tidy 분석용 가짜 헤더(stub)에 추가할 커스텀 타입/매크로를 반환한다.

    Returns:
        extra_types 문자열 리스트
    """
    settings = _load_settings()
    static_analysis = settings.get("static_analysis", {})
    clang_tidy = static_analysis.get("clang_tidy", {})
    return clang_tidy.get("stub_extra_types", [])
