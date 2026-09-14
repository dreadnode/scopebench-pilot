"""The ``fetch`` tool: fetch a URL and return it in the requested format."""

import httpx
from pydantic_ai import ModelRetry

from agent_harness._web import (
    DEFAULT_TIMEOUT,
    ResponseTooLargeError,
    fetch_page,
    make_client,
    render_body,
)
from agent_harness.deps import FetchResponse, WebFormat


async def fetch(
    url: str,
    format: WebFormat = "markdown",
    timeout: int = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
) -> FetchResponse:
    """Fetch content from a URL and return it in the requested format.

    HTML is converted to markdown by default. Follows redirects and rejects
    responses larger than 5 MB; content is capped at ~50,000 characters. Read-only.

    Args:
        url: The URL to fetch.
        format: One of ``"markdown"``, ``"text"``, or ``"html"``.
        timeout: Request timeout in seconds.
        headers: Optional extra request headers.

    Returns:
        A structured response record with the fetched content and metadata.

    Raises:
        ModelRetry: If the request fails or the response is too large.
    """
    try:
        async with make_client(timeout) as client:
            page = await fetch_page(client, url, headers)
    except ResponseTooLargeError as exc:
        raise ModelRetry(str(exc)) from exc
    except httpx.HTTPError as exc:
        msg = f"Failed to fetch {url}: {exc}"
        raise ModelRetry(msg) from exc
    title, content, truncated = render_body(page.text, format)
    return FetchResponse(
        success=page.is_success,
        url=url,
        final_url=page.final_url,
        status_code=page.status_code,
        content_type=page.content_type,
        format=format,
        title=title,
        content_length=len(content),
        truncated=truncated,
        content=content,
    )
