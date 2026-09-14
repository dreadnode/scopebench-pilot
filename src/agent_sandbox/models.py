"""Model capabilities and credential discovery for the bundled sandbox image.

The image can only run providers whose SDK is present in its locked dependency closure. Keeping
that registry beside the image makes support checks reusable by any caller and prevents benchmark
applications from independently guessing what the sandbox can execute.
"""

from __future__ import annotations

import os

from pydantic_ai.models import known_model_names

from agent_harness import HarnessSettings

# Provider prefix -> primary environment variable used for its API key.
SANDBOX_PROVIDERS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}

_GEMINI_FALLBACK_ENV = "GEMINI_API_KEY"


def resolve_model(model: str | None) -> str:
    """Return the requested model or the bundled harness's default model."""
    return model or HarnessSettings().model


def provider_of(model: str) -> str:
    """Return the provider prefix of a ``provider:model`` string."""
    return model.split(":", 1)[0]


def is_supported(model: str) -> bool:
    """Whether the sandbox image contains the SDK needed for ``model``."""
    return provider_of(model) in SANDBOX_PROVIDERS


def api_key_env(provider: str) -> str:
    """Return the environment-variable name(s) accepted for ``provider`` credentials."""
    env = SANDBOX_PROVIDERS[provider]
    if provider == "google":
        return f"{env} (or {_GEMINI_FALLBACK_ENV})"
    return env


def _provider_key(provider: str) -> str | None:
    """Return a configured key, or ``None`` for absent, empty, or unsupported providers."""
    env = SANDBOX_PROVIDERS.get(provider)
    if env is None:
        return None
    key = os.environ.get(env, "")
    if not key and provider == "google":
        key = os.environ.get(_GEMINI_FALLBACK_ENV, "")
    return key or None


def read_api_key(model: str) -> str | None:
    """Return the configured API key for ``model``'s provider, if any."""
    return _provider_key(provider_of(model))


def supported_models() -> dict[str, list[str]]:
    """Return known model slugs grouped by sandbox-supported provider."""
    groups: dict[str, list[str]] = {provider: [] for provider in SANDBOX_PROVIDERS}
    for name in known_model_names():
        if ":" in name and (group := groups.get(provider_of(name))) is not None:
            group.append(name)
    return {provider: sorted(names) for provider, names in groups.items()}


def format_model_listing() -> str:
    """Render known model slugs and credential status for a command-line listing."""
    lines: list[str] = []
    for provider, names in supported_models().items():
        status = "configured" if _provider_key(provider) else "missing"
        lines.append(f"{provider}  [{api_key_env(provider)}: {status}]")
        lines.extend(f"  {name}" for name in names)
        lines.append("")
    lines.append(
        "Any 'provider:model' string for a provider above is accepted even if not\n"
        + "listed (the registry lags new releases). Other providers need their SDK\n"
        + "added to the sandbox image first.",
    )
    return "\n".join(lines)
