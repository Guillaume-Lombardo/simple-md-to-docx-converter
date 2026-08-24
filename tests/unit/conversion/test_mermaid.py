"""Unit tests for bounded local Mermaid preprocessing."""

from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import cast

import pytest
from PIL import Image

from md_converter.conversion.archive import ApprovedResource
from md_converter.conversion.errors import ConversionError, ConversionErrorCode
from md_converter.conversion.images import ImageLimits, normalize_image
from md_converter.conversion.mermaid import (
    MermaidCliRenderer,
    MermaidConfig,
    MermaidLimits,
    MermaidPreprocessingConverter,
    contains_mermaid,
    render_mermaid,
)
from md_converter.conversion.validation import ApprovedMarkdown

pytestmark = pytest.mark.unit

IMAGE_LIMITS = ImageLimits(100_000, 256, 256, 65_536, 1_000, 64)
LIMITS = MermaidLimits(4, 1_000, 3_000, 100_000, 300_000, 4, 4)
RAW_OUTPUT = b"untrusted-png-output"


def _png(width: int = 8, height: int = 6) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), "blue").save(output, format="PNG")
    return normalize_image(PurePosixPath("image.png"), output.getvalue(), IMAGE_LIMITS)


def _patch_normalization(mocker, png: bytes | None = None) -> bytes:
    normalized = png or _png()
    mocker.patch(
        "md_converter.conversion.mermaid.normalize_image", return_value=normalized
    )
    return normalized


def test_supported_fence_is_replaced_with_bounded_approved_png(mocker) -> None:
    png = _patch_normalization(mocker)
    renderer = mocker.Mock()
    renderer.render.return_value = RAW_OUTPUT
    markdown = ApprovedMarkdown(
        "# Before\n\n```mermaid\nflowchart LR\nA-->B\n```\n\nAfter\n"
    )

    rendered = render_mermaid(markdown, renderer, LIMITS, IMAGE_LIMITS)

    assert "flowchart LR" not in rendered.text
    assert "# Before" in rendered.text
    assert "After" in rendered.text
    assert (
        "![Mermaid diagram 1](.md-converter-mermaid/0001.png){width=4px}"
    ) in rendered.text
    assert rendered.resources == (
        ApprovedResource(PurePosixPath(".md-converter-mermaid/0001.png"), png),
    )
    assert rendered.image_limits is IMAGE_LIMITS
    renderer.render.assert_called_once_with("flowchart LR\nA-->B\n", 100_000)


def test_multiple_nested_fences_keep_container_prefixes_and_order(mocker) -> None:
    _patch_normalization(mocker, _png(2, 4))
    renderer = mocker.Mock()
    renderer.render.return_value = RAW_OUTPUT
    markdown = ApprovedMarkdown(
        "> ```mermaid\n> flowchart LR\n> A-->B\n> ```\n\n"
        "- item\n\n  ~~~MERMAID\n  sequenceDiagram\n  A->>B: hello\n  ~~~\n",
        entrypoint=PurePosixPath("docs/readme.md"),
    )

    rendered = render_mermaid(markdown, renderer, LIMITS, IMAGE_LIMITS)

    assert "> ![Mermaid diagram 1]" in rendered.text
    assert "  ![Mermaid diagram 2]" in rendered.text
    assert [resource.path for resource in rendered.resources] == [
        PurePosixPath("docs/.md-converter-mermaid/0001.png"),
        PurePosixPath("docs/.md-converter-mermaid/0002.png"),
    ]
    assert renderer.render.call_count == 2


def test_document_height_is_the_only_attribute_for_portrait_scaling(mocker) -> None:
    _patch_normalization(mocker, _png(4, 8))
    renderer = mocker.Mock()
    renderer.render.return_value = RAW_OUTPUT

    rendered = render_mermaid(
        ApprovedMarkdown("```mermaid\nflowchart TD\nA-->B\n```"),
        renderer,
        LIMITS,
        IMAGE_LIMITS,
    )

    assert "{height=4px}" in rendered.text
    assert "width=" not in rendered.text


