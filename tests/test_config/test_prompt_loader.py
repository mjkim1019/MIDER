"""PromptLoader 단위 테스트."""

import pytest

from mider.config.prompt_loader import PROMPTS_DIR, load_prompt


class TestLoadPrompt:
    def test_file_not_found(self):
        """존재하지 않는 프롬프트 파일은 FileNotFoundError를 raise한다."""
        with pytest.raises(FileNotFoundError, match="프롬프트 파일을 찾을 수"):
            load_prompt("nonexistent_prompt")

    def test_load_without_variables(self, tmp_path, monkeypatch):
        """변수 없이 프롬프트를 로드한다."""
        prompt_file = tmp_path / "test_simple.txt"
        prompt_file.write_text("Hello, this is a test prompt.", encoding="utf-8")

        monkeypatch.setattr(
            "mider.config.prompt_loader.PROMPTS_DIR", tmp_path
        )

        result = load_prompt("test_simple")
        assert result == "Hello, this is a test prompt."

    def test_load_with_variables(self, tmp_path, monkeypatch):
        """변수를 치환하여 프롬프트를 로드한다."""
        prompt_file = tmp_path / "test_vars.txt"
        prompt_file.write_text(
            "Analyze {file_content}\n\nErrors: {eslint_errors}",
            encoding="utf-8",
        )

        monkeypatch.setattr(
            "mider.config.prompt_loader.PROMPTS_DIR", tmp_path
        )

        result = load_prompt(
            "test_vars",
            file_content="console.log('hi')",
            eslint_errors="no-undef",
        )
        assert "console.log('hi')" in result
        assert "no-undef" in result

    def test_missing_variable_raises(self, tmp_path, monkeypatch):
        """필수 변수가 누락되면 KeyError를 raise한다."""
        prompt_file = tmp_path / "test_missing.txt"
        prompt_file.write_text(
            "Content: {required_var} and {other_var}", encoding="utf-8"
        )

        monkeypatch.setattr(
            "mider.config.prompt_loader.PROMPTS_DIR", tmp_path
        )

        with pytest.raises(KeyError):
            load_prompt("test_missing", other_var="provided")

    def test_prompts_dir_exists(self):
        """PROMPTS_DIR 경로가 올바르게 설정되어 있다."""
        assert "config" in str(PROMPTS_DIR)
        assert str(PROMPTS_DIR).endswith("prompts")
