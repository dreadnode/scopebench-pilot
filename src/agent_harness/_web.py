"""Shared HTTP fetch + HTML rendering helper for the web tools.

Responses are streamed and the byte cap is enforced *during* download, so an
oversized body is abandoned instead of being pulled fully into memory first. A
single :class:`httpx.AsyncClient` can be shared across the concurrent fetches of
``web_extract`` via :func:`make_client`.
"""

from dataclasses import dataclass
from typing import cast

import httpx

from agent_harness._html import render
from agent_harness.deps import WebFormat

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_CONTENT_CHARS = 50_000
MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 30


class ResponseTooLargeError(Exception):
    """Raised when a response body exceeds :data:`MAX_RESPONSE_BYTES`."""


@dataclass
class Page:
    """The parts of a fetched response the web tools need."""

    is_success: bool
    final_url: str
    status_code: int
    content_type: str
    text: str


def make_client(timeout: float) -> httpx.AsyncClient:
    """Build an ``AsyncClient`` that follows redirects with the shared limits."""
    return httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        timeout=timeout,
    )


async def fetch_page(client: httpx.AsyncClient, url: str, headers: dict[str, str] | None) -> Page:
    """Fetch ``url`` with ``client``, enforcing the byte cap while streaming.

    Raises:
        httpx.HTTPError: On any transport/protocol failure.
        ResponseTooLargeError: If the body exceeds :data:`MAX_RESPONSE_BYTES`.
    """
    request = client.build_request("GET", url, headers=headers)
    response = await client.send(request, stream=True)
    try:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > MAX_RESPONSE_BYTES:
                msg = f"Response from {url} exceeds the {MAX_RESPONSE_BYTES}-byte limit."
                raise ResponseTooLargeError(msg)
    finally:
        await response.aclose()
    text = bytes(body).decode(response.encoding or "utf-8", errors="replace")
    return Page(
        is_success=response.is_success,
        final_url=str(response.url),
        status_code=response.status_code,
        content_type=cast("str", response.headers.get("content-type", "")),
        text=text,
    )


def render_body(text: str, fmt: WebFormat) -> tuple[str, str, bool]:
    """Render a response body, truncating to the content cap.

    Returns a tuple of ``(title, content, truncated)``.
    """
    title, content = render(text, fmt)
    truncated = len(content) > MAX_CONTENT_CHARS
    if truncated:
        content = content[:MAX_CONTENT_CHARS]
    return title, content, truncated