@pytest.mark.parametrize(
    "markdown",
    [
        "```python\nprint('mermaid')\n```",
        "` ```mermaid `",
        "```mermaid extra\nflowchart LR\n```",
        "---\nvalue: '```mermaid'\n---\n# Safe",
    ],
)
def test_non_supported_mermaid_spellings_are_unchanged(mocker, markdown: str) -> None:
    renderer = mocker.Mock()
    approved = ApprovedMarkdown(markdown)
    assert render_mermaid(approved, renderer, LIMITS) is approved
    assert contains_mermaid(markdown) is False
    renderer.render.assert_not_called()


def test_mermaid_presence_requires_explicit_image_limits(mocker) -> None:
    with pytest.raises(ConversionError) as captured:
        render_mermaid(
            ApprovedMarkdown("```mermaid\nflowchart LR\n```"),
            mocker.Mock(),
            LIMITS,
        )
    assert captured.value.code is ConversionErrorCode.MERMAID_UNAVAILABLE
    assert str(captured.value) == "Mermaid rendering is unavailable."


@pytest.mark.parametrize(
    "limits",
    [
        MermaidLimits(1, 8, 100, 100, 100, 10, 10),
        MermaidLimits(2, 100, 8, 100, 100, 10, 10),
    ],
)
def test_source_limits_fail_before_renderer(mocker, limits: MermaidLimits) -> None:
    renderer = mocker.Mock()
    markdown = ApprovedMarkdown(
        "```mermaid\nflowchart LR\n```\n```mermaid\nflowchart RL\n```"
    )
    with pytest.raises(ConversionError, match="configured Mermaid limits"):
        render_mermaid(markdown, renderer, limits, IMAGE_LIMITS)
    renderer.render.assert_not_called()


def test_empty_mermaid_fence_fails_before_renderer(mocker) -> None:
    renderer = mocker.Mock()
    with pytest.raises(ConversionError, match="configured Mermaid limits"):
        render_mermaid(
            ApprovedMarkdown("```mermaid\n\n```"), renderer, LIMITS, IMAGE_LIMITS
        )
    renderer.render.assert_not_called()


@pytest.mark.parametrize(
    "source",
    [
        '%%{init: {"securityLevel": "loose"}}%%\nflowchart LR\nA-->B',
        "%% { initialize: { theme: 'dark' } } %%\nflowchart LR\nA-->B",
        "---\nconfig:\n  securityLevel: loose\n---\nflowchart LR\nA-->B",
    ],
)
def test_document_configuration_directives_fail_before_renderer(
    mocker, source: str
) -> None:
    renderer = mocker.Mock()
    with pytest.raises(ConversionError, match="unsupported Mermaid configuration"):
        render_mermaid(
            ApprovedMarkdown(f"```mermaid\n{source}\n```"),
            renderer,
            LIMITS,
            IMAGE_LIMITS,
        )
    renderer.render.assert_not_called()


def test_generated_resource_collision_fails_before_renderer(mocker) -> None:
    png = _png()
    markdown = ApprovedMarkdown(
        "```mermaid\nflowchart LR\n```",
        resources=(
            ApprovedResource(PurePosixPath(".MD-CONVERTER-MERMAID/0001.PNG"), png),
        ),
        image_limits=IMAGE_LIMITS,
    )
    renderer = mocker.Mock()
    with pytest.raises(ConversionError, match="package is invalid"):
        render_mermaid(markdown, renderer, LIMITS)
    renderer.render.assert_not_called()


