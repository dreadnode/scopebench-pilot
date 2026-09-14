"""Tests for the ask_user tool."""

import pytest
from pydantic_ai import CallDeferred

from agent_harness.tools.ask_user import ask_user


def test_defers_with_metadata() -> None:
    with pytest.raises(CallDeferred) as exc:
        ask_user("What is your name?", options=["Ada", "Alan"])
    assert exc.value.metadata == {"question": "What is your name?", "options": ["Ada", "Alan"]}


def test_defers_without_options() -> None:
    with pytest.raises(CallDeferred) as exc:
        ask_user("Proceed?")
    assert exc.value.metadata == {"question": "Proceed?", "options": None}
