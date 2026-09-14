"""Tests for explicit and environment-driven harness configuration."""

import pytest

from agent_harness import HarnessSettings


def test_settings_defaults_are_complete() -> None:
    settings = HarnessSettings()
    assert settings.model == "anthropic:claude-sonnet-4-5"
    assert "read a file before modifying it" in settings.system_prompt
    assert settings.retries == 2


def test_settings_load_environment_overrides() -> None:
    settings = HarnessSettings.from_env(
        {
            "HARNESS_MODEL": "openai:gpt-5",
            "HARNESS_SYSTEM_PROMPT": "Custom policy",
            "HARNESS_RETRIES": "7",
        }
    )
    assert settings == HarnessSettings(
        model="openai:gpt-5", system_prompt="Custom policy", retries=7
    )


def test_settings_load_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_MODEL", "google:gemini-2.5-pro")
    assert HarnessSettings.from_env().model == "google:gemini-2.5-pro"


def test_settings_reject_empty_model() -> None:
    with pytest.raises(ValueError, match="model must not be empty"):
        _ = HarnessSettings(model=" ")


def test_settings_reject_negative_retries() -> None:
    with pytest.raises(ValueError, match="retries must be non-negative"):
        _ = HarnessSettings(retries=-1)


def test_settings_reject_non_integer_environment_retries() -> None:
    with pytest.raises(ValueError, match="HARNESS_RETRIES must be an integer"):
        _ = HarnessSettings.from_env({"HARNESS_RETRIES": "many"})
