"""The ``web_extract`` tool: extract content from several URLs at once."""

import asyncio

import httpx

from agent_harness._web import (
    DEFAULT_TIMEOUT,
    ResponseTooLargeError,
    fetch_page,
    make_client,
    render_body,
)
from agent_harness.deps import PageRecord, WebExtractResponse, WebFormat

_MAX_URLS = 5
_TOO_LARGE = "Response exceeds the size limit."


async def web_extract(
    urls: list[str],
    format: WebFormat = "markdown",
    timeout: int = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
) -> WebExtractResponse:
    """Extract content from multiple public URLs (up to 5 unique per call).

    Duplicate URLs are removed. Fetches run concurrently and individual failures
    are recorded per page rather than aborting the whole call. Read-only.

    Args:
        urls: The URLs to extract.
        format: One of ``"markdown"``, ``"text"``, or ``"html"``.
        timeout: Per-request timeout in seconds.
        headers: Optional extra request headers.

    Returns:
        A structured response with one record per URL.
    """
    unique = list(dict.fromkeys(urls))
    warnings: list[str] = []
    if len(unique) > _MAX_URLS:
        warnings.append(f"Only the first {_MAX_URLS} of {len(unique)} unique URLs were processed.")
        unique = unique[:_MAX_URLS]
    async with make_client(timeout) as client:
        results = list(
            await asyncio.gather(*(_extract_one(client, url, format, headers) for url in unique)),
        )
    extracted = sum(1 for record in results if record["success"])
    return WebExtractResponse(
        success=len(unique) > 0 and extracted == len(unique),
        partial=0 < extracted < len(unique),
        requested_count=len(urls),
        extracted_count=extracted,
        warnings=warnings,
        results=results,
    )


async def _extract_one(
    client: httpx.AsyncClient,
    url: str,
    fmt: WebFormat,
    headers: dict[str, str] | None,
) -> PageRecord:
    try:
        page = await fetch_page(client, url, headers)
    except ResponseTooLargeError:
        return _error_record(url, _TOO_LARGE)
    except httpx.HTTPError as exc:
        return _error_record(url, str(exc))
    title, content, truncated = render_body(page.text, fmt)
    return PageRecord(
        success=page.is_success,
        url=url,
        final_url=page.final_url,
        status_code=page.status_code,
        content_type=page.content_type,
        title=title,
        content=content,
        content_length=len(content),
        truncated=truncated,
        error=None,
    )


def _error_record(url: str, error: str) -> PageRecord:
    return PageRecord(
        success=False,
        url=url,
        final_url=url,
        status_code=0,
        content_type="",
        title="",
        content="",
        content_length=0,
        truncated=False,
        error=error,
    )
