"""Tests for the fuzzy edit helper."""

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from agent_harness._fuzzy import EditError, apply_edit


def test_exact_single() -> None:
    out = apply_edit("hello world", "world", "there", replace_all=False)
    assert out.text == "hello there"
    assert out.count == 1
    assert out.fuzzy is False
    assert out.changed_lines == (1, 1)


def test_exact_multiple_replace_all() -> None:
    out = apply_edit("a a a", "a", "b", replace_all=True)
    assert out.text == "b b b"
    assert out.count == 3


def test_ambiguous_names_the_match_lines() -> None:
    with pytest.raises(EditError) as exc:
        apply_edit("x\ny\nx\n", "x", "b", replace_all=False)
    msg = str(exc.value)
    assert "Found 2 matches of the string to replace, but replace_all is false." in msg
    assert "Matches found at lines: 1, 3." in msg
    assert msg.endswith("String: x")


def test_ambiguous_lines_deduped() -> None:
    with pytest.raises(EditError, match=r"Matches found at lines: 1\."):
        apply_edit("aa\n", "a", "b", replace_all=False)


def test_ambiguous_lines_capped() -> None:
    with pytest.raises(EditError, match=r"lines: 1, 2, 3, 4, 5 \(\+2 more\)\."):
        apply_edit("\n".join(["m"] * 7), "m", "n", replace_all=False)


def test_not_found_message() -> None:
    with pytest.raises(EditError) as exc:
        apply_edit("abc", "xyz", "q", replace_all=False)
    assert str(exc.value) == "String to replace not found in file.\nString: xyz"


def test_not_found_hints_at_read_prefixes() -> None:
    with pytest.raises(EditError, match="line-number prefixes"):
        apply_edit("foo\nbar\n", "12\tfoo\n13\tbar", "x", replace_all=False)


def test_prefix_hint_requires_every_line() -> None:
    with pytest.raises(EditError) as exc:
        apply_edit("foo\nbar\n", "12\tfoo\nbar-nope", "x", replace_all=False)
    assert "line-number prefixes" not in str(exc.value)


def test_prefix_hint_negative() -> None:
    with pytest.raises(EditError) as exc:
        apply_edit("foo\n", "x\t12", "y", replace_all=False)
    assert "line-number prefixes" not in str(exc.value)


def test_noop_rejected() -> None:
    with pytest.raises(EditError, match="No changes to make"):
        apply_edit("abc", "b", "b", replace_all=False)


def test_noop_beats_fuzzy_reindent() -> None:
    # old == new but indented differently from the file: a fuzzy match would
    # silently rewrite whitespace, so the equality check must come first.
    with pytest.raises(EditError, match="No changes to make"):
        apply_edit("    do()\n", "  do()", "  do()", replace_all=False)


def test_changed_lines_single_line() -> None:
    out = apply_edit("a\nb\nc\n", "b", "B", replace_all=False)
    assert out.changed_lines == (2, 2)


def test_changed_lines_multiline_replacement() -> None:
    out = apply_edit("a\nb\nc\n", "b", "x\ny\nz", replace_all=False)
    assert out.text == "a\nx\ny\nz\nc\n"
    assert out.changed_lines == (2, 4)


def test_changed_lines_replace_all_uses_first_match() -> None:
    out = apply_edit("m\nx\nm\n", "m", "n", replace_all=True)
    assert out.count == 2
    assert out.changed_lines == (1, 1)


def test_line_trimmed_fallback_reindents_to_source() -> None:
    content = "if x:\n    do()\n    more()\n"
    out = apply_edit(content, "do()\nmore()", "done()", replace_all=False)
    assert out.count == 1
    assert out.fuzzy is True
    assert "    done()" in out.text  # reindented to the matched block, not spliced flush-left
    assert "do()" not in out.text


def test_reindent_adds_indent_across_lines() -> None:
    content = "def f():\n    a()\n    b()\n"
    out = apply_edit(content, "a()\nb()", "a()\n\nc()", replace_all=False)
    assert "    a()" in out.text
    assert "\n\n" in out.text  # blank line stays blank, not indented
    assert "    c()" in out.text


def test_reindent_removes_indent() -> None:
    out = apply_edit("  do()\n", "    do()", "    done()", replace_all=False)
    assert out.text == "  done()"


def test_reindent_equal_ws_is_verbatim() -> None:
    # Exact fails (trailing space on source), but leading indentation matches.
    out = apply_edit("foo \nbar\n", "foo\nbar", "X", replace_all=False)
    assert out.text == "X"


def test_reindent_incompatible_ws_is_verbatim() -> None:
    # Tab-vs-space indentation cannot be reconciled, so new is spliced as-is.
    out = apply_edit("\tfoo\n\tbar\n", "  foo\n  bar", "Z", replace_all=False)
    assert out.text == "Z"


