"""Unit coverage for HTTP-only conversion and job CLI commands."""

from __future__ import annotations

import io
import json
import os
from email.message import Message
from pathlib import Path
from typing import BinaryIO, cast
from urllib.error import HTTPError, URLError
from uuid import UUID

import pytest

from markweave.cli.commands import conversion_http, conversions
from markweave.cli.commands.conversion_http import (
    ConversionHttpClient,
    ConversionHttpResponse,
    _atomic_stream,
    _multipart_body,
    _validate_destination,
)
from markweave.cli.errors import CliError
from markweave.cli.main import build_parser, main
from markweave.cli.types import ConnectionProfile

pytestmark = pytest.mark.unit

JOB_ID = "11111111-1111-4111-8111-111111111111"
CORRELATION_ID = "22222222-2222-4222-8222-222222222222"
TEMPLATE_ID = "33333333-3333-4333-8333-333333333333"
VERSION_ID = "44444444-4444-4444-8444-444444444444"


@pytest.mark.parametrize("arguments", (("convert",), ("jobs", "wait")))
def test_conversion_commands_require_their_positional_operands(
    arguments: tuple[str, ...],
) -> None:
    with pytest.raises(SystemExit) as raised:
        build_parser().parse_args(arguments)

    assert raised.value.code == 2


def test_conversion_parser_keeps_request_values_local_to_each_parse() -> None:
    parser = build_parser()

    first = parser.parse_args(
        (
            "convert",
            "first.md",
            "--output",
            "pdf",
            "--template-id",
            TEMPLATE_ID,
            "--template-version-id",
            VERSION_ID,
        )
    )
    second = parser.parse_args(("convert", "second.md"))

    assert first.command_name.values == {
        "profile": "default",
        "output": "pdf",
        "retries": "0",
        "source": "first.md",
        "template_id": TEMPLATE_ID,
        "template_version_id": VERSION_ID,
    }
    assert second.command_name.values == {
        "profile": "default",
        "output": "docx",
        "retries": "0",
        "source": "second.md",
    }

    overwritten = parser.parse_args(
        ("jobs", "download", JOB_ID, "first.docx", "--overwrite")
    )
    safe_default = parser.parse_args(("jobs", "download", JOB_ID, "second.docx"))

    assert overwritten.command_name.values["overwrite"] is True
    assert safe_default.command_name.values["overwrite"] is False


def _job(state: str = "queued", *, progress: int = 0) -> dict[str, object]:
    return {
        "id": JOB_ID,
        "owner_id": "55555555-5555-4555-8555-555555555555",
        "template_mode": "pandoc-default",
        "template_id": None,
        "template_version_id": None,
        "output": "docx",
        "component_versions": [["markweave", "0.4.0"]],
        "correlation_id": CORRELATION_ID,
        "state": state,
        "step": state,
        "progress": progress,
        "created_at": "2026-08-30T00:00:00Z",
        "updated_at": "2026-08-30T00:00:00Z",
        "attempt": 0,
        "cancel_requested": False,
        "error_code": None,
        "error_message": None,
        "expires_at": None,
    }


def _response(
    status: int = 200,
    payload: dict[str, object] | None = None,
    **kwargs,
) -> ConversionHttpResponse:
    return ConversionHttpResponse(status, payload, **kwargs)


def test_conversion_options_reads_and_validates_authoritative_metadata(
    mocker, capsys
) -> None:
    client = mocker.Mock()
    identity = {"current_version_id": VERSION_ID}
    client.options.return_value = _response(
        payload={
            "conversion_upload_max_bytes": 321_123,
            "resolved_template": identity,
            "template_version_id": VERSION_ID,
            "selection_source": "preferred",
        }
    )
    mocker.patch.object(conversions, "_client", return_value=client)

    assert main(("--json", "conversion-options")) == 0
    payload = json.loads(capsys.readouterr().out)["conversion_options"]
    assert payload["conversion_upload_max_bytes"] == 321_123
    assert payload["resolved_template"] == identity
    assert payload["selection_source"] == "preferred"
    client.options.assert_called_once_with()