@pytest.mark.parametrize(
    "resource_path",
    [
        PurePosixPath(".md-converter-mermaid/0002.png"),
        PurePosixPath(".md-converter-mermaid/0001.png/child.png"),
    ],
)
def test_all_generated_paths_and_prefixes_are_validated_before_renderer(
    mocker, resource_path: PurePosixPath
) -> None:
    markdown = ApprovedMarkdown(
        "```mermaid\nflowchart LR\n```\n```mermaid\nflowchart RL\n```",
        resources=(ApprovedResource(resource_path, _png()),),
        image_limits=IMAGE_LIMITS,
    )
    renderer = mocker.Mock()

    with pytest.raises(ConversionError, match="package is invalid"):
        render_mermaid(markdown, renderer, LIMITS)

    renderer.render.assert_not_called()


@pytest.mark.parametrize("output", [b"", b"x" * 100_001])
def test_invalid_renderer_output_is_content_free(mocker, output: bytes) -> None:
    renderer = mocker.Mock()
    renderer.render.return_value = output
    source = "secret diagram"
    with pytest.raises(ConversionError) as captured:
        render_mermaid(
            ApprovedMarkdown(f"```mermaid\n{source}\n```"),
            renderer,
            LIMITS,
            IMAGE_LIMITS,
        )
    assert captured.value.code is ConversionErrorCode.INVALID_MERMAID_OUTPUT
    assert source not in str(captured.value)


def test_normalization_failure_is_mapped_to_mermaid_output_error(mocker) -> None:
    mocker.patch(
        "md_converter.conversion.mermaid.normalize_image",
        side_effect=ConversionError(ConversionErrorCode.VALIDATION, "image detail"),
    )
    renderer = mocker.Mock()
    renderer.render.return_value = RAW_OUTPUT
    with pytest.raises(ConversionError) as captured:
        render_mermaid(
            ApprovedMarkdown("```mermaid\nflowchart LR\n```"),
            renderer,
            LIMITS,
            IMAGE_LIMITS,
        )
    assert captured.value.code is ConversionErrorCode.INVALID_MERMAID_OUTPUT
    assert "image detail" not in str(captured.value)


def test_normalized_output_must_stay_within_per_diagram_limit(mocker) -> None:
    mocker.patch(
        "md_converter.conversion.mermaid.normalize_image", return_value=b"x" * 101
    )
    renderer = mocker.Mock()
    renderer.render.return_value = b"raw"
    limits = MermaidLimits(1, 100, 100, 100, 200, 10, 10)

    with pytest.raises(ConversionError) as captured:
        render_mermaid(
            ApprovedMarkdown("```mermaid\nflowchart LR\n```"),
            renderer,
            limits,
            IMAGE_LIMITS,
        )

    assert captured.value.code is ConversionErrorCode.INVALID_MERMAID_OUTPUT


def test_total_raw_output_limit_is_enforced(mocker) -> None:
    _patch_normalization(mocker)
    renderer = mocker.Mock()
    renderer.render.return_value = b"123456"
    limits = MermaidLimits(2, 100, 200, 100_000, 10, 10, 10)
    with pytest.raises(ConversionError, match="configured Mermaid limits"):
        render_mermaid(
            ApprovedMarkdown(
                "```mermaid\nflowchart LR\n```\n```mermaid\nflowchart RL\n```"
            ),
            renderer,
            limits,
            IMAGE_LIMITS,
        )


def test_total_normalized_output_limit_is_enforced(mocker) -> None:
    png = _patch_normalization(mocker)
    renderer = mocker.Mock()
    renderer.render.return_value = b"raw"
    limits = MermaidLimits(2, 100, 200, len(png), len(png), 10, 10)

    with pytest.raises(ConversionError, match="configured Mermaid limits"):
        render_mermaid(
            ApprovedMarkdown(
                "```mermaid\nflowchart LR\n```\n```mermaid\nflowchart RL\n```"
            ),
            renderer,
            limits,
            IMAGE_LIMITS,
        )


