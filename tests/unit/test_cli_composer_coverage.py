"""Failure and authorization behavior for owner-scoped Composer CLI commands."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from markweave.cli.commands import composer
from markweave.cli.commands.conversion_http import ConversionHttpResponse
from markweave.cli.main import main
from markweave.cli.types import ConnectionProfile

pytestmark = pytest.mark.unit

DRAFT_ID = "00000000-0000-4000-8000-000000000303"
OTHER_DRAFT_ID = "00000000-0000-4000-8000-000000000304"
REVISION_ID = "00000000-0000-4000-8000-000000000505"
OTHER_REVISION_ID = "00000000-0000-4000-8000-000000000506"
STEP_ID = "00000000-0000-4000-8000-000000000606"
CONNECTION_ID = "00000000-0000-4000-8000-000000000101"
QUESTION_ID = "00000000-0000-4000-8000-000000000707"


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


def _question_step(**overrides: object) -> dict[str, object]:
    return {
        "id": STEP_ID,
        "draft_id": DRAFT_ID,
        "connection_id": CONNECTION_ID,
        "model_identity": "small-model",
        "base_version": 2,
        "status": "completed",
        "intent": "question",
        "proposal_id": None,
        "question_id": QUESTION_ID,
        "answered_question_id": None,
        "error_code": None,
        "created_at": "2026-09-23T00:00:00Z",
        "updated_at": "2026-09-23T00:00:00Z",
        **overrides,
    }


def test_question_result_is_read_from_exact_owner_step_without_model_request(
    remote, capsys
) -> None:
    remote.request.return_value = ConversionHttpResponse(200, _question_step())

    assert main(("--json", "composer", "model-steps", "status", DRAFT_ID, STEP_ID)) == 0

    result = json.loads(capsys.readouterr().out)["model_step"]
    assert result["intent"] == "question"
    assert result["question_id"] == QUESTION_ID
    assert result["proposal_id"] is None
    remote.request.assert_called_once_with(
        "GET",
        f"/api/v1/composer/drafts/{DRAFT_ID}/model-steps/{STEP_ID}",
        csrf=False,
        headers={},
        body=None,
    )


def test_cross_owner_revision_and_question_status_fail_without_exposing_content(
    remote, capsys
) -> None:
    private_text = "private question and document content"
    remote.request.side_effect = (
        ConversionHttpResponse(
            404, {"error": {"code": "NOT_FOUND", "message": "Not found."}}
        ),
        ConversionHttpResponse(404, {"content": private_text}),
    )

    assert main(("--json", "composer", "revisions", "show", DRAFT_ID, REVISION_ID)) == 1
    revision_error = json.loads(capsys.readouterr().err)["error"]
    assert revision_error["code"] == "not_found"
    assert main(("--json", "composer", "model-steps", "status", DRAFT_ID, STEP_ID)) == 1
    step_error = capsys.readouterr()
    assert json.loads(step_error.err)["error"]["code"] == "model_step_read_failed"
    assert private_text not in step_error.out + step_error.err
    assert [call.args[1] for call in remote.request.call_args_list] == [
        f"/api/v1/composer/drafts/{DRAFT_ID}/revisions/{REVISION_ID}",
        f"/api/v1/composer/drafts/{DRAFT_ID}/model-steps/{STEP_ID}",
    ]


@pytest.mark.parametrize(
    "invalid",
    (
        {"draft_id": OTHER_DRAFT_ID},
        {"answered_question_id": "not-a-uuid"},
        {"question_id": None},
    ),
)
def test_question_step_identity_mismatch_fails_closed(remote, capsys, invalid) -> None:
    remote.request.return_value = ConversionHttpResponse(200, _question_step(**invalid))

    assert main(("--json", "composer", "model-steps", "status", DRAFT_ID, STEP_ID)) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err)["error"]["code"] == "response_invalid"


def test_revision_restore_conflict_requires_explicit_retry_with_new_etag(
    remote, capsys
) -> None:
    remote.request.side_effect = (
        ConversionHttpResponse(
            412,
            {
                "error": {
                    "code": "PRECONDITION_FAILED",
                    "message": "The draft changed.",
                }
            },
        ),
        ConversionHttpResponse(
            201,
            {"id": OTHER_REVISION_ID, "number": 5, "operation": "restore"},
        ),
    )
    command = (
        "--json",
        "composer",
        "revisions",
        "restore",
        DRAFT_ID,
        REVISION_ID,
        "--idempotency-key",
        "restore-after-review",
    )

    assert main((*command, "--etag", '"draft-v1"')) == 1
    assert json.loads(capsys.readouterr().err)["error"]["code"] == (
        "precondition_failed"
    )
    assert remote.request.call_count == 1
    assert main((*command, "--etag", '"draft-v2"')) == 0
    assert json.loads(capsys.readouterr().out)["revision"]["id"] == (OTHER_REVISION_ID)
    for call, etag in zip(
        remote.request.call_args_list, ('"draft-v1"', '"draft-v2"'), strict=True
    ):
        assert call.args == (
            "POST",
            f"/api/v1/composer/drafts/{DRAFT_ID}/revisions/{REVISION_ID}/restore",
        )
        assert call.kwargs["csrf"] is True
        assert call.kwargs["headers"]["If-Match"] == etag
        assert call.kwargs["headers"]["Idempotency-Key"] == "restore-after-review"
        assert json.loads(call.kwargs["body"]) == {}


def test_source_download_rejects_cross_revision_headers_before_reporting_success(
    remote, tmp_path, capsys
) -> None:
    output = tmp_path / "original-source.zip"

    def mismatched_artifact(_path, _destination, **options):
        options["validate_headers"](
            {
                "x-composer-revision": OTHER_REVISION_ID,
                "x-content-type-options": "nosniff",
            }
        )

    remote.download.side_effect = mismatched_artifact
    assert (
        main(
            (
                "--json",
                "composer",
                "revisions",
                "download",
                DRAFT_ID,
                REVISION_ID,
                "--kind",
                "source",
                "--output",
                str(output),
            )
        )
        == 1
    )
    error = json.loads(capsys.readouterr().err)["error"]
    assert error["code"] == "response_invalid"
    assert not output.exists()
    assert remote.download.call_args.args == (
        f"/api/v1/composer/drafts/{DRAFT_ID}/revisions/{REVISION_ID}/artifacts/source",
        output,
    )


def test_malformed_revision_diff_is_rejected_without_displaying_server_text(
    remote, capsys
) -> None:
    private_text = "private revision content"
    remote.request.return_value = ConversionHttpResponse(
        200, cast("dict[str, Any]", [private_text])
    )

    assert (
        main(
            (
                "--json",
                "composer",
                "revisions",
                "diff",
                DRAFT_ID,
                REVISION_ID,
                "--from-revision",
                OTHER_REVISION_ID,
            )
        )
        == 1
    )
    output = capsys.readouterr()
    assert output.out == ""
    assert private_text not in output.err
    assert json.loads(output.err)["error"]["code"] == "response_invalid"
