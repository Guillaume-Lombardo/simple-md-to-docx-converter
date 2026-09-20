"""AnyDoc inline runs, style delimiters and links; upstream MIT notice: ../ANYDOC_COMPAT_LICENSE.txt."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, cast

import markweave.reversions.hyperlinks as _hyperlinks
from markweave.reversions._anydoc_compat.escaping import (
    _closing_delimiters,
    _escape_text,
    _escape_url_as_text,
    _format_url,
    _render_code_span,
    _render_math_span,
)
from markweave.reversions._anydoc_compat.render_context import (
    _EscapeOptions,
    _NodeRun,
    _RenderContext,
    _Run,
    _TextRun,
)
from markweave.reversions._anydoc_compat.traversal import _inlines_are_empty
from markweave.reversions.errors import ReverseErrorCategory, reject

_is_safe_hyperlink = _hyperlinks._is_safe_hyperlink

_MIN_BRIDGE_RUNS = 2


def _trim_paragraph(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        trimmed = line.lstrip()
        if not _ends_with_hard_break(trimmed):
            trimmed = trimmed.rstrip()
        if not trimmed.rstrip("\\").strip():
            trimmed = ""
        lines.append(trimmed)
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    output = "\n".join(lines)
    if _ends_with_hard_break(output):
        output = output[:-1].rstrip()
    return output


def _ends_with_hard_break(value: str) -> bool:
    return (len(value) - len(value.rstrip("\\"))) % 2 == 1


def _normalize(inlines: Sequence[Any], context: _RenderContext) -> list[_Run]:
    output: list[_Run] = []
    for inline in inlines:
        if inline.kind == "text":
            if not inline.text:
                continue
            style = (
                inline.style.bold,
                inline.style.italic,
                inline.style.strike,
                inline.style.code,
            )
            if not inline.text.strip():
                style = (False, False, False, False)
            if (
                output
                and isinstance(output[-1], _TextRun)
                and output[-1].style == style
            ):
                output[-1] = _TextRun(output[-1].text + inline.text, style)
            elif (
                style != (False, False, False, False)
                and not style[3]
                and len(output) >= _MIN_BRIDGE_RUNS
                and isinstance(output[-1], _TextRun)
                and output[-1].style == (False, False, False, False)
                and not output[-1].text.strip()
                and isinstance(output[-2], _TextRun)
                and output[-2].style == style
            ):
                whitespace = cast("_TextRun", output.pop()).text
                previous = output[-1]
                output[-1] = _TextRun(previous.text + whitespace + inline.text, style)
            else:
                output.append(_TextRun(inline.text, style))
        elif inline.kind == "link" and not inline.target.value:
            if not _inlines_are_empty(inline.content):
                output.extend(_normalize(inline.content, context))
        elif (inline.kind == "anchor" and inline.anchor not in context.html_ids) or (
            inline.kind == "math" and not inline.text.strip()
        ):
            continue
        else:
            output.append(_NodeRun(inline))
    return output


def _render_inlines(  # noqa: PLR0912
    inlines: Sequence[Any],
    inline_context: Literal["block", "heading", "table"],
    context: _RenderContext,
    *,
    in_label: bool = False,
) -> str:
    runs = _normalize(inlines, context)
    suffix: list[frozenset[str]] = [frozenset() for _ in range(len(runs) + 1)]
    for index in range(len(runs) - 1, -1, -1):
        suffix[index] = suffix[index + 1] | _delimiters_of(runs[index], context)
    output = ""
    for index, run in enumerate(runs):
        if isinstance(run, _TextRun):
            following = runs[index + 1] if index + 1 < len(runs) else None
            next_active = _is_active_run(following)
            next_nonspace = _is_nonspace_run(following, inline_context)
            output += _render_text_run(
                run,
                inline_context,
                _EscapeOptions(
                    trailing_active=next_active,
                    trailing_nonspace=next_nonspace,
                    trailing_delims=suffix[index + 1],
                    in_label=in_label,
                ),
                at_line_start=not output or output.endswith("\n"),
            )
            continue
        inline = run.node
        if inline.kind == "note_ref":
            if inline.note_id in context.note_numbers:
                output += f"[^{context.note_numbers[inline.note_id]}]"
        elif inline.kind == "link":
            output += _render_link(inline, inline_context, context)
        elif inline.kind == "image":
            occurrence = context.image_index[0]
            context.image_index[0] += 1
            path = context.image_paths[occurrence]
            if path is not None:
                alt = _escape_text(
                    inline.alt.strip(),
                    inline_context,
                    _EscapeOptions(in_label=True),
                )
                output += f"![{alt}]({path.as_posix()})"
                context.retained_occurrences.append(occurrence)
            elif inline.alt.strip():
                output += _escape_text(
                    inline.alt.strip(),
                    inline_context,
                    _EscapeOptions(in_label=in_label),
                )
                context.retained_occurrences.append(occurrence)
        elif inline.kind == "anchor":
            output += f'<a id="{context.html_ids[inline.anchor]}"></a>'
        elif inline.kind == "line_break":
            output += {"block": "\\\n", "heading": " ", "table": "\n"}[inline_context]
        elif inline.kind == "math":
            output += _render_math_span(inline.text, inline_context)
        elif inline.kind == "checkbox":
            output += "[x]" if inline.checked else "[ ]"
            if index + 1 < len(runs) and not _starts_with_space(runs[index + 1]):
                output += " "
    return output


def _is_active_run(run: _Run | None) -> bool:
    if isinstance(run, _TextRun):
        return run.style != (False, False, False, False)
    return isinstance(run, _NodeRun) and run.node.kind in {
        "link",
        "image",
        "note_ref",
        "math",
    }


def _is_nonspace_run(
    run: _Run | None, inline_context: Literal["block", "heading", "table"]
) -> bool:
    return isinstance(run, _NodeRun) and (
        run.node.kind in {"anchor", "checkbox"}
        or (run.node.kind == "line_break" and inline_context != "heading")
    )


def _starts_with_space(run: _Run) -> bool:
    return (isinstance(run, _TextRun) and bool(run.text) and run.text[0].isspace()) or (
        isinstance(run, _NodeRun) and run.node.kind == "line_break"
    )


def _render_link(
    inline: Any,
    inline_context: Literal["block", "heading", "table"],
    context: _RenderContext,
) -> str:
    target = inline.target
    if target.kind == "anchor":
        fragment = context.fragments.get(target.value)
        if fragment is None:
            return _render_inlines(inline.content, inline_context, context)
        url = f"#{fragment}"
    else:
        if not _is_safe_hyperlink(target.value):
            reject(ReverseErrorCategory.MALFORMED)
        url = target.value
    label = _render_inlines(inline.content, inline_context, context, in_label=True)
    if not label.strip():
        if target.kind == "anchor":
            return ""
        label = _escape_url_as_text(url, inline_context)
    return f"[{label}]({_format_url(url)})"


def _render_text_run(
    run: _TextRun,
    inline_context: Literal["block", "heading", "table"],
    options: _EscapeOptions,
    *,
    at_line_start: bool,
) -> str:
    plain = (False, False, False, False)
    if run.style == plain:
        return _escape_text(
            run.text,
            inline_context,
            _EscapeOptions(
                at_line_start=at_line_start,
                trailing_active=options.trailing_active,
                trailing_nonspace=options.trailing_nonspace,
                trailing_delims=options.trailing_delims,
                in_label=options.in_label,
            ),
        )
    lead_count = len(run.text) - len(run.text.lstrip())
    trail_index = len(run.text.rstrip())
    lead, core, trail = (
        run.text[:lead_count],
        run.text[lead_count:trail_index],
        run.text[trail_index:],
    )
    if not core:
        return lead + trail
    bold, italic, strike, code = run.style
    if code:
        rendered = _render_code_span(core, inline_context)
    else:
        opening = (
            ("~~" if strike else "") + ("**" if bold else "") + ("*" if italic else "")
        )
        rendered = (
            opening
            + _escape_text(
                core,
                inline_context,
                _EscapeOptions(styled=True, in_label=options.in_label),
            )
            + opening[::-1]
        )
    return lead + rendered + trail


def _delimiters_of(  # noqa: PLR0911, PLR0912
    run: _Run, context: _RenderContext
) -> frozenset[str]:
    if isinstance(run, _TextRun):
        bold, italic, strike, code = run.style
        if code:
            return frozenset("`")
        if run.style == (False, False, False, False):
            return _closing_delimiters(run.text)
        delimiters: set[str] = set()
        if bold or italic:
            delimiters.add("*")
        if strike:
            delimiters.add("~")
        if "`" in run.text:
            delimiters.add("`")
        if "]" in run.text:
            delimiters.add("]")
        delimiters |= _closing_delimiters(
            "".join(char if char == "$" or char.isspace() else "x" for char in run.text)
        )
        return frozenset(delimiters)
    inline = run.node
    if inline.kind == "link":
        if (
            inline.target.kind == "anchor"
            and inline.target.value not in context.fragments
        ):
            result: set[str] = set()
            for child in _normalize(inline.content, context):
                result |= _delimiters_of(child, context)
            return frozenset(result)
        if _emits_backtick(inline.content) or "`" in inline.target.value:
            return frozenset("`")
    elif inline.kind == "image":
        if inline.source.kind == "external":
            return frozenset("`" if "`" in inline.alt else "")
        return _closing_delimiters(inline.alt)
    return frozenset()


def _emits_backtick(inlines: Sequence[Any]) -> bool:
    for inline in inlines:
        if inline.kind == "text" and (
            "`" in inline.text or (inline.style.code and bool(inline.text.strip()))
        ):
            return True
        if inline.kind == "link" and (
            _emits_backtick(inline.content) or "`" in inline.target.value
        ):
            return True
        if inline.kind == "image" and "`" in inline.alt:
            return True
    return False
