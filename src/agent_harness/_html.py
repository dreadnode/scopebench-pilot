"""HTML rendering policy for the web tools."""

import re
from html import escape

from html_to_markdown import ConversionOptions, PreprocessingOptions, convert

from agent_harness.deps import WebFormat

_NO_PREPROCESSING = PreprocessingOptions(enabled=False)
_MARKDOWN_OPTIONS = ConversionOptions(
    bullets="-",
    extract_metadata=True,
    heading_style="atx",
    output_format="markdown",
    preprocessing=_NO_PREPROCESSING,
    strip_tags=["script", "style"],
)
_TEXT_OPTIONS = ConversionOptions(
    extract_metadata=True,
    output_format="plain",
    preprocessing=_NO_PREPROCESSING,
    strip_tags=["script", "style"],
)
_FRAGMENT_TITLE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)
_DOCUMENT_CONTAINER = re.compile(r"<(?:html|head)\b", re.IGNORECASE)
_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n+", re.DOTALL)
_LIST_MARKER = re.compile(r"^(?:[-+*]|\d+[.)])\s+", re.MULTILINE)


def _normalize_titled_fragment(html: str) -> str:
    """Give a fragment's title document context so the converter exposes its metadata."""
    match = _FRAGMENT_TITLE.search(html)
    if match is None or _DOCUMENT_CONTAINER.search(html) is not None:
        return html
    title = " ".join((convert(match.group(1), _TEXT_OPTIONS).content or "").split())
    body = html[: match.start()] + html[match.end() :]
    return f"<html><head><title>{escape(title)}</title></head><body>{body}</body></html>"


def render(html: str, fmt: WebFormat) -> tuple[str, str]:
    """Return the document title and content rendered in the requested format."""
    options = _MARKDOWN_OPTIONS if fmt == "markdown" else _TEXT_OPTIONS
    result = convert(_normalize_titled_fragment(html), options)
    title = result.metadata.document.title or ""
    content = html if fmt == "html" else (result.content or "")
    if fmt == "markdown":
        content = _FRONT_MATTER.sub("", content)
    elif fmt == "text":
        content = _LIST_MARKER.sub("", content)
    return title, content
