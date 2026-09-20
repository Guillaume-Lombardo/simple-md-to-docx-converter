"""AnyDoc Markdown escaping, URLs, code and math spans; upstream MIT notice: ../ANYDOC_COMPAT_LICENSE.txt."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from typing import Literal

from markweave.reversions._anydoc_compat.render_context import _EscapeOptions


def _closing_delimiters(text: str) -> frozenset[str]:
    chars = list(text)
    output: set[str] = set()
    index = 0
    while index < len(chars):
        end = index + 1
        while end < len(chars) and chars[end] == chars[index]:
            end += 1
        char = chars[index]
        if (
            char in "`]"
            or (char == "$" and _can_close_math(chars, index, end))
            or (char in "*_~" and _can_close(chars, index, end))
        ):
            output.add(char)
        index = end
    return frozenset(output)


def _can_close_math(chars: Sequence[str], start: int, end: int) -> bool:
    previous = chars[start - 1] if start else None
    following = chars[end] if end < len(chars) else None
    return not (previous is not None and previous.isspace()) and not (
        following is not None and following.isascii() and following.isdigit()
    )


def _can_close(chars: Sequence[str], start: int, end: int) -> bool:
    previous = chars[start - 1] if start else None
    following = chars[end] if end < len(chars) else None
    if previous is not None and previous.isspace():
        return False
    if (
        previous is not None
        and previous.isascii()
        and not previous.isalnum()
        and following is not None
        and following.isalnum()
    ):
        return False
    return chars[start] != "_" or not (
        previous is not None
        and previous.isalnum()
        and following is not None
        and following.isalnum()
    )


def _escape_text(  # noqa: PLR0912, PLR0915
    text: str,
    inline_context: Literal["block", "heading", "table"],
    options: _EscapeOptions,
) -> str:
    chars = list(text)
    last: dict[str, int] = {}
    index = 0
    while index < len(chars):
        end = index + 1
        while end < len(chars) and chars[end] == chars[index]:
            end += 1
        char = chars[index]
        if (
            char in "`]"
            or (char == "$" and _can_close_math(chars, index, end))
            or (char in "*_~" and _can_close(chars, index, end))
        ):
            last[char] = end - 1
        index = end
    output: list[str] = []
    line_has_content = not (options.at_line_start and inline_context == "block")
    index = 0
    while index < len(chars):
        char = chars[index]
        if char == "\n":
            output.append(char)
            if inline_context == "block":
                line_has_content = False
            index += 1
            continue
        start_of_line = not line_has_content
        if not char.isspace():
            line_has_content = True
        following = chars[index + 1] if index + 1 < len(chars) else None
        next_nonspace = (
            options.trailing_active or options.trailing_nonspace
            if following is None
            else not following.isspace()
        )
        paired = (
            options.trailing_active
            or char in options.trailing_delims
            or last.get(char, -1) > index
        )
        escape = False
        if char == "\\":
            escape = True
        elif char == "$":
            escape = next_nonspace and paired
        elif char == "]" and options.in_label:
            escape = True
        elif char == "`":
            escape = options.styled or paired
        elif char == "*":
            escape = options.styled or start_of_line or (next_nonspace and paired)
        elif char == "_":
            previous_alnum = index > 0 and chars[index - 1].isalnum()
            following_alnum = following is not None and following.isalnum()
            escape = options.styled or (
                next_nonspace and not (previous_alnum and following_alnum) and paired
            )
        elif char == "~":
            escape = options.styled or (next_nonspace and paired)
        elif char == "[":
            escape = (
                options.in_label
                or "]" in options.trailing_delims
                or last.get("]", -1) > index
            )
        elif char == "<":
            escape = following is not None and (
                (following.isascii() and following.isalpha()) or following in "/!?"
            )
        elif char == "!":
            escape = following is None and options.trailing_active
        elif char == "|" and inline_context == "table":
            escape = True
        elif char == "&" and _entity_ahead(chars[index:]):
            output.append("&amp;")
            index += 1
            continue
        elif char == "#" and start_of_line:
            cursor = index
            while cursor < len(chars) and chars[cursor] == "#":
                cursor += 1
            escape = cursor == len(chars) or chars[cursor].isspace()
        elif char == "-" and start_of_line:
            escape = not next_nonspace or _line_is_only(chars[index:], "-")
        elif char == "+" and start_of_line:
            escape = not next_nonspace
        elif char == ">" and start_of_line:
            escape = True
        elif char == "=" and start_of_line:
            escape = _line_is_only(chars[index:], "=")
        elif char.isascii() and char.isdigit() and start_of_line:
            cursor = index
            while (
                cursor < len(chars)
                and chars[cursor].isascii()
                and chars[cursor].isdigit()
            ):
                cursor += 1
            if (
                cursor < len(chars)
                and chars[cursor] in ".)"
                and (cursor + 1 == len(chars) or chars[cursor + 1].isspace())
            ):
                output.extend(chars[index:cursor])
                output.extend(("\\", chars[cursor]))
                index = cursor + 1
                continue
        if escape:
            output.append("\\")
        output.append(char)
        index += 1
    return "".join(output)


def _line_is_only(chars: Sequence[str], char: str) -> bool:
    return all(
        value == char or value in " \t"
        for value in list(chars)[
            : next((i for i, value in enumerate(chars) if value == "\n"), len(chars))
        ]
    )


def _entity_ahead(chars: Sequence[str]) -> bool:
    if len(chars) > 1 and chars[1] == "#":
        return True
    index = 1
    while index < len(chars) and chars[index].isascii() and chars[index].isalnum():
        index += 1
    return index > 1 and index < len(chars) and chars[index] == ";"


def _format_url(url: str) -> str:
    output: list[str] = []
    for char in url:
        if char == "<":
            output.append("%3C")
        elif char == ">":
            output.append("%3E")
        elif char == "|":
            output.append("%7C")
        elif unicodedata.category(char) == "Cc":
            output.extend(f"%{byte:02X}" for byte in char.encode())
        else:
            output.append(char)
    escaped = "".join(output)
    return (
        f"<{escaped}>"
        if any(char.isspace() or char in "()" for char in escaped)
        else escaped
    )


def _escape_url_as_text(
    url: str, inline_context: Literal["block", "heading", "table"]
) -> str:
    cleaned = "".join(
        " " if unicodedata.category(char).startswith("C") else char for char in url
    )
    return _escape_text(
        cleaned,
        inline_context,
        _EscapeOptions(trailing_active=True, in_label=True),
    )


def _escape_marker_label(
    label: str, inline_context: Literal["block", "heading", "table"]
) -> str:
    cleaned = "".join(
        " " if unicodedata.category(char).startswith("C") else char for char in label
    )
    return _escape_text(
        cleaned,
        inline_context,
        _EscapeOptions(at_line_start=inline_context == "block", trailing_active=True),
    )


def _backtick_fence(text: str, minimum: int) -> str:
    longest = 0
    current = 0
    for char in text:
        if char == "`":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return "`" * max(longest + 1, minimum)


def _escape_cell_code_span(text: str) -> str:
    output: list[str] = []
    backslashes = 0
    for char in text:
        if char == "|":
            output.extend("\\" for _ in range(backslashes + 1))
            backslashes = 0
        elif char == "\\":
            backslashes += 1
        else:
            backslashes = 0
        output.append(char)
    return "".join(output)


def _render_code_span(
    text: str, inline_context: Literal["block", "heading", "table"]
) -> str:
    text = text.replace("\n", " ")
    fence = _backtick_fence(text, 1)
    padding = " " if text.startswith("`") or text.endswith("`") else ""
    if inline_context == "table":
        text = _escape_cell_code_span(text)
    return f"{fence}{padding}{text}{padding}{fence}"


def _escape_math_dollars(text: str) -> str:
    output: list[str] = []
    backslashes = 0
    for char in text:
        if char == "$" and backslashes % 2 == 0:
            output.append("\\")
        output.append(char)
        backslashes = backslashes + 1 if char == "\\" else 0
    return "".join(output)


def _render_math_span(
    text: str, inline_context: Literal["block", "heading", "table"]
) -> str:
    source = _escape_math_dollars(text.strip()).replace("\n", " ")
    if inline_context == "table":
        output: list[str] = []
        backslashes = 0
        for char in source:
            if char == "|" and backslashes % 2 == 0:
                output.append("\\")
            output.append(char)
            backslashes = backslashes + 1 if char == "\\" else 0
        source = "".join(output)
    return f"${source}$"