def test_preprocessing_converter_delegates_only_rendered_markdown(mocker) -> None:
    _patch_normalization(mocker)
    renderer = mocker.Mock()
    renderer.render.return_value = RAW_OUTPUT
    converter = mocker.Mock()
    converter.convert.return_value = b"docx"
    wrapper = MermaidPreprocessingConverter(converter, renderer, LIMITS, IMAGE_LIMITS)
    source = ApprovedMarkdown("```mermaid\nflowchart LR\n```")

    assert wrapper.convert(source, b"reference") == b"docx"
    delegated, reference = converter.convert.call_args.args
    assert "flowchart LR" not in delegated.text
    assert reference == b"reference"


@pytest.mark.parametrize(
    "values",
    [
        (0, 1, 1, 1, 1, 1, 1),
        (1, True, 1, 1, 1, 1, 1),
        (1, 1, -1, 1, 1, 1, 1),
        (1, 1, 1, 1, 1, 0, 1),
    ],
)
def test_mermaid_limits_require_positive_integers(
    values: tuple[int, int, int, int, int, int, int],
) -> None:
    with pytest.raises(ValueError, match="positive integers"):
        MermaidLimits(*values)


def _config(tmp_path: Path) -> MermaidConfig:
    return MermaidConfig("mmdc", "/usr/bin/chrome", 5.0, 0.5, 800, 600, tmp_path)


@pytest.mark.parametrize(
    "config",
    [
        ("", "chrome", 1.0, 1.0, 1, 1),
        ("mmdc", "", 1.0, 1.0, 1, 1),
        ("mmdc", "chrome", 0.0, 1.0, 1, 1),
        ("mmdc", "chrome", 1.0, float("inf"), 1, 1),
        ("mmdc", "chrome", 1.0, 1.0, True, 1),
        ("mmdc", "chrome", 1.0, 1.0, 1, 0),
    ],
)
def test_mermaid_config_rejects_invalid_values(
    config: tuple[str, str, float, float, int, int],
) -> None:
    with pytest.raises(ValueError):
        MermaidConfig(*config)


def test_mermaid_config_rejects_missing_workspace(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="workspace root"):
        MermaidConfig("mmdc", "chrome", 1.0, 1.0, 1, 1, tmp_path / "missing")


def test_cli_uses_fixed_arguments_environment_and_private_workspace(
    tmp_path: Path, mocker
) -> None:
    process = mocker.Mock()
    process.pid = 2345
    process.wait.return_value = 0
    workspaces: list[Path] = []

    def start(arguments, **options):
        workspace = options["cwd"]
        workspaces.append(workspace)
        assert arguments == [
            "mmdc",
            "--quiet",
            "--puppeteerConfigFile",
            str(workspace / "puppeteer.json"),
            "--configFile",
            str(workspace / "mermaid.json"),
            "--input",
            str(workspace / "diagram.mmd"),
            "--output",
            str(workspace / "diagram.png"),
            "--outputFormat",
            "png",
            "--backgroundColor",
            "transparent",
            "--width",
            "800",
            "--height",
            "600",
            "--scale",
            "1",
        ]
        assert "--no-sandbox" not in arguments
        assert options["shell"] is False
        assert options["start_new_session"] is True
        assert options["stdin"] is subprocess.DEVNULL
        assert options["stdout"] is subprocess.DEVNULL
        assert options["stderr"] is subprocess.DEVNULL
        environment = options["env"]
        assert environment["PATH"] == "/fixed/path"
        assert environment["PUPPETEER_SKIP_DOWNLOAD"] == "true"
        assert environment["PUPPETEER_EXECUTABLE_PATH"] == "/usr/bin/chrome"
        assert "SECRET" not in environment
        assert json.loads((workspace / "puppeteer.json").read_text()) == {
            "executablePath": "/usr/bin/chrome",
            "headless": "shell",
        }
        assert json.loads((workspace / "mermaid.json").read_text()) == {
            "securityLevel": "strict"
        }
        assert (workspace / "diagram.mmd").read_text() == "flowchart LR"
        (workspace / "diagram.png").write_bytes(RAW_OUTPUT)
        return process

    popen = mocker.patch(
        "md_converter.conversion.mermaid.subprocess.Popen", side_effect=start
    )
    killpg = mocker.patch(
        "md_converter.conversion.mermaid.os.killpg", side_effect=ProcessLookupError
    )
    renderer = MermaidCliRenderer(
        _config(tmp_path), {"PATH": "/fixed/path", "SECRET": "not-allowed"}
    )
    assert renderer.render("flowchart LR", 100_000) == RAW_OUTPUT
    assert popen.call_count == 1
    killpg.assert_called_once_with(2345, 15)
    assert len(workspaces) == 1
    assert not workspaces[0].exists()
    assert list(tmp_path.iterdir()) == []