def test_convert_submits_canonical_source_and_reports_polling_without_filename(
    tmp_path: Path, mocker, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "private-customer-name.md"
    source.write_text("# Private body", encoding="utf-8")
    client = mocker.Mock()
    client.submit.return_value = _response(
        202, _job(), headers={"retry-after": "2", "x-correlation-id": CORRELATION_ID}
    )
    mocker.patch.object(conversions, "_client", return_value=client)

    assert (
        main(
            (
                "--json",
                "convert",
                str(source),
                "--output",
                "both",
                "--template-id",
                TEMPLATE_ID,
                "--template-version-id",
                VERSION_ID,
                "--idempotency-key",
                "stable-key",
            )
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    assert JOB_ID in captured.out
    assert '"poll_after_seconds":2' in captured.out
    assert '"idempotency_key":"stable-key"' in captured.out
    assert source.name not in captured.out + captured.err
    client.submit.assert_called_once_with(
        b"# Private body",
        source_kind="md",
        output="both",
        template_id=TEMPLATE_ID,
        template_version_id=VERSION_ID,
        idempotency_key="stable-key",
    )


def test_convert_retries_only_ambiguous_network_failures_with_explicit_key(
    tmp_path: Path, mocker
) -> None:
    source = tmp_path / "source.zip"
    source.write_bytes(b"PK fixture")
    client = mocker.Mock()
    client.submit.side_effect = (
        CliError("network_error", "The service could not be reached."),
        _response(202, _job(), headers={"retry-after": "1"}),
    )
    mocker.patch.object(conversions, "_client", return_value=client)

    assert (
        main(
            (
                "convert",
                str(source),
                "--idempotency-key",
                "retry-key",
                "--retries",
                "1",
            )
        )
        == 0
    )
    assert client.submit.call_count == 2
    assert main(("convert", str(source), "--retries", "1")) == 1
    assert client.submit.call_count == 2


@pytest.mark.parametrize(
    "failures, expected_calls",
    (
        ((CliError("quota_exceeded", "Quota exceeded."),), 1),
        (
            (
                CliError("network_error", "The service could not be reached."),
                CliError("network_error", "The service could not be reached."),
            ),
            2,
        ),
    ),
)
def test_convert_does_not_retry_definitive_or_exhausted_failures(
    failures: tuple[CliError, ...], expected_calls: int, tmp_path: Path, mocker
) -> None:
    source = tmp_path / "source.md"
    source.write_text("source", encoding="utf-8")
    client = mocker.Mock()
    client.submit.side_effect = failures
    mocker.patch.object(conversions, "_client", return_value=client)

    assert (
        main(
            (
                "convert",
                str(source),
                "--idempotency-key",
                "retry-key",
                "--retries",
                "1",
            )
        )
        == 1
    )
    assert client.submit.call_count == expected_calls


def test_conversion_source_read_stays_on_open_descriptor_during_symlink_swap(
    tmp_path: Path, mocker
) -> None:
    source = tmp_path / "source.md"
    source.write_bytes(b"anchored source")
    opened_source = tmp_path / "opened-source.md"
    replacement = tmp_path / "replacement.md"
    replacement.write_bytes(b"replacement secret")
    real_fstat = conversions.os.fstat
    swapped = False

    def swap_path_after_open(descriptor: int):
        nonlocal swapped
        if not swapped:
            swapped = True
            source.rename(opened_source)
            source.symlink_to(replacement)
        return real_fstat(descriptor)

    mocker.patch.object(conversions.os, "fstat", swap_path_after_open)

    assert conversions._read_source(source) == ("md", b"anchored source")
    assert source.read_bytes() == b"replacement secret"


@pytest.mark.parametrize(
    "arguments",
    (
        ("convert", "missing.md"),
        ("convert", "source.txt"),
        ("convert", "source.md", "--template-id", TEMPLATE_ID),
        (
            "convert",
            "source.md",
            "--template-id",
            "invalid",
            "--template-version-id",
            VERSION_ID,
        ),
        ("convert", "source.md", "--idempotency-key", "bad key"),
        ("convert", "source.md", "--retries", "6", "--idempotency-key", "key"),
        ("jobs", "show", "invalid"),
        ("jobs", "list", "--limit", "101"),
        ("jobs", "list", "--limit", "many"),
    ),
)
def test_invalid_local_inputs_fail_before_http(
    arguments: tuple[str, ...], tmp_path: Path, monkeypatch, mocker
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "source.txt").write_text("source", encoding="utf-8")
    (tmp_path / "source.md").write_text("source", encoding="utf-8")
    client = mocker.Mock()
    mocker.patch.object(conversions, "_client", return_value=client)

    assert main(arguments) == 1
    client.assert_not_called()


def test_source_rejects_empty_files_and_symlinks(tmp_path: Path, mocker) -> None:
    empty = tmp_path / "empty.md"
    empty.touch()
    target = tmp_path / "target.md"
    target.write_text("source", encoding="utf-8")
    symlink = tmp_path / "link.md"
    symlink.symlink_to(target)
    client = mocker.Mock()
    mocker.patch.object(conversions, "_client", return_value=client)

    assert main(("convert", str(empty))) == 1
    assert main(("convert", str(symlink))) == 1
    client.assert_not_called()


def test_source_rejects_a_non_regular_file(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    os.mkfifo(source)

    with pytest.raises(CliError, match="unsafe"):
        conversions._read_source(source)


def test_list_show_cancel_and_wait_use_only_family_http_client(
    mocker, capsys: pytest.CaptureFixture[str]
) -> None:
    client = mocker.Mock()
    client.list_jobs.return_value = _response(
        payload={"items": [_job()], "total": 1, "offset": 0, "limit": 10}
    )
    client.get_job.side_effect = (
        _response(payload=_job()),
        _response(payload=_job("running", progress=50)),
        _response(payload=_job("succeeded", progress=100)),
    )
    client.cancel_job.return_value = _response(payload=_job("cancelled"))
    mocker.patch.object(conversions, "_client", return_value=client)
    mocker.patch.object(conversions.time, "sleep")

    assert main(("--json", "jobs", "list", "--limit", "10")) == 0
    assert main(("jobs", "show", JOB_ID)) == 0
    assert main(("jobs", "cancel", JOB_ID)) == 0
    assert (
        main(("--timeout", "5", "jobs", "wait", JOB_ID, "--poll-interval", "0.1")) == 0
    )
    captured = capsys.readouterr()
    assert "Listed 1 of 1 jobs." not in captured.out
    assert "succeeded (100%)" in captured.out
    client.list_jobs.assert_called_once_with(offset=0, limit=10)
    client.cancel_job.assert_called_once_with(JOB_ID)
    assert client.get_job.call_count == 3


def test_wait_is_bounded_and_preserves_terminal_safe_failures(mocker, capsys) -> None:
    client = mocker.Mock()
    failed = _job("failed", progress=100)
    failed["error_code"] = "CONVERSION_FAILED"
    failed["error_message"] = "The document could not be converted."
    client.get_job.return_value = _response(payload=failed)
    mocker.patch.object(conversions, "_client", return_value=client)

    assert main(("jobs", "wait", JOB_ID)) == 1
    assert main(("--timeout", "2", "jobs", "wait", JOB_ID)) == 1
    captured = capsys.readouterr()
    assert "bounded --timeout" in captured.err
    assert "The document could not be converted." in captured.err

    client.get_job.return_value = _response(payload=_job("cancelled"))
    assert main(("--timeout", "2", "jobs", "wait", JOB_ID)) == 1
    client.get_job.return_value = _response(payload=_job("expired"))
    assert main(("--timeout", "2", "jobs", "wait", JOB_ID)) == 1

    client.get_job.return_value = _response(payload=_job("failed", progress=100))
    assert main(("--timeout", "2", "jobs", "wait", JOB_ID)) == 1
    assert "The job failed." in capsys.readouterr().err


def test_server_errors_preserve_safe_message_and_correlation_id(mocker, capsys) -> None:
    client = mocker.Mock()
    client.get_job.return_value = _response(
        404,
        {"error": {"code": "JOB_NOT_FOUND", "message": "Job not found."}},
        headers={"x-correlation-id": CORRELATION_ID},
    )
    mocker.patch.object(conversions, "_client", return_value=client)

    assert main(("--json", "jobs", "show", JOB_ID)) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert '"code":"job_not_found"' in captured.err
    assert CORRELATION_ID in captured.err


def test_download_commands_report_bytes_and_never_print_destination(
    tmp_path: Path, mocker, capsys
) -> None:
    client = mocker.Mock()
    client.download_result.return_value = _response(
        bytes_written=8, headers={"x-correlation-id": CORRELATION_ID}
    )
    client.download_manifest.return_value = _response(bytes_written=10)
    mocker.patch.object(conversions, "_client", return_value=client)
    private = tmp_path / "private-result.docx"

    assert main(("--json", "jobs", "download", JOB_ID, str(private))) == 0
    assert main(("jobs", "manifest", JOB_ID, str(tmp_path / "manifest.json"))) == 0
    captured = capsys.readouterr()
    assert '"bytes":8' in captured.out
    assert CORRELATION_ID in captured.out
    assert private.name not in captured.out + captured.err


def test_multipart_uses_only_canonical_source_name() -> None:
    body, content_type = _multipart_body(
        b"private content",
        source_kind="md",
        output="pdf",
        template_id=TEMPLATE_ID,
        template_version_id=VERSION_ID,
    )
    assert content_type.startswith("multipart/form-data; boundary=markweave-")
    assert b'filename="source.md"' in body
    assert b"private content" in body
    assert TEMPLATE_ID.encode() in body and VERSION_ID.encode() in body


def test_multipart_replaces_a_boundary_that_occurs_in_the_source(mocker) -> None:
    first = mocker.Mock(hex="collision")
    second = mocker.Mock(hex="safe")
    mocker.patch.object(conversion_http, "uuid4", side_effect=(first, second))

    body, content_type = _multipart_body(
        b"private markweave-collision content",
        source_kind="zip",
        output="docx",
        template_id=None,
        template_version_id=None,
    )

    assert content_type.endswith("boundary=markweave-safe")
    assert b'filename="source.zip"' in body


def test_atomic_stream_refuses_clobber_and_supports_explicit_regular_overwrite(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "result.bin"
    assert _atomic_stream(io.BytesIO(b"first"), destination, overwrite=False) == 5
    assert destination.read_bytes() == b"first"
    assert destination.stat().st_mode & 0o777 == 0o600

    with pytest.raises(CliError, match="already exists"):
        _validate_destination(destination, overwrite=False)
    _validate_destination(destination, overwrite=True)
    assert _atomic_stream(io.BytesIO(b"second"), destination, overwrite=True) == 6
    assert destination.read_bytes() == b"second"


def test_atomic_stream_cleans_partial_file_after_interruption(tmp_path: Path) -> None:
    class Interrupted:
        calls = 0

        def read(self, _size: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"partial"
            raise OSError("interrupted")

    destination = tmp_path / "result.bin"
    with pytest.raises(OSError):
        _atomic_stream(cast(BinaryIO, Interrupted()), destination, overwrite=False)
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("cleanup_failure", ("unlink", "fsync"))
def test_atomic_stream_closes_owned_parent_after_cleanup_failure(
    cleanup_failure: str, tmp_path: Path, mocker
) -> None:
    class Interrupted:
        def read(self, _size: int) -> bytes:
            raise RuntimeError("primary stream failure")

    opened_descriptors: list[int] = []
    real_open_destination = conversion_http._open_destination
    real_unlink = os.unlink
    real_fsync = os.fsync

    def record_open(destination: Path, *, overwrite: bool) -> int:
        descriptor = real_open_destination(destination, overwrite=overwrite)
        opened_descriptors.append(descriptor)
        return descriptor

    def fail_unlink(path, *, dir_fd=None) -> None:
        raise PermissionError("cleanup unlink failure")

    def fail_directory_fsync(descriptor: int) -> None:
        if descriptor in opened_descriptors:
            raise OSError("cleanup fsync failure")
        real_fsync(descriptor)

    mocker.patch.object(conversion_http, "_open_destination", record_open)
    if cleanup_failure == "unlink":
        mocker.patch.object(conversion_http.os, "unlink", fail_unlink)
    else:
        mocker.patch.object(conversion_http.os, "fsync", fail_directory_fsync)

    destination = tmp_path / "result.bin"
    with pytest.raises(RuntimeError, match="primary stream failure"):
        _atomic_stream(cast(BinaryIO, Interrupted()), destination, overwrite=False)

    assert len(opened_descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(opened_descriptors[0])
    temporary_files = list(tmp_path.glob(".markweave-download-*"))
    if cleanup_failure == "unlink":
        assert len(temporary_files) == 1
        real_unlink(temporary_files[0])
    else:
        assert temporary_files == []


def test_cleanup_helpers_preserve_first_error_and_attempt_all_closes(mocker) -> None:
    response = mocker.Mock()
    response_error = RuntimeError("response close failure")
    response.close.side_effect = response_error
    close = mocker.patch.object(
        conversion_http.os,
        "close",
        side_effect=(OSError("directory close failure"),),
    )

    with pytest.raises(RuntimeError, match="response close failure") as raised:
        conversion_http._close_download_resources(response, 41, primary_error=None)

    assert raised.value is response_error
    close.assert_called_once_with(41)

    close.reset_mock(side_effect=True)
    conversion_http._close_download_resources(
        response,
        41,
        primary_error=RuntimeError("primary request failure"),
    )
    close.assert_called_once_with(41)

    first_close_error = OSError("file close failure")
    close.reset_mock(side_effect=True)
    close.side_effect = (first_close_error, OSError("directory close failure"))
    with pytest.raises(OSError, match="file close failure") as raised:
        conversion_http._cleanup_atomic_resources(
            descriptor=42,
            temporary="",
            directory_descriptor=43,
            close_directory=True,
            primary_error=None,
        )

    assert raised.value is first_close_error
    assert close.call_args_list == [mocker.call(42), mocker.call(43)]


def test_cleanup_helpers_cover_absent_resources_and_late_failures(mocker) -> None:
    directory_close_error = OSError("directory close failure")
    close = mocker.patch.object(
        conversion_http.os, "close", side_effect=directory_close_error
    )

    with pytest.raises(OSError, match="directory close failure") as raised:
        conversion_http._close_download_resources(None, 41, primary_error=None)

    assert raised.value is directory_close_error
    close.assert_called_once_with(41)

    close.reset_mock(side_effect=True)
    unlink = mocker.patch.object(
        conversion_http.os, "unlink", side_effect=FileNotFoundError
    )
    fsync_error = OSError("directory sync failure")
    fsync = mocker.patch.object(conversion_http.os, "fsync", side_effect=fsync_error)

    conversion_http._cleanup_atomic_resources(
        descriptor=-1,
        temporary="missing",
        directory_descriptor=42,
        close_directory=False,
        primary_error=RuntimeError("primary stream failure"),
    )

    close.assert_not_called()
    unlink.assert_called_once_with("missing", dir_fd=42)
    fsync.assert_called_once_with(42)


def test_atomic_stream_keeps_publication_and_cleanup_on_open_parent_descriptor(
    tmp_path: Path, mocker
) -> None:
    parent = tmp_path / "download"
    parent.mkdir()
    moved_parent = tmp_path / "download-moved"
    destination = parent / "result.bin"
    real_link = os.link

    def publish_after_parent_replacement(
        source,
        target,
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
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    mocker.patch.object(conversion_http.os, "link", publish_after_parent_replacement)

    assert _atomic_stream(io.BytesIO(b"anchored"), destination, overwrite=False) == 8
    assert (moved_parent / "result.bin").read_bytes() == b"anchored"
    assert (parent / "replacement-marker").read_bytes() == b"replacement"
    assert not list(moved_parent.glob(".markweave-download-*"))
    assert not list(parent.glob(".markweave-download-*"))


def test_download_destination_rejects_symlinks_and_non_directories(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"target")
    symlink = tmp_path / "link"
    symlink.symlink_to(target)
    with pytest.raises(CliError, match="unsafe"):
        _validate_destination(symlink, overwrite=True)

    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(CliError, match="invalid"):
        _validate_destination(parent_link / "result", overwrite=False)


def test_response_correlation_accepts_only_canonical_uuid() -> None:
    assert (
        _response(headers={"x-correlation-id": CORRELATION_ID}).correlation_id
        == CORRELATION_ID
    )
    assert (
        _response(headers={"x-correlation-id": "private source name"}).correlation_id
        is None
    )
    assert UUID(CORRELATION_ID).version == 4


class _FakeHttpResponse:
    def __init__(
        self,
        content: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        failure: Exception | None = None,
        close_failure: Exception | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._content = content
        self._failure = failure
        self._close_failure = close_failure
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if self._failure is not None:
            raise self._failure
        return self._content if size < 0 else self._content[:size]

    def close(self) -> None:
        self.closed = True
        if self._close_failure is not None:
            raise self._close_failure


def _http_client() -> ConversionHttpClient:
    return ConversionHttpClient(
        ConnectionProfile(
            "default",
            "http://127.0.0.1:8080",
            "session=opaque",
            "csrf-opaque",
        ),
        timeout=2,
    )


@pytest.mark.parametrize("idempotency_key", (None, "stable-key"))
def test_conversion_http_submit_forwards_only_an_explicit_idempotency_key(
    idempotency_key: str | None, mocker
) -> None:
    client = _http_client()
    request = mocker.patch.object(client, "request", return_value=_response(202))

    client.submit(
        b"source",
        source_kind="md",
        output="docx",
        template_id=None,
        template_version_id=None,
        idempotency_key=idempotency_key,
    )

    headers = request.call_args.kwargs["headers"]
    assert headers.get("Idempotency-Key") == idempotency_key


def test_conversion_http_request_binds_api_cookie_csrf_and_bounded_json(mocker) -> None:
    response = _FakeHttpResponse(
        b'{"id":"value"}', headers={"X-Correlation-ID": CORRELATION_ID}
    )
    opener = mocker.Mock()
    opener.open.return_value = response
    mocker.patch.object(conversion_http, "build_opener", return_value=opener)

    result = _http_client().request("DELETE", "/api/v1/conversions/one", csrf=True)

    assert result.payload == {"id": "value"}
    assert result.correlation_id == CORRELATION_ID
    assert response.closed is True
    request = opener.open.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:8080/api/v1/conversions/one"
    assert request.get_header("Cookie") == "session=opaque"
    assert request.get_header("X-csrf-token") == "csrf-opaque"
    assert opener.open.call_args.kwargs == {"timeout": 2}


def test_conversion_https_client_returns_bounded_http_error_payload(mocker) -> None:
    headers = Message()
    headers["X-Correlation-ID"] = CORRELATION_ID
    response = HTTPError(
        "https://service.example/api/v1/conversions",
        429,
        "Too Many Requests",
        headers,
        io.BytesIO(b'{"error":{"code":"QUEUE_FULL","message":"Queue full."}}'),
    )
    opener = mocker.Mock()
    opener.open.side_effect = response
    build_opener = mocker.patch.object(
        conversion_http, "build_opener", return_value=opener
    )
    client = ConversionHttpClient(
        ConnectionProfile(
            "default",
            "https://service.example",
            "session=opaque",
            "csrf-opaque",
        ),
        timeout=2,
    )

    result = client.request("POST", "/api/v1/conversions")

    assert result.status == 429
    assert result.payload == {"error": {"code": "QUEUE_FULL", "message": "Queue full."}}
    assert result.correlation_id == CORRELATION_ID
    assert len(build_opener.call_args.args) == 2


def test_conversion_http_rejects_non_api_paths_network_and_oversized_json(
    mocker,
) -> None:
    client = _http_client()
    with pytest.raises(CliError, match="path is invalid"):
        client.request("GET", "/health/live")

    opener = mocker.Mock()
    opener.open.side_effect = URLError("offline")
    mocker.patch.object(conversion_http, "build_opener", return_value=opener)
    with pytest.raises(CliError, match="could not be reached"):
        client.get_job(JOB_ID)

    response = _FakeHttpResponse(b"x" * (conversion_http._MAX_JSON_BYTES + 1))
    opener.open.side_effect = None
    opener.open.return_value = response
    with pytest.raises(CliError, match="too large"):
        client.get_job(JOB_ID)
    assert response.closed is True


@pytest.mark.parametrize("content", (b"", b"not-json", b"[]", b'"text"'))
def test_conversion_http_invalid_json_is_not_promoted_to_payload(
    content: bytes, mocker
) -> None:
    response = _FakeHttpResponse(content)
    opener = mocker.Mock()
    opener.open.return_value = response
    mocker.patch.object(conversion_http, "build_opener", return_value=opener)
    assert _http_client().get_job(JOB_ID).payload is None


def test_conversion_http_error_download_is_bounded_and_does_not_create_file(
    tmp_path: Path, mocker
) -> None:
    response = _FakeHttpResponse(
        b'{"error":{"code":"JOB_EXPIRED","message":"Expired."}}', status=409
    )
    opener = mocker.Mock()
    opener.open.return_value = response
    mocker.patch.object(conversion_http, "build_opener", return_value=opener)
    destination = tmp_path / "result.bin"

    result = _http_client().download_result(JOB_ID, destination, overwrite=False)

    assert result.status == 409
    assert result.payload == {"error": {"code": "JOB_EXPIRED", "message": "Expired."}}
    assert not destination.exists()
    assert response.closed is True


def test_download_closes_parent_fd_when_response_close_repeatedly_fails(
    tmp_path: Path, mocker
) -> None:
    descriptor_directory = Path("/proc/self/fd")
    assert descriptor_directory.is_dir()
    opener = mocker.Mock()
    opener.open.side_effect = lambda *_args, **_kwargs: _FakeHttpResponse(
        b'{"error":{"code":"JOB_EXPIRED","message":"Expired."}}',
        status=409,
        close_failure=RuntimeError("response close failure"),
    )
    mocker.patch.object(conversion_http, "build_opener", return_value=opener)
    destination = tmp_path / "result.bin"
    descriptors_before = len(tuple(descriptor_directory.iterdir()))

    for _ in range(32):
        with pytest.raises(RuntimeError, match="response close failure"):
            _http_client().download_result(JOB_ID, destination, overwrite=False)

    assert len(tuple(descriptor_directory.iterdir())) == descriptors_before
    assert not destination.exists()


def test_conversion_http_interrupted_download_is_sanitized_and_cleans_temp(
    tmp_path: Path, mocker
) -> None:
    response = _FakeHttpResponse(b"", failure=OSError("private transport detail"))
    opener = mocker.Mock()
    opener.open.return_value = response
    mocker.patch.object(conversion_http, "build_opener", return_value=opener)
    destination = tmp_path / "result.bin"

    with pytest.raises(CliError, match="could not be completed"):
        _http_client().download_result(JOB_ID, destination, overwrite=False)
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []
    assert response.closed is True


def test_invalid_service_payloads_and_download_errors_fail_stably(
    mocker, capsys
) -> None:
    client = mocker.Mock()
    mocker.patch.object(conversions, "_client", return_value=client)
    client.list_jobs.return_value = _response(payload={"items": "bad", "total": 1})
    assert main(("jobs", "list")) == 1
    client.get_job.return_value = _response(payload={"id": JOB_ID})
    assert main(("jobs", "show", JOB_ID)) == 1
    client.download_result.return_value = _response(
        409, {"error": {"code": "JOB_EXPIRED", "message": "Expired."}}
    )
    assert main(("jobs", "download", JOB_ID, "result.bin")) == 1
    assert "Expired." in capsys.readouterr().err


def test_wait_timeout_is_rechecked_after_the_http_request(mocker) -> None:
    client = mocker.Mock()
    client.get_job.return_value = _response(payload=_job("running", progress=50))
    client_factory = mocker.patch.object(conversions, "_client", return_value=client)
    mocker.patch.object(conversions.time, "monotonic", side_effect=(0.0, 0.1, 2.0))

    assert main(("--timeout", "1", "jobs", "wait", JOB_ID)) == 1
    client_factory.assert_called_once()
