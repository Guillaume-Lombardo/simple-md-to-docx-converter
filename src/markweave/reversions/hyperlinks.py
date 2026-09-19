"""Pure destination validation shared by the reverse renderer boundary.

This module does not import anydoc, parse documents, or mirror its renderer.
"""

import unicodedata
from urllib.parse import unquote, urlsplit

_ALLOWED_HYPERLINK_SCHEMES = frozenset({"http", "https"})
_MAX_URL_DECODE_PASSES = 2
_MAX_URL_PORT = 65_535


def _decoded_destination_variants(value: str) -> tuple[str, ...]:
    variants = [value]
    for _ in range(_MAX_URL_DECODE_PASSES):
        decoded = unquote(variants[-1], encoding="utf-8", errors="replace")
        if decoded == variants[-1]:
            break
        variants.append(decoded)
    return tuple(variants)


def _is_safe_hyperlink(value: str) -> bool:
    variants = _decoded_destination_variants(value)
    if any(
        character.isspace() or unicodedata.category(character) == "Cc"
        for variant in variants
        for character in variant
    ):
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.casefold() in _ALLOWED_HYPERLINK_SCHEMES
        and bool(parsed.netloc)
        and bool(hostname)
        and parsed.username is None
        and parsed.password is None
        and (port is None or 0 <= port <= _MAX_URL_PORT)
        and "%" not in hostname
        and "\\" not in value
    )
