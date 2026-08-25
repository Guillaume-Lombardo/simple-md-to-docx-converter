"""Unit tests for pre-Pandoc Markdown validation."""

import io
from pathlib import PurePosixPath
from typing import cast

import pytest
from PIL import Image

from markweave.conversion.archive import ApprovedDocument, ApprovedResource
from markweave.conversion.errors import ConversionError, ConversionErrorCode
from markweave.conversion.images import ImageLimits, normalize_image
from markweave.conversion.service import DocxConversionService
from markweave.conversion.validation import (
    PANDOC_READER,
    validate_document,
    validate_markdown,
)

IMAGE_LIMITS = ImageLimits(100_000, 256, 256, 65_536, 1_000, 64)


def _normalized_png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(output, format="PNG")
    return normalize_image(PurePosixPath("image.png"), output.getvalue(), IMAGE_LIMITS)


PNG = _normalized_png()


@pytest.mark.unit
def test_reader_expression_is_the_approved_fixed_dialect() -> None:
    assert PANDOC_READER == (
        "commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw_html",
    [
        "<div>content</div>",
        'text <span\n class="x">content</span>',
        "<!-- comment -->",
        "<?processing instruction?>",
        "<!DOCTYPE html>",
        "<![CDATA[content]]>",
    ],
)
def test_raw_html_is_rejected_before_converter_call(mocker, raw_html: str) -> None:
    converter = mocker.Mock()
    service = DocxConversionService(converter)
    with pytest.raises(ConversionError) as captured:
        service.convert(raw_html, b"reference")
    assert captured.value.code is ConversionErrorCode.VALIDATION
    assert str(captured.value) == "Markdown input contains raw HTML."
    converter.convert.assert_not_called()


@pytest.mark.unit
def test_service_propagates_worker_deadline_to_docx_engine(mocker) -> None:
    converter = mocker.Mock()
    converter.convert.return_value = b"docx"

    assert (
        DocxConversionService(converter).convert(
            "# Safe", b"reference", deadline_monotonic=123.5
        )
        == b"docx"
    )
    converter.convert.assert_called_once_with(
        mocker.ANY,
        b"reference",
        deadline_monotonic=123.5,
        cancellation_requested=None,
    )


@pytest.mark.unit
def test_service_uses_simple_and_cancellable_document_engine_paths(mocker) -> None:
    converter = mocker.Mock()
    converter.convert.return_value = b"docx"
    service = DocxConversionService(converter)

    assert service.convert("# Safe", b"reference") == b"docx"
    converter.convert.assert_called_once_with(mocker.ANY, b"reference")

    converter.reset_mock()
    document = ApprovedDocument("# Safe", PurePosixPath("readme.md"), ())
    cancellation = mocker.Mock(return_value=False)
    assert (
        service.convert_document(
            document, b"reference", cancellation_requested=cancellation
        )
        == b"docx"
    )
    converter.convert.assert_called_once_with(
        mocker.ANY,
        b"reference",
        deadline_monotonic=None,
        cancellation_requested=cancellation,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw_html",
    [
        "---\ntitle: |\n  <span>bad</span>\n---\n# Safe",
        "---\ntitle: '<!ENTITY bad>'\n---\n# Safe",
        '---\ntitle: "\\u003cspan\\u003ebad\\u003c/span\\u003e"\n---\n# Safe',
        "Safe.[^1]\n\n[^1]: note\n\n    <span>bad</span>",
    ],
)
def test_extension_context_cannot_hide_raw_html(mocker, raw_html: str) -> None:
    converter = mocker.Mock()
    service = DocxConversionService(converter)
    with pytest.raises(ConversionError) as captured:
        service.convert(raw_html, b"reference")
    assert captured.value.code is ConversionErrorCode.VALIDATION
    assert str(captured.value) == "Markdown input contains raw HTML."
    converter.convert.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    "remote",
    [
        "![image](https://example.test/image.png)",
        "![image](ftp://example.test/image.png)",
        "![image](//example.test/image.png)",
        "![image](data:image/png;base64,AAAA)",
        "![image](file:///etc/passwd)",
        "[link](file:///etc/passwd)",
        "[link](data:text/plain,content)",
        "[link](javascript:alert(1))",
        "[link](mailto:user@example.test)",
        "[link](https:example.test/path)",
        "[link](file%3A///etc/passwd)",
        "[link](%2F%2Fexample.test/path)",
    ],
)
def test_remote_resources_are_rejected_before_converter_call(
    mocker, remote: str
) -> None:
    converter = mocker.Mock()
    service = DocxConversionService(converter)
    with pytest.raises(ConversionError) as captured:
        service.convert(remote, b"reference")
    assert captured.value.code is ConversionErrorCode.VALIDATION
    assert str(captured.value) == "Markdown input contains a remote resource."
    converter.convert.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    "remote",
    [
        "---\ntitle: |\n  [private](https://example.test/private)\n---\n# Safe",
        "---\nsource: '[local](file:///etc/passwd)'\n---\n# Safe",
        "---\nsource: '![data](data:text/plain,secret)'\n---\n# Safe",
        '---\nsource: "[local](file\\u003a///etc/passwd)"\n---\n# Safe',
        "Safe.[^1]\n\n[^1]: note\n\n    [hidden](file:///etc/passwd)",
    ],
)
def test_extension_context_cannot_hide_resource_destination(
    mocker, remote: str
) -> None:
    converter = mocker.Mock()
    service = DocxConversionService(converter)
    with pytest.raises(ConversionError) as captured:
        service.convert(remote, b"reference")
    assert captured.value.code is ConversionErrorCode.VALIDATION
    assert str(captured.value) == "Markdown input contains a remote resource."
    converter.convert.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    "image",
    [
        "![absolute](/etc/private.png)",
        "![escaping](../private.png)",
        "![relative](assets/local.png)",
        "![fragment](#local-image)",
    ],
)
def test_local_images_are_rejected_until_t08_materializes_approved_assets(
    mocker, image: str
) -> None:
    converter = mocker.Mock()
    service = DocxConversionService(converter)
    with pytest.raises(ConversionError) as captured:
        service.convert(image, b"reference")
    assert captured.value.code is ConversionErrorCode.VALIDATION
    assert str(captured.value) == "Markdown input contains an unapproved image."
    converter.convert.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    "markdown",
    [
        "![local](../assets/image.png)",
        "Safe.[^1]\n\n[^1]: ![local](../assets/image.png)",
        "---\ncover: '![local](../assets/image.png)'\n---\n# Safe",
    ],
)
def test_archive_images_must_bind_to_approved_normalized_resources(
    mocker, markdown: str
) -> None:
    converter = mocker.Mock()
    service = DocxConversionService(converter)
    document = ApprovedDocument(
        markdown,
        PurePosixPath("docs/readme.md"),
        (ApprovedResource(PurePosixPath("assets/image.png"), PNG),),
        IMAGE_LIMITS,
    )
    service.convert_document(document, b"reference")
    approved = converter.convert.call_args.args[0]
    assert approved.entrypoint == PurePosixPath("docs/readme.md")
    assert approved.resources == document.resources


