"""Tests for the HTML renderer."""

from agent_harness._html import render

_DOC = (
    "<title>My Page</title>"
    "<style>.x{color:red}</style>"
    "<script>var a=1;</script>"
    "<h1>Head</h1>"
    "<p>Para with <strong>bold</strong> and <em>emphasis</em>.</p>"
    "<div>Div<br>break</div>"
    "<ul><li>Item</li></ul>"
    '<a href="http://e.com">link</a>'
    "<a>nolink</a>"
    "<blockquote>quoted</blockquote>"
    "<pre><code>value = 1</code></pre>"
    "<span>span-text</span>"
    "plain &amp; decoded"
)


def test_markdown_structure() -> None:
    title, content = render(_DOC, "markdown")
    assert title == "My Page"
    assert "# Head" in content
    assert "- Item" in content
    assert "[link](http://e.com)" in content
    assert "nolink" in content
    assert "**bold**" in content
    assert "*emphasis*" in content
    assert "> quoted" in content
    assert "```" in content
    assert "plain & decoded" in content
    assert "span-text" in content
    assert "My Page" not in content
    assert "color:red" not in content
    assert "var a=1" not in content


def test_text_strips_markdown() -> None:
    _, content = render(_DOC, "text")
    assert "# Head" not in content
    assert "Head" in content
    assert "[link]" not in content
    assert "link" in content
    assert "- Item" not in content
    assert "Item" in content
    assert "**bold**" not in content
    assert "bold" in content
    assert "My Page" not in content
    assert "color:red" not in content
    assert "var a=1" not in content


def test_html_returns_raw() -> None:
    _, content = render(_DOC, "html")
    assert content == _DOC


def test_malformed_html_and_nested_links() -> None:
    title, content = render(
        "<title>  Nested <b>title</b> </title><p>before<a href='/x'><b>linked</b></a>",
        "markdown",
    )
    assert title == "Nested title"
    assert "before[**linked**](/x)" in content
