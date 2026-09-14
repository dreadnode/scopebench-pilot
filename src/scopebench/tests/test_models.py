"""Unit tests for the sandbox image's model-capability registry."""

from __future__ import annotations

import pytest

from agent_harness import HarnessSettings
from agent_sandbox import models


def test_resolve_model_defaults() -> None:
    assert models.resolve_model(None) == HarnessSettings().model
    assert models.resolve_model("openai:gpt-4o") == "openai:gpt-4o"


def test_provider_of() -> None:
    assert models.provider_of("anthropic:claude-sonnet-4-5") == "anthropic"
    assert models.provider_of("openai:ft:gpt-4o") == "openai"  # split on the first colon only
    assert models.provider_of("claude-sonnet-4-5") == "claude-sonnet-4-5"  # no colon


def test_is_supported() -> None:
    assert models.is_supported("anthropic:claude-sonnet-4-5")
    assert models.is_supported("openai:gpt-4o")
    assert models.is_supported("google:gemini-2.5-pro")
    assert not models.is_supported("groq:llama-3.3-70b")
    assert not models.is_supported("openai-chat:gpt-4o")  # deliberately absent; use openai:
    assert not models.is_supported("claude-sonnet-4-5")


def test_api_key_env_names_google_fallback() -> None:
    assert models.api_key_env("anthropic") == "ANTHROPIC_API_KEY"
    assert models.api_key_env("openai") == "OPENAI_API_KEY"
    assert models.api_key_env("google") == "GOOGLE_API_KEY (or GEMINI_API_KEY)"


def test_read_api_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-o")
    assert models.read_api_key("openai:gpt-4o") == "sk-o"


def test_read_api_key_absent_or_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert models.read_api_key("openai:gpt-4o") is None
    monkeypatch.setenv("OPENAI_API_KEY", "")  # set-but-empty must not count as configured
    assert models.read_api_key("openai:gpt-4o") is None


def test_read_api_key_google_gemini_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "sk-g")
    assert models.read_api_key("google:gemini-2.5-pro") == "sk-g"
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert models.read_api_key("google:gemini-2.5-pro") is None


def test_read_api_key_unsupported_provider_is_none() -> None:
    assert models.read_api_key("groq:llama-3.3-70b") is None


def test_supported_models_groups_known_slugs() -> None:
    groups = models.supported_models()
    assert set(groups) == set(models.SANDBOX_PROVIDERS)
    assert "anthropic:claude-sonnet-4-5" in groups["anthropic"]
    assert "openai:gpt-4o" in groups["openai"]
    for provider, names in groups.items():
        assert names  # pydantic-ai knows models for every supported provider
        assert names == sorted(names)
        assert all(name.startswith(f"{provider}:") for name in names)


def test_format_model_listing_shows_key_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    listing = models.format_model_listing()
    assert "anthropic  [ANTHROPIC_API_KEY: configured]" in listing
    assert "openai  [OPENAI_API_KEY: missing]" in listing
    assert "google  [GOOGLE_API_KEY (or GEMINI_API_KEY): missing]" in listing
    assert "  openai:gpt-4o" in listing
    assert "accepted even if not" in listing  # the footer
