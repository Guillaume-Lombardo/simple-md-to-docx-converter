"""Unit tests for pre-Pandoc Markdown validation."""

import pytest

from md_converter.conversion.errors import ConversionError, ConversionErrorCode
from md_converter.conversion.service import DocxConversionService
from md_converter.conversion.validation import PANDOC_READER, validate_markdown


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
@pytest.mark.parametrize("empty", ["", "  \n"])
def test_empty_markdown_has_stable_validation_error(empty: str) -> None:
    with pytest.raises(ConversionError) as captured:
        validate_markdown(empty)
    assert captured.value.code is ConversionErrorCode.VALIDATION
    assert str(captured.value) == "Markdown input must not be empty."
