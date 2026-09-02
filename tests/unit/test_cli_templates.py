"""Unit coverage for the HTTP-only template CLI family."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import cast
from urllib.error import URLError

import pytest

from markweave.cli.commands import templates
from markweave.cli.main import main
from markweave.cli.output import OutputWriter
from markweave.cli.types import CommandContext, ConnectionProfile, OutputFormat

pytestmark = pytest.mark.unit

TEMPLATE_ID = "11111111-1111-4111-8111-111111111111"
VERSION_ID = "22222222-2222-4222-8222-222222222222"
OWNER_ID = "33333333-3333-4333-8333-333333333333"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class _Transport:
    def __init__(self, *responses: templates._Response) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, path: str, **kwargs):
        self.requests.append((method, path, kwargs))
        return self.responses.pop(0)


def _profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="default",
        service_url="https://converter.example",
        session_state="session=opaque",
        csrf_state="csrf-opaque",
    )


def _response(
    status: int,
    payload: object = None,
    *,
    headers: dict[str, str] | None = None,
    content: bytes = b"",
) -> templates._Response:
    return templates._Response(status, headers or {}, payload, content)


def _install(mocker, transport: _Transport) -> None:
    mocker.patch.object(
        templates,
        "ProfileStore",
        return_value=mocker.Mock(load=lambda name: _profile()),
    )
    mocker.patch.object(templates, "_TemplateTransport", return_value=transport)


def _identity(*, revision: int = 1, status: str = "active") -> dict[str, object]:
    return {
        "id": TEMPLATE_ID,
        "owner_id": OWNER_ID,
        "owner_username": "alice",
        "name": "Finance",
        "description": "Quarterly",
        "status": status,
        "revision": revision,
        "current_version_id": VERSION_ID,
    }


def test_list_and_search_forward_only_documented_filters(mocker, capsys) -> None:
    transport = _Transport(
        _response(
            200,
            {"items": [_identity()], "total": 1, "offset": 0, "limit": 5},
        ),
        _response(200, {"items": [], "total": 0, "offset": 0, "limit": 20}),
    )
    _install(mocker, transport)

    assert (
        main(
            (
                "--json",
                "templates",
                "list",
                "--owner-id",
                OWNER_ID,
                "--status",
                "active",
                "--limit",
                "5",
            )
        )
        == 0
    )
    assert main(("templates", "search", "--description", "quarter")) == 0

    first_path = transport.requests[0][1]
    assert first_path == (
        f"/api/v1/templates?owner_id={OWNER_ID}&status=active&limit=5"
    )
    assert transport.requests[1][1] == "/api/v1/templates?description=quarter"
    output = capsys.readouterr().out.splitlines()
    assert json.loads(output[0])["items"][0]["owner_id"] == OWNER_ID
    assert output[1] == "No templates."


def test_context_reads_authoritative_selection_and_upload_limit(mocker, capsys) -> None:
    transport = _Transport(
        _response(
            200,
            {
                "preferred_template_id": TEMPLATE_ID,
                "system_fallback_template_id": None,
                "template_max_archive_bytes": 654_321,
            },
        )
    )
    _install(mocker, transport)

    assert main(("--json", "templates", "context")) == 0
    assert transport.requests == [("GET", "/api/v1/template-context", {})]
    assert json.loads(capsys.readouterr().out) == {
        "template_context": {
            "preferred_template_id": TEMPLATE_ID,
            "system_fallback_template_id": None,
            "template_max_archive_bytes": 654_321,
        }
    }


def test_search_requires_an_explicit_remote_filter(mocker, capsys) -> None:
    transport = _Transport()
    _install(mocker, transport)

    assert main(("templates", "search")) == 1
    assert transport.requests == []
    assert capsys.readouterr().err == (
        "error: Search requires --name or --description.\n"
    )


def test_create_sends_fixed_multipart_filename_and_returns_etag(
    mocker, tmp_path: Path, capsys
) -> None:
    hostile = tmp_path / 'secret-"\r\nX-Leak: yes.docx'
    hostile.write_bytes(b"docx-content")
    etag = f'"template-{TEMPLATE_ID}-1"'
    transport = _Transport(_response(201, _identity(), headers={"etag": etag}))
    _install(mocker, transport)

    assert (
        main(
            (
                "--json",
                "templates",
                "create",
                "--name",
                "Finance",
                "--description",
                "Quarterly",
                "--file",
                str(hostile),
                "--font",
                "Calibri",
                "--font",
                "Cambria",
            )
        )
        == 0
    )

    request = transport.requests[0]
    body, boundary = cast(tuple[bytes, str], request[2]["multipart"])
    assert request[:2] == ("POST", "/api/v1/templates")
    assert request[2]["csrf"] is True
    assert b'filename="template.docx"' in body
    assert hostile.name.encode() not in body
    assert body.count(b'name="expected_fonts"') == 2
    assert boundary.encode() in body
    assert json.loads(capsys.readouterr().out)["etag"] == etag


def test_upload_rejects_symlinks_before_network_access(
    mocker, tmp_path: Path, capsys
) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(b"docx")
    link = tmp_path / "link.docx"
    link.symlink_to(source)
    transport = _Transport()
    _install(mocker, transport)

    assert (
        main(
            (
                "templates",
                "create",
                "--name",
                "Unsafe",
                "--file",
                str(link),
                "--font",
                "Calibri",
            )
        )
        == 1
    )
    assert transport.requests == []
    assert capsys.readouterr().err == ("error: The upload must be a regular file.\n")


def test_update_fetches_current_etag_or_forwards_explicit_stale_etag(
    mocker, capsys
) -> None:
    current = f'"template-{TEMPLATE_ID}-2"'
    stale = f'"template-{TEMPLATE_ID}-1"'
    transport = _Transport(
        _response(200, _identity(revision=2), headers={"etag": current}),
        _response(
            200, _identity(revision=3), headers={"etag": f'"template-{TEMPLATE_ID}-3"'}
        ),
        _response(
            412,
            {"error": {"code": "TEMPLATE_CONFLICT", "message": "Template changed."}},
        ),
    )
    _install(mocker, transport)

    arguments = (
        "templates",
        "update",
        TEMPLATE_ID,
        "--name",
        "Renamed",
        "--description",
        "Updated",
    )
    assert main(arguments) == 0
    assert main((*arguments, "--etag", stale)) == 1

    assert transport.requests[0][:2] == ("GET", f"/api/v1/templates/{TEMPLATE_ID}")
    assert transport.requests[1][2]["headers"] == {"If-Match": current}
    assert transport.requests[2][2]["headers"] == {"If-Match": stale}
    assert capsys.readouterr().err == "error: Template changed.\n"


def test_archive_and_delete_require_force_in_non_interactive_mode(
    mocker, capsys
) -> None:
    transport = _Transport()
    _install(mocker, transport)

    assert main(("--non-interactive", "templates", "archive", TEMPLATE_ID)) == 1
    assert main(("--non-interactive", "templates", "delete", TEMPLATE_ID)) == 1
    assert transport.requests == []
    assert capsys.readouterr().err.count("requires --force") == 2


def test_delete_with_force_uses_exact_etag_and_reports_identity(mocker, capsys) -> None:
    etag = f'"template-{TEMPLATE_ID}-4"'
    transport = _Transport(_response(204))
    _install(mocker, transport)

    assert (
        main(
            (
                "--json",
                "--non-interactive",
                "templates",
                "delete",
                TEMPLATE_ID,
                "--etag",
                etag,
                "--force",
            )
        )
        == 0
    )
    assert transport.requests == [
        (
            "DELETE",
            f"/api/v1/templates/{TEMPLATE_ID}",
            {"csrf": True, "headers": {"If-Match": etag}},
        )
    ]
    assert json.loads(capsys.readouterr().out) == {
        "id": TEMPLATE_ID,
        "status": "deleted",
    }


def test_download_verifies_digest_and_atomically_replaces_only_with_force(
    mocker, tmp_path: Path, capsys
) -> None:
    content = b"immutable-docx"
    digest = hashlib.sha256(content).hexdigest()
    response = _response(
        200,
        headers={"content-type": DOCX_TYPE, "etag": f'"sha256-{digest}"'},
        content=content,
    )
    output = tmp_path / "template.docx"
    output.write_bytes(b"preserved")
    transport = _Transport(response, response)
    _install(mocker, transport)

    arguments = (
        "templates",
        "download",
        TEMPLATE_ID,
        "--output",
        str(output),
    )
    assert main(arguments) == 1
    assert output.read_bytes() == b"preserved"
    assert main((*arguments, "--force")) == 0
    assert output.read_bytes() == content
    assert not list(tmp_path.glob(".template.docx.*"))
    captured = capsys.readouterr()
    assert "already exists" in captured.err


def test_download_without_force_never_overwrites_a_concurrently_created_target(
    mocker, tmp_path: Path, capsys
) -> None:
    content = b"immutable-docx"
    digest = hashlib.sha256(content).hexdigest()
    output = tmp_path / "template.docx"
    transport = _Transport(
        _response(
            200,
            headers={"content-type": DOCX_TYPE, "etag": f'"sha256-{digest}"'},
            content=content,
        )
    )
    _install(mocker, transport)
    real_link = os.link

    def publish_after_competitor(
        source,
        destination,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
        follow_symlinks=True,
    ):
        output.write_bytes(b"concurrent-writer")
        return real_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    mocker.patch("markweave.cli.commands.templates.os.link", publish_after_competitor)

    assert (
        main(
            (
                "templates",
                "download",
                TEMPLATE_ID,
                "--output",
                str(output),
            )
        )
        == 1
    )
    assert output.read_bytes() == b"concurrent-writer"
    assert not list(tmp_path.glob(".template.docx.*"))
    assert "already exists" in capsys.readouterr().err


def test_download_keeps_publication_and_cleanup_on_one_parent_directory(
    mocker, tmp_path: Path, capsys
) -> None:
    content = b"immutable-docx"
    digest = hashlib.sha256(content).hexdigest()
    parent = tmp_path / "download"
    parent.mkdir()
    moved_parent = tmp_path / "download-moved"
    output = parent / "result.docx"
    transport = _Transport(
        _response(
            200,
            headers={"content-type": DOCX_TYPE, "etag": f'"sha256-{digest}"'},
            content=content,
        )
    )
    _install(mocker, transport)
    real_link = os.link

    def publish_after_parent_replacement(
        source,
        destination,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
        follow_symlinks=True,
    ):
        parent.rename(moved_parent)
        parent.mkdir()
        (parent / "replacement-marker").write_bytes(b"replacement")
        return real_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    mocker.patch(
        "markweave.cli.commands.templates.os.link", publish_after_parent_replacement
    )

    assert (
        main(
            (
                "templates",
                "download",
                TEMPLATE_ID,
                "--output",
                str(output),
            )
        )
        == 0
    )
    assert (moved_parent / "result.docx").read_bytes() == content
    assert (parent / "replacement-marker").read_bytes() == b"replacement"
    assert not list(moved_parent.glob(".result.docx.*"))
    assert not list(parent.glob(".result.docx.*"))
    assert not capsys.readouterr().err


def test_integrity_failure_never_creates_download(
    mocker, tmp_path: Path, capsys
) -> None:
    output = tmp_path / "template.docx"
    transport = _Transport(
        _response(
            200,
            headers={"content-type": DOCX_TYPE, "etag": '"sha256-' + "0" * 64 + '"'},
            content=b"corrupted",
        )
    )
    _install(mocker, transport)

    assert (
        main(
            (
                "templates",
                "version-download",
                TEMPLATE_ID,
                VERSION_ID,
                "--output",
                str(output),
            )
        )
        == 1
    )
    assert not output.exists()
    assert "integrity" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("arguments", "method", "path"),
    (
        (
            ("templates", "preferred", "--template-id", TEMPLATE_ID),
            "PUT",
            f"/api/v1/templates/{TEMPLATE_ID}/preferred",
        ),
        (
            ("templates", "preferred", "--clear"),
            "DELETE",
            "/api/v1/template-preference",
        ),
        (
            ("templates", "fallback", TEMPLATE_ID),
            "PUT",
            f"/api/v1/templates/{TEMPLATE_ID}/system-fallback",
        ),
    ),
)
def test_preference_commands_use_only_authenticated_http(
    mocker, arguments, method, path
) -> None:
    transport = _Transport(_response(204))
    _install(mocker, transport)

    assert main(arguments) == 0
    assert transport.requests == [(method, path, {"csrf": True})]


def test_interactive_archive_can_be_confirmed_or_cancelled(mocker, capsys) -> None:
    etag = f'"template-{TEMPLATE_ID}-1"'
    transport = _Transport(
        _response(200, _identity(), headers={"etag": etag}),
        _response(200, _identity(status="archived"), headers={"etag": etag}),
    )
    _install(mocker, transport)
    prompt = mocker.patch("builtins.input", side_effect=("no", "yes"))

    assert main(("templates", "archive", TEMPLATE_ID)) == 1
    assert main(("templates", "archive", TEMPLATE_ID)) == 0
    assert prompt.call_count == 2
    assert transport.requests[0][:2] == ("GET", f"/api/v1/templates/{TEMPLATE_ID}")
    assert "Operation cancelled." in capsys.readouterr().err


@pytest.mark.parametrize(
    "etag",
    (
        "not-an-etag",
        f'"template-{TEMPLATE_ID}-wrong"',
        f'"template-{TEMPLATE_ID}-0"',
    ),
)
def test_malformed_etags_fail_locally(etag: str) -> None:
    with pytest.raises(templates.CliError, match="invalid"):
        templates._validate_etag(etag, TEMPLATE_ID)


def test_response_and_output_contract_reject_malformed_remote_data(
    tmp_path: Path,
) -> None:
    invalid_json = templates._decode_response(
        200, {"Content-Type": "application/json"}, b"not-json"
    )
    with pytest.raises(templates.CliError, match="invalid response"):
        templates._expect_json(invalid_json, 200)
    with pytest.raises(templates.CliError, match="invalid response"):
        templates._expect_list(_response(200, ["not-an-object"]), 200)
    with pytest.raises(templates.CliError, match="invalid response"):
        templates._required_header(_response(200), "etag")
    with pytest.raises(templates.CliError, match="invalid response"):
        templates._write_page(OutputWriter(OutputFormat.HUMAN), {"items": "not-a-list"})
    with pytest.raises(templates.CliError, match="invalid template document"):
        templates._download_to(
            OutputWriter(OutputFormat.HUMAN),
            templates._Command("download", {"output": tmp_path / "bad.docx"}),
            _response(200, headers={"content-type": "text/plain"}, content=b"bad"),
        )


def test_transport_sanitizes_network_failures(mocker) -> None:
    opener = mocker.Mock()
    opener.open.side_effect = URLError("private network detail")
    mocker.patch.object(templates, "build_opener", return_value=opener)
    transport = templates._TemplateTransport(_profile(), 1)

    with pytest.raises(templates.CliError, match="could not be reached"):
        transport.request("GET", "/api/v1/templates")


def test_confirmation_rejects_unavailable_input(mocker) -> None:
    mocker.patch("builtins.input", side_effect=EOFError)
    context = CommandContext(OutputFormat.HUMAN, False, None)
    with pytest.raises(templates.CliError, match="confirmation"):
        templates._confirm(context, templates._Command("delete"), "Confirm: ")


def test_local_bounds_reject_oversized_or_empty_data(mocker, tmp_path: Path) -> None:
    mocker.patch.object(templates, "_DOCUMENT_LIMIT", 3)
    with pytest.raises(templates.CliError, match="response is too large"):
        templates._decode_response(200, {}, b"1234")

    empty = tmp_path / "empty.docx"
    empty.touch()
    with pytest.raises(templates.CliError, match="empty"):
        templates._read_upload(empty)
    oversized = tmp_path / "oversized.docx"
    oversized.write_bytes(b"1234")
    with pytest.raises(templates.CliError, match="too large"):
        templates._read_upload(oversized)


def test_json_bound_is_stricter_than_the_document_bound(mocker) -> None:
    mocker.patch.object(templates, "_DOCUMENT_LIMIT", 10)
    mocker.patch.object(templates, "_JSON_RESPONSE_LIMIT", 3)
    with pytest.raises(templates.CliError, match="response is too large"):
        templates._decode_response(200, {"Content-Type": "application/json"}, b"1234")


def test_download_rejects_malformed_digest_etag(tmp_path: Path) -> None:
    with pytest.raises(templates.CliError, match="invalid template document"):
        templates._download_to(
            OutputWriter(OutputFormat.HUMAN),
            templates._Command("download", {"output": tmp_path / "bad.docx"}),
            _response(
                200,
                headers={"content-type": DOCX_TYPE, "etag": "not-a-digest"},
                content=b"bad",
            ),
        )


@pytest.mark.parametrize(
    "payload",
    (
        None,
        {"error": "not-an-object"},
        {"error": {"code": 42, "message": "malformed"}},
    ),
)
def test_unstructured_http_errors_use_one_safe_fallback(payload: object) -> None:
    with pytest.raises(templates.CliError, match="rejected"):
        templates._expect_status(_response(500, payload), 200)


@pytest.mark.parametrize(
    ("parser", "value"),
    (
        (templates._uuid, "not-a-uuid"),
        (templates._non_negative, "not-an-integer"),
        (templates._non_negative, "-1"),
        (templates._page_size, "101"),
    ),
)
def test_argument_types_reject_invalid_values(parser, value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parser(value)
