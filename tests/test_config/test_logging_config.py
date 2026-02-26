"""logging_config 단위 테스트."""

import logging
from unittest.mock import patch

from mider.config.logging_config import setup_logging


class TestSetupLogging:
    def test_default_level(self):
        """기본 로그 레벨은 INFO이다."""
        with patch.dict("os.environ", {}, clear=True):
            setup_logging()
            root_logger = logging.getLogger()
            assert root_logger.level == logging.INFO

    def test_custom_level(self):
        """커스텀 로그 레벨을 설정할 수 있다."""
        setup_logging(level="DEBUG")
        root_logger = logging.getLogger()
        assert root_logger.level == logging.DEBUG

    def test_env_level(self):
        """환경 변수로 로그 레벨을 설정할 수 있다."""
        with patch.dict("os.environ", {"MIDER_LOG_LEVEL": "WARNING"}):
            setup_logging()
            root_logger = logging.getLogger()
            assert root_logger.level == logging.WARNING

    def test_external_libs_suppressed(self):
        """외부 라이브러리 로거가 WARNING 이상으로 설정된다."""
        setup_logging()
        assert logging.getLogger("openai").level == logging.WARNING
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING

    def test_rich_handler_attached(self):
        """RichHandler가 루트 로거에 연결된다."""
        setup_logging()
        from rich.logging import RichHandler

        root_logger = logging.getLogger()
        has_rich = any(
            isinstance(h, RichHandler) for h in root_logger.handlers
        )
        assert has_rich