@pytest.mark.unit
@pytest.mark.parametrize(
    ("image", "message"),
    [
        ("![missing](missing.png)", "missing or unapproved"),
        ("![escape](../../outside.png)", "escapes the archive"),
        ("![absolute](/etc/passwd)", "image path is invalid"),
        ("![query](../assets/image.png?raw=1)", "image path is invalid"),
        ("![fragment](../assets/image.png#part)", "image path is invalid"),
        ("![encoded](..%2fassets/image.png)", "image path is invalid"),
        ("![double](%252e%252e%252fassets/image.png)", "image path is invalid"),
    ],
)
def test_invalid_archive_image_references_fail_before_converter(
    mocker, image: str, message: str
) -> None:
    converter = mocker.Mock()
    document = ApprovedDocument(
        image,
        PurePosixPath("docs/readme.md"),
        (ApprovedResource(PurePosixPath("assets/image.png"), PNG),),
        IMAGE_LIMITS,
    )
    with pytest.raises(ConversionError, match=message):
        DocxConversionService(converter).convert_document(document, b"reference")
    converter.convert.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    "document",
    [
        ApprovedDocument("# Safe", PurePosixPath("../escape.md"), ()),
        ApprovedDocument(
            "# Safe",
            PurePosixPath("document.md"),
            (),
            image_limits=cast("ImageLimits", "invalid"),
        ),
        ApprovedDocument(
            "![x](x.png)",
            PurePosixPath("document.md"),
            (ApprovedResource(PurePosixPath("../x.png"), PNG),),
            IMAGE_LIMITS,
        ),
        ApprovedDocument(
            "![x](x.png)",
            PurePosixPath("document.md"),
            (ApprovedResource(PurePosixPath("x.png"), b"\x89PNG\r\n\x1a\nnot-a-png"),),
            IMAGE_LIMITS,
        ),
        ApprovedDocument(
            "![x](x.png)",
            PurePosixPath("document.md"),
            (ApprovedResource(PurePosixPath("x.png"), PNG),),
        ),
        ApprovedDocument(
            "![x](DOCUMENT.MD)",
            PurePosixPath("document.md"),
            (ApprovedResource(PurePosixPath("DOCUMENT.MD"), PNG),),
            IMAGE_LIMITS,
        ),
        ApprovedDocument(
            "![x](assets/x.png)",
            PurePosixPath("document.md"),
            (
                ApprovedResource(PurePosixPath("assets"), PNG),
                ApprovedResource(PurePosixPath("assets/x.png"), PNG),
            ),
            IMAGE_LIMITS,
        ),
    ],
)
def test_forged_approved_document_manifest_is_rejected(
    document: ApprovedDocument,
) -> None:
    with pytest.raises(ConversionError, match="package is invalid"):
        validate_document(document)


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw_attribute",
    [
        "`<img src=x>`{=html}",
        "`unsafe`{=1html}",
        "`unsafe`{=123}",
        "`unsafe`{=é}",
        "`unsafe`{=λ}",
        "`unsafe`{=日本語}",
        "`unsafe`{=\uff11\uff12\uff13}",
        "`unsafe`{=e\u0301}",
        "`unsafe`{=\u0301}",
        "`unsafe`{=a\u0308\u0301}",
        "`unsafe`{=x\u20dd}",
        "`unsafe`{=x\ufe0f}",
        "`unsafe`{=क्}",
        "`unsafe`{=html}suffix",
        "`\\command{unsafe}`{=tex}",
        "```{=html}\n<div>unsafe</div>\n```",
        "```  {=html}  \n<div>unsafe</div>\n```",
        "```{=123}\nunsafe\n```",
        "~~~{=tex}\n\\command{unsafe}\n~~~",
    ],
)
def test_pandoc_raw_attributes_are_rejected_before_converter_call(
    mocker, raw_attribute: str
) -> None:
    converter = mocker.Mock()
    service = DocxConversionService(converter)
    with pytest.raises(ConversionError) as captured:
        service.convert(raw_attribute, b"reference")
    assert captured.value.code is ConversionErrorCode.VALIDATION
    assert str(captured.value) == "Markdown input contains a raw attribute."
    converter.convert.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    "literal",
    [
        "`{=html}`",
        r"`literal`\{=html}",
        "`literal` {=html}",
        "`literal`{=_html}",
        "`literal`{=-html}",
        "`literal`{=html:foo}",
        "`literal`{=html.foo}",
        "`literal`{=a\u200db}",
        "```text\n{=html}\n```",
        "    `{=tex}`",
    ],
)
def test_raw_attribute_text_remains_allowed_inside_real_code(literal: str) -> None:
    assert validate_markdown(literal).text == literal


