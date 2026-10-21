"""CLI parity for exact DOCX and PPTX text edits and model text proposals."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from markweave.cli.commands import composer
from markweave.cli.commands.conversion_http import ConversionHttpResponse
from markweave.cli.main import main
from markweave.cli.types import ConnectionProfile
from tests.unit.test_cli_composer import (
    CONNECTION_ID,
    DRAFT_ID,
    PROPOSAL_ID,
    REVISION_ID,
    STEP_ID,
    _connection,
    _model_step,
)

pytestmark = pytest.mark.unit

_CAPABILITIES = {"maximum_model_request_bytes": 4096, "maximum_output_tokens": 32}
_REVISION = {"id": REVISION_ID, "number": 2, "operation": "docx_edit:x"}


@pytest.fixture
def remote(mocker):
    profile = ConnectionProfile(
        "default", "https://markweave.example", "session=opaque", "csrf-opaque"
    )
    store = mocker.Mock(load=mocker.Mock(return_value=profile))
    client = mocker.Mock()
    mocker.patch.object(composer, "ProfileStore", return_value=store)
    mocker.patch.object(composer, "ConversionHttpClient", return_value=client)
    return client


def _private(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


def _call(client, index: int) -> tuple[str, str, dict]:
    call = client.request.call_args_list[index]
    return call.args[0], call.args[1], call.kwargs


def test_targets_list_exact_revision_text_with_pagination(remote, capsys) -> None:
    remote.request.return_value = ConversionHttpResponse(
        200,
        {
            "targets": [{"target": "ppt/slides/slide1.xml#t0", "text": "Deck"}],
            "offset": 0,
            "limit": 50,
            "total": 1,
        },
    )

    assert (
        main(
            (
                "composer",
                "office",
                "targets",
                DRAFT_ID,
                REVISION_ID,
                "--format",
                "pptx",
            )
        )
        == 0
    )

    method, path, _kwargs = _call(remote, 0)
    assert method == "GET"
    assert path == (
        f"/api/v1/composer/drafts/{DRAFT_ID}/revisions/{REVISION_ID}"
        "/pptx-text-targets?offset=0&limit=50"
    )
    assert "ppt/slides/slide1.xml#t0" in capsys.readouterr().out


def test_edit_sends_reviewed_operations_from_a_private_file(
    remote, tmp_path, capsys
) -> None:
    operations = [
        {"target": "word/document.xml#t0", "expected_text": "Old", "text": "New"}
    ]
    path = _private(tmp_path / "edits.json", json.dumps(operations))
    remote.request.side_effect = (
        ConversionHttpResponse(200, _CAPABILITIES),
        ConversionHttpResponse(201, _REVISION),
    )

    assert (
        main(
            (
                "composer",
                "office",
                "edit",
                DRAFT_ID,
                REVISION_ID,
                "--format",
                "docx",
                "--operations-file",
                str(path),
                "--etag",
                '"draft-2"',
                "--idempotency-key",
                "edit-1",
            )
        )
        == 0
    )

    method, path_called, kwargs = _call(remote, 1)
    assert method == "POST"
    assert path_called.endswith(f"/revisions/{REVISION_ID}/docx-edits")
    assert kwargs["csrf"] is True
    assert kwargs["headers"]["If-Match"] == '"draft-2"'
    assert kwargs["headers"]["Idempotency-Key"] == "edit-1"
    assert json.loads(kwargs["body"]) == {
        "operations": [{"op": "replace_text", **operations[0]}]
    }
    assert "Composer text edits published." in capsys.readouterr().out


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        json.dumps({"target": "t"}),
        json.dumps([{"target": "t", "expected_text": "a"}]),
        json.dumps([{"target": "t", "expected_text": "a", "text": 1}]),
        json.dumps([{"target": "t", "expected_text": "a", "text": "b", "op": "x"}]),
    ],
)
def test_edit_rejects_malformed_operations_before_posting(
    remote, tmp_path, capsys, content: str
) -> None:
    path = _private(tmp_path / "edits.json", content)
    remote.request.return_value = ConversionHttpResponse(200, _CAPABILITIES)

    assert (
        main(
            (
                "composer",
                "office",
                "edit",
                DRAFT_ID,
                REVISION_ID,
                "--format",
                "docx",
                "--operations-file",
                str(path),
                "--etag",
                '"draft-2"',
                "--idempotency-key",
                "edit-1",
            )
        )
        == 1
    )
    assert remote.request.call_count == 1
    assert "edit operations are invalid" in capsys.readouterr().err


def test_edit_refuses_shared_and_oversized_files(remote, tmp_path, capsys) -> None:
    shared = tmp_path / "shared.json"
    shared.write_text("[]", encoding="utf-8")
    shared.chmod(0o644)
    large = _private(tmp_path / "large.json", "x" * 5000)
    remote.request.return_value = ConversionHttpResponse(200, _CAPABILITIES)
    arguments = (
        "composer",
        "office",
        "edit",
        DRAFT_ID,
        REVISION_ID,
        "--format",
        "docx",
        "--etag",
        '"draft-2"',
        "--idempotency-key",
        "edit-1",
        "--operations-file",
    )

    assert main((*arguments, str(shared))) == 1
    assert "Office edit files must be regular" in capsys.readouterr().err
    assert main((*arguments, str(large))) == 1
    assert "exceeds the service limit" in capsys.readouterr().err
    assert main((*arguments, str(tmp_path / "missing.json"))) == 1
    assert "office edit could not be read" in capsys.readouterr().err


def _propose_arguments(path: Path, *targets: str) -> tuple[str, ...]:
    return (
        "composer",
        "office",
        "propose",
        DRAFT_ID,
        REVISION_ID,
        CONNECTION_ID,
        *(item for target in targets for item in ("--target", target)),
        "--instruction-file",
        str(path),
        "--max-output-tokens",
        "16",
        "--etag",
        '"draft-2"',
        "--idempotency-key",
        "propose-1",
        "--force",
    )


def _preview(**overrides: object) -> dict[str, object]:
    return {
        "format": "docx",
        "base_revision_id": REVISION_ID,
        "base_sha256": "a" * 64,
        "transmitted_content": "Formal.\n\nTitle",
        "targets": [{"target": "word/document.xml#t0", "text": "Title"}],
        "preview_digest": "d" * 64,
        **overrides,
    }


def test_propose_previews_exact_text_then_starts_with_its_digest(
    remote, tmp_path, capsys
) -> None:
    instruction = _private(tmp_path / "instruction.txt", "Formal.")
    remote.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(200, _CAPABILITIES),
        ConversionHttpResponse(200, _preview()),
        ConversionHttpResponse(202, _model_step()),
    )

    assert main(_propose_arguments(instruction, "word/document.xml#t0")) == 0

    _method, preview_path, preview = _call(remote, 2)
    assert preview_path.endswith(f"/revisions/{REVISION_ID}/office-edit-steps/preview")
    assert preview["csrf"] is True
    sent = json.loads(preview["body"])
    assert sent == {
        "connection_id": CONNECTION_ID,
        "approved_endpoint": "https://llm.example/v1",
        "approved_model": "small-model",
        "instruction": "Formal.",
        "targets": ["word/document.xml#t0"],
        "max_output_tokens": 16,
    }
    _method, start_path, start = _call(remote, 3)
    assert start_path.endswith(f"/revisions/{REVISION_ID}/office-edit-steps")
    assert json.loads(start["body"]) == {**sent, "preview_digest": "d" * 64}
    assert start["headers"]["Idempotency-Key"] == "propose-1"
    assert f"Started Composer text proposal step {STEP_ID}." in (
        capsys.readouterr().out
    )


@pytest.mark.parametrize(
    "preview",
    [
        _preview(preview_digest="short"),
        _preview(transmitted_content=None),
        _preview(base_revision_id="00000000-0000-4000-8000-000000000999"),
    ],
)
def test_propose_rejects_inconsistent_previews_before_transmission(
    remote, tmp_path, preview: dict[str, object]
) -> None:
    instruction = _private(tmp_path / "instruction.txt", "Formal.")
    remote.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(200, _CAPABILITIES),
        ConversionHttpResponse(200, preview),
    )

    assert main(_propose_arguments(instruction, "word/document.xml#t0")) == 1
    assert remote.request.call_count == 3


def test_propose_validates_limits_targets_and_start_responses(
    remote, tmp_path, capsys
) -> None:
    instruction = _private(tmp_path / "instruction.txt", "Formal.")
    remote.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(200, {**_CAPABILITIES, "maximum_output_tokens": 8}),
    )
    assert main(_propose_arguments(instruction, "t0")) == 1
    assert "Output tokens exceed" in capsys.readouterr().err

    remote.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(200, _CAPABILITIES),
    )
    assert main(_propose_arguments(instruction, "t0", "t0")) == 1

    remote.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(200, _CAPABILITIES),
        ConversionHttpResponse(200, _preview()),
        ConversionHttpResponse(503, {"error": {"code": "DOWN"}}),
    )
    assert main(_propose_arguments(instruction, "t0")) == 1
    assert "model step request failed" in capsys.readouterr().err

    remote.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(200, _CAPABILITIES),
        ConversionHttpResponse(200, _preview()),
        ConversionHttpResponse(202, _model_step(model_identity="other-model")),
    )
    assert main(_propose_arguments(instruction, "t0")) == 1
    assert "invalid response" in capsys.readouterr().err


def test_propose_start_transport_failure_is_reported_safely(
    remote, tmp_path, capsys, mocker
) -> None:
    instruction = _private(tmp_path / "instruction.txt", "Formal.")
    responses = iter(
        (
            ConversionHttpResponse(200, _connection()),
            ConversionHttpResponse(200, _CAPABILITIES),
            ConversionHttpResponse(200, _preview()),
        )
    )

    def request(*_args: object, **_kwargs: object) -> ConversionHttpResponse:
        try:
            return next(responses)
        except StopIteration:
            raise composer.CliError("transport_failed", "raw") from None

    remote.request.side_effect = request
    assert main(_propose_arguments(instruction, "t0")) == 1
    assert "The model step could not be started." in capsys.readouterr().err


def test_publish_uses_the_office_proposal_route(remote, capsys) -> None:
    remote.request.return_value = ConversionHttpResponse(201, _REVISION)

    assert (
        main(
            (
                "composer",
                "office",
                "publish",
                DRAFT_ID,
                PROPOSAL_ID,
                "--etag",
                '"draft-4"',
                "--idempotency-key",
                "publish-1",
            )
        )
        == 0
    )

    _method, path, kwargs = _call(remote, 0)
    assert path == (
        f"/api/v1/composer/drafts/{DRAFT_ID}/proposals/{PROPOSAL_ID}/office-edits"
    )
    assert kwargs["headers"]["If-Match"] == '"draft-4"'
    assert "Composer text proposal published." in capsys.readouterr().out
