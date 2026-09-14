"""Tests for the web_extract tool."""

import sys

import httpx
import pytest
from pytest_httpx import HTTPXMock

from agent_harness.tools.web_extract import web_extract


async def test_extracts_multiple(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://a.com/", text="<title>A</title>alpha", status_code=200)
    httpx_mock.add_response(url="https://b.com/", text="bravo", status_code=200)
    result = await web_extract(["https://a.com/", "https://b.com/"])
    assert result["success"]
    assert not result["partial"]
    assert result["extracted_count"] == 2
    assert result["requested_count"] == 2
    assert len(result["results"]) == 2


async def test_dedupes(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://a.com/", text="alpha", status_code=200)
    result = await web_extract(["https://a.com/", "https://a.com/"])
    assert result["requested_count"] == 2
    assert result["extracted_count"] == 1
    assert len(result["results"]) == 1


async def test_partial_on_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://ok.com/", text="ok", status_code=200)
    httpx_mock.add_exception(httpx.ConnectError("down"), url="https://bad.com/")
    result = await web_extract(["https://ok.com/", "https://bad.com/"])
    assert result["partial"]
    assert not result["success"]
    assert result["extracted_count"] == 1
    assert any(record["error"] for record in result["results"])


async def test_all_fail(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("x"), url="https://bad.com/")
    result = await web_extract(["https://bad.com/"])
    assert not result["success"]
    assert not result["partial"]
    assert result["extracted_count"] == 0


async def test_non_success_status(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://nf.com/", status_code=404, text="nope")
    result = await web_extract(["https://nf.com/"])
    assert not result["success"]
    assert result["results"][0]["status_code"] == 404
    assert result["results"][0]["error"] is None


async def test_size_limit(httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch) -> None:
    # The cap is enforced inside _web during streaming, so patch it there.
    monkeypatch.setattr(sys.modules["agent_harness._web"], "MAX_RESPONSE_BYTES", 10)
    httpx_mock.add_response(url="https://big.com/", content=b"x" * 11, status_code=200)
    result = await web_extract(["https://big.com/"])
    assert result["results"][0]["error"] == "Response exceeds the size limit."
    assert not result["success"]


async def test_caps_urls(httpx_mock: HTTPXMock) -> None:
    for i in range(5):
        httpx_mock.add_response(url=f"https://s{i}.com/", text="x", status_code=200)
    urls = [f"https://s{i}.com/" for i in range(6)]
    result = await web_extract(urls)
    assert result["warnings"]
    assert len(result["results"]) == 5


async def test_empty_urls() -> None:
    result = await web_extract([])
    assert not result["success"]
    assert result["extracted_count"] == 0
    assert result["results"] == []
