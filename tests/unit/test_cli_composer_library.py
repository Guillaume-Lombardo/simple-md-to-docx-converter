"""CLI parity for the cited reference library and cited model steps."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from markweave.cli.commands import composer
from markweave.cli.commands.conversion_http import ConversionHttpResponse
from markweave.cli.errors import CliError
from markweave.cli.main import main
from markweave.cli.types import ConnectionProfile
from markweave.composer.library_prompt import LIBRARY_EXCERPTS_MARKER
from tests.unit.test_cli_composer import (
    CONNECTION_ID,
    DRAFT_ID,
    _connection,
    _model_step,
)

pytestmark = pytest.mark.unit

_DOCUMENT_ID = "6b5ad0f3-2f2a-4f43-9d0c-0c2b0dbb2d11"
_VERSION_ID = "0c8f7f1e-5a25-4f6f-8c8b-0d4c2d9a7b31"
_USER_ID = "2f0d8c7a-6a43-4b0e-9a49-5e3c1f2b8d40"
_CAPABILITIES = {
    "maximum_model_request_bytes": 4096,
    "maximum_output_tokens": 32,
    "maximum_upload_bytes": 1024,
}
_DOCUMENT = {
    "id": _DOCUMENT_ID,
    "title": "Field notes",
    "version": 1,
    "current_version_id": _VERSION_ID,
}
_CITATION = {"version_id": _VERSION_ID, "chunk": 0}


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


def _call(client, index: int) -> tuple[str, str, dict]:
    call = client.request.call_args_list[index]
    return call.args[0], call.args[1], call.kwargs


def _content(tmp_path: Path) -> Path:
    path = tmp_path / "approved.txt"
    path.write_text("Summarize the findings.", encoding="utf-8")
    path.chmod(0o600)
    return path


def _start(tmp_path: Path, *citations: str) -> tuple[str, ...]:
    return (
        "--json",
        "--non-interactive",
        "composer",
        "model-steps",
        "start",
        DRAFT_ID,
        CONNECTION_ID,
        "--content-file",
        str(_content(tmp_path)),
        "--max-output-tokens",
        "16",
        "--etag",
        '"draft-2"',
        "--idempotency-key",
        "cited-1",
        "--force",
        *(item for citation in citations for item in ("--cite", citation)),
    )


def _preview(**overrides: object) -> dict[str, object]:
    transmitted = (
        "Summarize the findings.\n\n"
        + LIBRARY_EXCERPTS_MARKER
        + json.dumps([{"text": "Corrosion."}])
    )
    return {
        "transmitted_content": transmitted,
        "author_refs": [],
        "preview_digest": hashlib.sha256(transmitted.encode()).hexdigest(),
        "library_excerpts": [{**_CITATION, "text": "Corrosion."}],
        **overrides,
    }


def test_cli_marker_mirrors_the_server_prompt() -> None:
    assert composer._LIBRARY_EXCERPTS_MARKER == LIBRARY_EXCERPTS_MARKER


def test_cited_model_step_sends_reviewed_references_and_digest(
    remote, tmp_path
) -> None:
    preview = _preview()
    remote.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(200, _CAPABILITIES),
        ConversionHttpResponse(200, preview),
        ConversionHttpResponse(202, _model_step(library_refs=[_CITATION])),
    )

    assert main(_start(tmp_path, f"{_VERSION_ID}:0")) == 0

    _method, path, preview_call = _call(remote, 2)
    assert path.endswith("/model-steps/preview")
    assert json.loads(preview_call["body"])["library_refs"] == [_CITATION]
    assert json.loads(preview_call["body"])["author_ids"] == []
    started = json.loads(_call(remote, 3)[2]["body"])
    assert started["library_refs"] == [_CITATION]
    assert started["author_preview_digest"] == preview["preview_digest"]
    assert "author_refs" not in started


@pytest.mark.parametrize(
    "citations",
    [
        ("not-a-citation",),
        (f"{_VERSION_ID}:-1",),
        ("bad:0",),
        (f"{_VERSION_ID}:0", f"{_VERSION_ID}:0"),
        tuple(f"{_VERSION_ID}:{chunk}" for chunk in range(21)),
    ],
)
def test_invalid_citations_never_preview(remote, tmp_path, citations) -> None:
    remote.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(200, _CAPABILITIES),
    )

    assert main(_start(tmp_path, *citations)) != 0

    assert remote.request.call_count == 2


@pytest.mark.parametrize(
    ("preview", "step"),
    [
        (_preview(library_excerpts=[{"version_id": _VERSION_ID, "chunk": 1}]), None),
        (_preview(transmitted_content="Summarize the findings."), None),
        (_preview(), _model_step(library_refs=[])),
        (_preview(), _model_step(library_refs=[{"version_id": "x", "chunk": 0}])),
        (_preview(), _model_step(library_refs=[{**_CITATION, "chunk": -1}])),
        (_preview(), _model_step(library_refs={})),
    ],
)
def test_changed_preview_or_step_citations_fail_closed(
    remote, tmp_path, preview, step
) -> None:
    responses = [
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(200, _CAPABILITIES),
        ConversionHttpResponse(200, preview),
    ]
    if step is not None:
        responses.append(ConversionHttpResponse(202, step))
    remote.request.side_effect = tuple(responses)

    assert main(_start(tmp_path, f"{_VERSION_ID}:0")) != 0

    assert remote.request.call_count == len(responses)


def test_upload_sends_a_bounded_multipart_source_and_title(
    remote, tmp_path, capsys
) -> None:
    source = tmp_path / "notes.md"
    source.write_bytes(b"# Notes\n")
    remote.request.side_effect = (
        ConversionHttpResponse(200, _CAPABILITIES),
        ConversionHttpResponse(201, _DOCUMENT),
    )

    assert main(("composer", "library", "upload", str(source), "--title", "Notes")) == 0

    method, path, kwargs = _call(remote, 1)
    assert (method, path) == ("POST", "/api/v1/composer/library")
    body = kwargs["body"]
    assert b'name="title"\r\n\r\nNotes\r\n' in body
    assert b'filename="notes.md"\r\nContent-Type: text/markdown' in body
    assert b"# Notes\n" in body
    assert kwargs["csrf"] is True
    assert "Uploaded library document" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("filename", "title"),
    [("notes.pdf", None), ("notes.md", " "), ("notes.md", "a\nb")],
)
def test_invalid_uploads_are_refused_locally(remote, tmp_path, filename, title) -> None:
    source = tmp_path / filename
    source.write_bytes(b"# Notes\n")
    remote.request.return_value = ConversionHttpResponse(200, _CAPABILITIES)
    command = ["composer", "library", "upload", str(source)]
    if title is not None:
        command += ["--title", title]

    assert main(tuple(command)) != 0

    assert all(call.args[0] == "GET" for call in remote.request.call_args_list)


def test_replace_and_grants_send_the_reviewed_etag(remote, tmp_path) -> None:
    source = tmp_path / "notes.txt"
    source.write_bytes(b"Notes\n")
    remote.request.side_effect = (
        ConversionHttpResponse(200, _CAPABILITIES),
        ConversionHttpResponse(201, _DOCUMENT),
        ConversionHttpResponse(200, _DOCUMENT),
        ConversionHttpResponse(200, _DOCUMENT),
    )
    replace = ("composer", "library", "replace", _DOCUMENT_ID, str(source))
    assert main((*replace, "--etag", '"1"')) == 0
    for action in ("grant", "revoke"):
        command = ("composer", "library", action, _DOCUMENT_ID, _USER_ID)
        assert main((*command, "--etag", '"2"')) == 0

    calls = [_call(remote, index) for index in range(1, 4)]
    assert [(method, path) for method, path, _ in calls] == [
        ("POST", f"/api/v1/composer/library/{_DOCUMENT_ID}/versions"),
        ("PUT", f"/api/v1/composer/library/{_DOCUMENT_ID}/grants/{_USER_ID}"),
        ("DELETE", f"/api/v1/composer/library/{_DOCUMENT_ID}/grants/{_USER_ID}"),
    ]
    assert calls[0][2]["headers"]["If-Match"] == '"1"'
    assert b'filename="notes.txt"\r\nContent-Type: text/plain' in calls[0][2]["body"]


def test_reads_list_show_and_versions(remote, capsys) -> None:
    remote.request.side_effect = (
        ConversionHttpResponse(
            200, {"documents": [_DOCUMENT], "offset": 0, "limit": 50}
        ),
        ConversionHttpResponse(200, _DOCUMENT),
        ConversionHttpResponse(
            200,
            {
                "versions": [
                    {"id": _VERSION_ID, "number": 1, "chunk_count": 1, "sha256": "a"}
                ],
                "offset": 0,
                "limit": 50,
            },
        ),
    )

    assert main(("composer", "library", "list", "--query", "field notes")) == 0
    assert main(("composer", "library", "show", _DOCUMENT_ID)) == 0
    assert main(("composer", "library", "versions", _DOCUMENT_ID)) == 0

    assert _call(remote, 0)[1] == (
        "/api/v1/composer/library?query=field+notes&offset=0&limit=50"
    )
    assert _call(remote, 1)[1] == f"/api/v1/composer/library/{_DOCUMENT_ID}"
    assert "Field notes" in capsys.readouterr().out


def test_delete_requires_no_content(remote, capsys) -> None:
    remote.request.side_effect = (
        ConversionHttpResponse(204, None),
        ConversionHttpResponse(
            412, {"error": {"code": "COMPOSER_LIBRARY_CHANGED", "message": "x"}}
        ),
    )
    command = ("composer", "library", "delete", _DOCUMENT_ID, "--etag", '"3"')

    assert main(command) == 0
    assert main(command) != 0

    assert _call(remote, 0)[0] == "DELETE"
    assert "Library document deleted." in capsys.readouterr().out


def test_search_and_excerpts_return_exact_citations(remote, capsys) -> None:
    hit = {**_CITATION, "title": "Field notes", "snippet": "Corrosion."}
    excerpt = {**_CITATION, "title": "Field notes", "text": "Corrosion found."}
    remote.request.side_effect = (
        ConversionHttpResponse(200, {"results": [hit]}),
        ConversionHttpResponse(200, {"excerpts": [excerpt]}),
        ConversionHttpResponse(200, {"excerpts": [{**excerpt, "chunk": 1}]}),
    )

    assert main(("composer", "library", "search", "corrosion", "--limit", "5")) == 0
    excerpts = ("composer", "library", "excerpts", "--cite", f"{_VERSION_ID}:0")
    assert main(excerpts) == 0
    assert main(excerpts) != 0

    assert json.loads(_call(remote, 0)[2]["body"]) == {
        "query": "corrosion",
        "limit": 5,
    }
    assert json.loads(_call(remote, 1)[2]["body"]) == {"references": [_CITATION]}
    assert f"[library:{_VERSION_ID}#0]" in capsys.readouterr().out


def test_search_limit_is_bounded(remote) -> None:
    command = ("composer", "library", "search", "corrosion", "--limit", "21")

    assert main(command) != 0

    remote.request.assert_not_called()


def test_download_validates_library_headers(remote, tmp_path) -> None:
    remote.download.return_value = ConversionHttpResponse(200, None)
    output = tmp_path / "notes.md"
    command = ("composer", "library", "download", _DOCUMENT_ID, _VERSION_ID)

    assert main((*command, "--output", str(output))) == 0

    validate = remote.download.call_args.kwargs["validate_headers"]
    validate({"x-content-type-options": "nosniff", "content-type": "text/plain"})
    for headers in (
        {"x-content-type-options": "nosniff", "content-type": "text/html"},
        {"content-type": "text/plain"},
    ):
        with pytest.raises(CliError):
            validate(headers)