@pytest.mark.unit
def test_code_can_show_html_and_urls_as_literal_examples() -> None:
    markdown = """Inline `<span>
https://example.test</span>`.

```html
<div>https://example.test/image.png</div>
```

    <aside>https://example.test/indented</aside>
"""
    assert validate_markdown(markdown).text == markdown


@pytest.mark.unit
def test_nonclosing_fence_with_trailing_text_keeps_content_literal() -> None:
    markdown = """```html
literal
```not-a-closing-fence
<section>still literal code</section>
"""
    assert validate_markdown(markdown).text == markdown


@pytest.mark.unit
@pytest.mark.parametrize(
    "literal",
    [r"\<span>escaped\</span>", "<https://example.test/resource>"],
)
def test_escaped_html_is_text_but_remote_autolink_is_still_forbidden(
    literal: str,
) -> None:
    if literal.startswith("\\"):
        assert validate_markdown(literal).text == literal
    else:
        with pytest.raises(ConversionError, match="remote resource"):
            validate_markdown(literal)


@pytest.mark.unit
def test_plain_url_text_is_not_a_loaded_resource() -> None:
    markdown = "Documentation example: https://example.test/resource"
    assert validate_markdown(markdown).text == markdown


@pytest.mark.unit
def test_incomplete_tag_text_in_metadata_is_not_raw_html() -> None:
    markdown = '---\ntitle: "x <foo y"\n---\n# Safe'
    assert validate_markdown(markdown).text == markdown


@pytest.mark.unit
def test_invalid_yaml_metadata_has_stable_validation_error(mocker) -> None:
    converter = mocker.Mock()
    service = DocxConversionService(converter)
    markdown = "---\ntitle: [invalid\n---\n# Safe"
    with pytest.raises(ConversionError) as captured:
        service.convert(markdown, b"reference")
    assert captured.value.code is ConversionErrorCode.VALIDATION
    assert str(captured.value) == "Markdown input contains invalid YAML metadata."
    converter.convert.assert_not_called()


@pytest.mark.unit
def test_excessively_nested_yaml_has_stable_validation_error(mocker) -> None:
    converter = mocker.Mock()
    service = DocxConversionService(converter)
    depth = 500
    nested_value = "[" * depth + "safe" + "]" * depth
    markdown = f"---\nvalue: {nested_value}\n---\n# Safe"
    with pytest.raises(ConversionError) as captured:
        service.convert(markdown, b"reference")
    assert captured.value.code is ConversionErrorCode.VALIDATION
    assert str(captured.value) == "Markdown input contains invalid YAML metadata."
    converter.convert.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("empty", ["", "  \n"])
def test_empty_markdown_has_stable_validation_error(empty: str) -> None:
    with pytest.raises(ConversionError) as captured:
        validate_markdown(empty)
    assert captured.value.code is ConversionErrorCode.VALIDATION
    assert str(captured.value) == "Markdown input must not be empty."