def test_cli_maps_unavailable_and_nonzero_failures(tmp_path: Path, mocker) -> None:
    popen = mocker.patch("md_converter.conversion.mermaid.subprocess.Popen")
    popen.side_effect = OSError
    with pytest.raises(ConversionError) as unavailable:
        MermaidCliRenderer(_config(tmp_path), {}).render("secret", 100_000)
    assert unavailable.value.code is ConversionErrorCode.MERMAID_UNAVAILABLE
    assert "secret" not in str(unavailable.value)

    process = mocker.Mock()
    process.pid = 4321
    process.wait.return_value = 2
    popen.side_effect = None
    popen.return_value = process
    killpg = mocker.patch("md_converter.conversion.mermaid.os.killpg")
    with pytest.raises(ConversionError) as failed:
        MermaidCliRenderer(_config(tmp_path), {}).render("secret", 100_000)
    assert failed.value.code is ConversionErrorCode.MERMAID_FAILURE
    assert "secret" not in str(failed.value)
    assert [call.args for call in killpg.call_args_list] == [
        (4321, 15),
        (4321, 0),
        (4321, 9),
    ]


def test_cli_timeout_terminates_then_kills_process_group(
    tmp_path: Path, mocker
) -> None:
    process = mocker.Mock()
    process.pid = 1234
    process.wait.side_effect = [
        subprocess.TimeoutExpired("mmdc", 5),
        subprocess.TimeoutExpired("mmdc", 0.5),
        0,
    ]
    mocker.patch(
        "md_converter.conversion.mermaid.subprocess.Popen", return_value=process
    )
    killpg = mocker.patch("md_converter.conversion.mermaid.os.killpg")

    with pytest.raises(ConversionError) as captured:
        MermaidCliRenderer(_config(tmp_path), {}).render("secret", 100_000)

    assert captured.value.code is ConversionErrorCode.MERMAID_TIMEOUT
    assert [call.args for call in killpg.call_args_list] == [(1234, 15), (1234, 9)]
    assert process.wait.call_count == 3


def test_cli_missing_output_is_invalid(tmp_path: Path, mocker) -> None:
    process = mocker.Mock()
    process.pid = 3456
    process.wait.return_value = 0
    mocker.patch(
        "md_converter.conversion.mermaid.os.killpg", side_effect=ProcessLookupError
    )

    def missing(_arguments, **_options):
        return process

    mocker.patch(
        "md_converter.conversion.mermaid.subprocess.Popen", side_effect=missing
    )
    with pytest.raises(ConversionError) as captured:
        MermaidCliRenderer(_config(tmp_path), {}).render("secret", 100_000)
    assert captured.value.code is ConversionErrorCode.INVALID_MERMAID_OUTPUT


def test_cli_symlink_output_is_invalid(tmp_path: Path, mocker) -> None:
    process = mocker.Mock()
    process.pid = 4567
    process.wait.return_value = 0
    mocker.patch(
        "md_converter.conversion.mermaid.os.killpg", side_effect=ProcessLookupError
    )

    def symlink(_arguments, **options):
        (options["cwd"] / "diagram.png").symlink_to("/dev/null")
        return process

    mocker.patch(
        "md_converter.conversion.mermaid.subprocess.Popen", side_effect=symlink
    )
    with pytest.raises(ConversionError) as captured:
        MermaidCliRenderer(_config(tmp_path), {}).render("secret", 100_000)
    assert captured.value.code is ConversionErrorCode.INVALID_MERMAID_OUTPUT


