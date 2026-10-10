"""Service-generated parity corpus for Markdown image references (T104).

The service parser in ``markweave.conversion.image_references`` is authoritative.
This module builds the checked-in fixture from it; a Python test requires the file
to match and the browser packager's test consumes the same file. Regenerate with:

    uv run python -m tests.markdown_image_references
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from markweave.conversion.errors import ConversionError
from markweave.conversion.image_references import (
    MARKDOWN,
    image_destinations,
    is_remote_destination,
    resolve_local_image_path,
    walk,
)

FIXTURE = Path(__file__).parent / "fixtures" / "markdown-image-references.json"
ENTRYPOINT = PurePosixPath("document.md")

MARKDOWN_CASES = (
    # Inline images, titles, and angle-bracket destinations.
    "![a](img/a.png)",
    '![a](<my file.png> "t")',
    "![a](x.png 'title')",
    "![a](x.png (title))",
    "![a]( spaced.png )",
    "![a](\n  next-line.png\n)",
    "![a](\n\nblank.png)",
    "![a](<unterminated.png)",
    "![a](<a\\>b.png>)",
    "![a](open.png 'title)",
    "![a](trailing.png junk)",
    "![a](p(1).png)",
    "![a](p(1.png)",
    "![a [b] c](n.png)",
    "![never closed",
    "![a\n\nb](split.png)",
    "[![a](in.png)](https://example.com)",
    "![x](a.png\x01)",
    "![a](caf\u00e9.png)",
    "line\r\n![a](crlf.png)\r\n",
    # Reference images and definitions, including multiline and container ones.
    "![a][r]\n\n[r]: x.png",
    "![R]\n\n[r]: <y z.png>",
    "![a][]\n\n[a]: c.png",
    "![a][missing]\n",
    "![logo][ref]\n\n[ref]:\n  assets/logo.png\n",
    '![logo][ref]\n\n[ref]:\n  assets/logo.png\n  "Title"\n',
    "![logo][ref]\n\n> [ref]: assets/quoted.png\n",
    "![logo][ref]\n\n- [ref]: assets/listed.png\n",
    "> ![q][ref]\n>\n> [ref]: assets/inner.png\n",
    "![a][R  E F]\n\n[r e f]: spaced-label.png",
    "![a][r]\n\n[r]: first.png\n[r]: second.png",
    "[r]:\n![r]",
    "[r]: <broken\n![r]",
    # Code is never an image.
    "`![a](no.png)`",
    "text ``![x](d.png)`` more",
    "``unclosed ![a](unclosed-code.png)",
    "\\`![a](after-escaped-tick.png)`",
    "```\n![a](no.png)\n```",
    "````md\n```\n![a](no.png)\n````",
    "~~~\n![x](t.png)\n~~~",
    "- item\n  ```\n  ![a](fenced.png)\n  ```\n",
    "    ![a](code.png)",
    "para\n    ![a](lazy.png)",
    "- item\n\n    ![a](list.png)",
    "- list\n\n      ![literal](missing.png)",
    "1. list\n\n       ![literal](numbered-code.png)",
    "- item\n\nback\n\n    ![a](after-list.png)",
    "> quote\n>\n>     ![a](quoted-code.png)",
    # Escapes and entities.
    "\\![a](esc.png)",
    "![a](a\\_b.png)",
    "![a](a\\b.png)",
    "![a](&amp;.png)",
    "![logo](assets/&copy;.png)",
    "![e](&eacute;t&eacute;.png)",
    "![n](&#x41;&#66;.png)",
    "![n](&CounterClockwiseContourIntegral;.png)",
    "![n](&notanentity;.png)",
    "![n](&#0;.png)",
    # Containers, footnotes, raw HTML, links.
    "> ![q](quote.png)",
    "Text[^1]\n\n[^1]: ![f](footnote.png)",
    '<img src="html.png">',
    "<https://example.com/a.png>",
    "![a](https://example.com/remote.png)",
    "| a |\n| - |\n| ![t](table.png) |",
    # Front matter scalars are validated as Markdown too.
    "---\nimage: ![x](meta.png)\n---\n# t",
    "---\nnested:\n    deep: ![x](deep.png)\n---\n",
    '---\nquoted: "![x](quoted.png)"\nlist:\n  - ![y](item.png)\n---\nbody ![z](body.png)\n',
    "---\nblock: |\n  ![x](block.png)\n---\n",
    "---\nnot: closed\n![x](unclosed-front.png)\n",
    "---\nshared: &a '![s](s.png)'\nagain: *a\nlist: [*a, '![t](t.png)']\n---\n",
    "---\ntagged: !custom '![c](tag.png)'\nplain: '![p](plain.png)'\n---\n",
    "---\ndup: '![a](one.png)'\ndup: '![b](two.png)'\n'![k](key.png)': 1\n---\n",
    "---\na: [\n---\n![x](after-invalid.png)\n",
)

RESOLUTION_CASES = (
    "assets/a.png",
    "./assets/a.png",
    "assets/../a.png",
    "assets/./b.png",
    "assets//c.png",
    "my%20file.png",
    "100%25.png",
    "caf%C3%A9.png",
    "assets/%C2%A9.png",
    "../a.png",
    "assets/../../a.png",
    "/etc/a.png",
    "a.png?x=1",
    "a.png#frag",
    "a.png?",
    "%2e%2e/a.png",
    "a%2fb.png",
    "a%5Cb.png",
    "a%2520b.png",
    "a%ff.png",
    "1:a.png",
    "x%00.png",
    ".",
    "./",
    "a/..",
    "https://example.com/a.png",
    "//example.com/a.png",
    "data:image/png,x",
    "%2f%2fx",
    "c:/a.png",
    "%252f%252fx",
)


def _resolution(destination: str) -> dict[str, Any]:
    if is_remote_destination(destination):
        return {"destination": destination, "remote": True}
    try:
        path = resolve_local_image_path(destination, ENTRYPOINT)
    except ConversionError as error:
        return {"destination": destination, "error": str(error)}
    return {"destination": destination, "path": path.as_posix() if path.parts else ""}


def _destinations(markdown: str) -> dict[str, Any]:
    try:
        return {
            "markdown": markdown,
            "destinations": list(image_destinations(markdown)),
        }
    except ConversionError as error:
        # The service rejects the metadata; the browser keeps only body images.
        body = [
            str(token.attrGet("src"))
            for token in walk(MARKDOWN.parse(markdown))
            if token.type == "image"
        ]
        return {"markdown": markdown, "error": str(error), "body_destinations": body}


def build() -> dict[str, Any]:
    """Return the corpus with the service parser's answers."""

    return {
        "generator": "uv run python -m tests.markdown_image_references",
        "markdown": [_destinations(markdown) for markdown in MARKDOWN_CASES],
        "resolutions": [_resolution(value) for value in RESOLUTION_CASES],
    }


def render() -> str:
    return json.dumps(build(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    FIXTURE.write_text(render(), encoding="utf-8")