def test_whitespace_only_needle_not_found() -> None:
    with pytest.raises(EditError, match="not found"):
        apply_edit("abc", "   ", "x", replace_all=False)


def test_empty_old_string_raises() -> None:
    # Guarded explicitly: an empty needle would otherwise match at every offset.
    with pytest.raises(EditError, match="empty"):
        apply_edit("abc", "", "x", replace_all=False)


# ---------------------------------------------------------------- properties

# One "line" of content: no character str.splitlines() breaks on (U+2028/U+2029
# spelled via chr() to keep the source ASCII).
_SPLITLINES_CHARS = "\r\n\x0b\x0c\x1c\x1d\x1e\x85" + chr(0x2028) + chr(0x2029)
_BODY_LINE = st.text(alphabet=st.characters(exclude_characters=_SPLITLINES_CHARS))
# Strip-stable and non-empty: starts and ends with non-whitespace.
_FLUSH_LINE = _BODY_LINE.map(str.strip).filter(bool)

# No CR, so no "\r\n" can form: apply_edit then takes its LF fast path and is exactly
# str.replace. (CRLF content is normalized/restored, which these str.replace invariants
# deliberately don't model — see the dedicated CRLF tests below.)
_NO_CR = st.characters(exclude_characters="\r")


@given(
    chunks=st.lists(st.text(_NO_CR), min_size=2),
    old=st.text(_NO_CR, min_size=1),
    new=st.text(_NO_CR),
)
def test_exact_replace_all_equals_str_replace(chunks: list[str], old: str, new: str) -> None:
    content = old.join(chunks)  # guarantees at least one exact occurrence
    if old == new:
        with pytest.raises(EditError, match="No changes to make"):
            _ = apply_edit(content, old, new, replace_all=True)
        return
    out = apply_edit(content, old, new, replace_all=True)
    assert out.text == content.replace(old, new)
    assert out.count == content.count(old)  # same non-overlapping semantics


@given(
    chunks=st.lists(st.text(_NO_CR), min_size=2),
    old=st.text(_NO_CR, min_size=1),
    new=st.text(_NO_CR),
)
def test_single_replace_requires_unique_match(chunks: list[str], old: str, new: str) -> None:
    content = old.join(chunks)
    if old == new:
        with pytest.raises(EditError, match="No changes to make"):
            _ = apply_edit(content, old, new, replace_all=False)
        return
    if content.count(old) == 1:
        out = apply_edit(content, old, new, replace_all=False)
        assert out.text == content.replace(old, new)
        assert out.count == 1
        assert out.fuzzy is False
        assert 1 <= out.changed_lines[0] <= out.changed_lines[1]
    else:
        with pytest.raises(EditError, match=r"Found \d+ matches"):
            apply_edit(content, old, new, replace_all=False)


# --------------------------------------------------------------- CRLF preservation


def test_crlf_single_line_preserved() -> None:
    # Editing one line leaves every other line's CRLF intact (no whole-file LF rewrite).
    out = apply_edit("a\r\nb\r\nc\r\n", "b", "B", replace_all=False)
    assert out.text == "a\r\nB\r\nc\r\n"
    assert out.count == 1
    assert out.changed_lines == (2, 2)


def test_crlf_multiline_lf_old_matches_crlf_content() -> None:
    # The model's old/new use LF (what `read` shows); the file on disk is CRLF.
    out = apply_edit("x\r\ny\r\nz\r\n", "x\ny", "X\nY", replace_all=False)
    assert out.text == "X\r\nY\r\nz\r\n"
    assert out.count == 1


def test_crlf_reindent_preserved() -> None:
    # Mirrors test_line_trimmed_fallback_reindents_to_source but on CRLF content: the untouched
    # `if x:` line keeps its CRLF, and `new` is reindented to the matched block.
    content = "if x:\r\n    do()\r\n    more()\r\n"
    out = apply_edit(content, "do()\nmore()", "done()", replace_all=False)
    assert out.count == 1
    assert out.text == "if x:\r\n    done()"


@given(
    old_lines=st.lists(_FLUSH_LINE, min_size=2),
    new_lines=st.lists(_BODY_LINE, min_size=1),
    indent=st.text(alphabet=" \t", min_size=1),
)
def test_trimmed_match_rebases_indentation(
    old_lines: list[str], new_lines: list[str], indent: str
) -> None:
    # The needle is the block without its indentation: exact matching cannot
    # succeed (every junction pairs a non-blank flush line against an indented
    # one), so the line-trimmed fallback must find it and re-indent `new`.
    assume("\n".join(old_lines) != "\n".join(new_lines))
    content = "\n".join(indent + line for line in old_lines)
    out = apply_edit(content, "\n".join(old_lines), "\n".join(new_lines), replace_all=False)
    assert out.count == 1
    assert out.fuzzy is True
    assert out.text == "\n".join(indent + line if line.strip() else line for line in new_lines)
