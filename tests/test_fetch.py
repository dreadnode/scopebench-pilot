"""Tests for the fetch tool."""

import sys

import httpx
import pytest
from pydantic_ai import ModelRetry
from pytest_httpx import HTTPXMock

from agent_harness.tools.fetch import fetch


async def test_fetch_markdown(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://ex.com/",
        text="<title>T</title><h1>Hi</h1>",
        status_code=200,
        headers={"content-type": "text/html"},
    )
    result = await fetch("https://ex.com/")
    assert result["success"]
    assert result["status_code"] == 200
    assert result["title"] == "T"
    assert "# Hi" in result["content"]
    assert not result["truncated"]
    assert result["content_type"] == "text/html"


async def test_fetch_non_success_status(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://ex.com/404", status_code=404, text="nope")
    result = await fetch("https://ex.com/404")
    assert not result["success"]
    assert result["status_code"] == 404


async def test_fetch_truncates(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://ex.com/big", text="y" * 60000, status_code=200)
    result = await fetch("https://ex.com/big")
    assert result["truncated"]
    assert result["content_length"] == 50000


async def test_fetch_http_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("boom"), url="https://ex.com/err")
    with pytest.raises(ModelRetry):
        await fetch("https://ex.com/err")


async def test_fetch_too_large(httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch) -> None:
    # The cap is enforced inside _web during streaming, so patch it there.
    monkeypatch.setattr(sys.modules["agent_harness._web"], "MAX_RESPONSE_BYTES", 10)
    httpx_mock.add_response(url="https://ex.com/huge", content=b"x" * 11, status_code=200)
    with pytest.raises(ModelRetry):
        await fetch("https://ex.com/huge")
