"""Tests for the confirm tool."""

from agent_harness.tools.confirm import confirm


def test_returns_approval_message() -> None:
    assert confirm("delete the database") == "Approved: delete the database"