def test_cli_fifo_output_is_rejected_without_blocking(tmp_path: Path, mocker) -> None:
    process = mocker.Mock()
    process.pid = 4789
    process.wait.return_value = 0
    mocker.patch(
        "md_converter.conversion.mermaid.os.killpg", side_effect=ProcessLookupError
    )

    def fifo(_arguments, **options):
        os.mkfifo(options["cwd"] / "diagram.png")
        return process

    mocker.patch("md_converter.conversion.mermaid.subprocess.Popen", side_effect=fifo)
    with pytest.raises(ConversionError) as captured:
        MermaidCliRenderer(_config(tmp_path), {}).render("secret", 100_000)
    assert captured.value.code is ConversionErrorCode.INVALID_MERMAID_OUTPUT


def test_cli_output_read_is_bounded(tmp_path: Path, mocker) -> None:
    process = mocker.Mock()
    process.pid = 5678
    process.wait.return_value = 0
    mocker.patch(
        "md_converter.conversion.mermaid.os.killpg", side_effect=ProcessLookupError
    )

    def oversized(_arguments, **options):
        (options["cwd"] / "diagram.png").write_bytes(b"123456")
        return process

    mocker.patch(
        "md_converter.conversion.mermaid.subprocess.Popen", side_effect=oversized
    )
    with pytest.raises(ConversionError) as captured:
        MermaidCliRenderer(_config(tmp_path), {}).render("secret", 5)
    assert captured.value.code is ConversionErrorCode.INVALID_MERMAID_OUTPUT


def test_cli_requires_positive_output_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="output limit"):
        MermaidCliRenderer(_config(tmp_path), {}).render("secret", 0)


def test_cli_process_disappearance_during_timeout_is_safe(
    tmp_path: Path, mocker
) -> None:
    process = mocker.Mock()
    process.pid = 1234
    process.wait.side_effect = subprocess.TimeoutExpired("mmdc", 5)
    mocker.patch(
        "md_converter.conversion.mermaid.subprocess.Popen", return_value=process
    )
    mocker.patch(
        "md_converter.conversion.mermaid.os.killpg", side_effect=ProcessLookupError
    )
    with pytest.raises(ConversionError) as captured:
        MermaidCliRenderer(_config(tmp_path), {}).render("secret", 100_000)
    assert captured.value.code is ConversionErrorCode.MERMAID_TIMEOUT


def test_cli_final_wait_after_sigkill_is_bounded(tmp_path: Path, mocker) -> None:
    process = mocker.Mock()
    process.pid = 6789
    process.wait.side_effect = [
        subprocess.TimeoutExpired("mmdc", 5),
        subprocess.TimeoutExpired("mmdc", 0.5),
        subprocess.TimeoutExpired("mmdc", 0.5),
    ]
    mocker.patch(
        "md_converter.conversion.mermaid.subprocess.Popen", return_value=process
    )
    killpg = mocker.patch("md_converter.conversion.mermaid.os.killpg")

    with pytest.raises(ConversionError) as captured:
        MermaidCliRenderer(_config(tmp_path), {}).render("secret", 100_000)

    assert captured.value.code is ConversionErrorCode.MERMAID_TIMEOUT
    assert [call.args for call in killpg.call_args_list] == [(6789, 15), (6789, 9)]
    assert process.wait.call_count == 3


def test_config_runtime_type_checks_do_not_accept_float_as_integer() -> None:
    with pytest.raises(ValueError, match="viewport"):
        MermaidConfig("mmdc", "chrome", 1, 1, cast("int", 1.0), 1)
