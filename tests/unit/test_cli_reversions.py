"""Unit coverage for the HTTP-only reverse-job CLI family."""

from __future__ import annotations

import io
import json
import os
from http.client import IncompleteRead
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

import pytest

from markweave.cli.commands import conversion_http, reversions
from markweave.cli.commands.conversion_http import ConversionHttpResponse
from markweave.cli.errors import CliError
from markweave.cli.main import build_parser, main

pytestmark = pytest.mark.unit

JOB_ID = "11111111-1111-4111-8111-111111111111"
OWNER_ID = "22222222-2222-4222-8222-222222222222"
CORRELATION_ID = "33333333-3333-4333-8333-333333333333"


class _DownloadResponse(io.BytesIO):
    status = 200
    headers: ClassVar[dict[str, str]] = {}


def _capabilities(*, maximum: int = 32) -> dict[str, object]:
    return {
        "schema_version": 1,
        "format_families": [
            {
                "family": "word",
                "extensions": [".docx"],
                "detected_formats": ["docx"],
                "content_detection": "OPC",
                "selected_parser_format": None,
            },
            {
                "family": "csv",
                "extensions": [".csv"],
                "detected_formats": [],
                "content_detection": "bounded text",
                "selected_parser_format": "csv",
            },
        ],
        "admission": {
            "extension_is_hint": True,
            "mismatch_policy": "reject",
            "undetected_policy": "reject except CSV",
            "csv_policy": "bounded text",
            "scanner_order": "scan first",
        },
        "maximum_upload_bytes": maximum,
        "result_package_modes": ["markdown", "markdown_with_assets"],
        "pdf": {
            "contract": "text extraction only",
            "document_model_available": False,
            "embedded_assets_available": False,
            "image_preservation": False,
            "mixed_or_image_only_pages": "reject",
            "warning": "not preserved",
        },
        "execution": {"local": True, "ocr": False, "hosted_fallback": False},
    }


def _job(state: str = "queued", *, step: str | None = None) -> dict[str, object]:
    return {
        "id": JOB_ID,
        "owner_id": OWNER_ID,
        "source_stem": "private-name",
        "source_family": "word",
        "source_extension": ".docx",
        "detected_format": "docx",
        "component_versions": [["firecrawl-anydoc", "0.2.4"]],
        "correlation_id": CORRELATION_ID,
        "state": state,
        "step": step or ("complete" if state == "succeeded" else state),
        "created_at": "2026-09-06T00:00:00Z",
        "updated_at": "2026-09-06T00:00:00Z",
        "attempt": 0,
        "cancel_requested": state == "cancelled",
        "result_mode": "markdown" if state == "succeeded" else None,
        "result_size": 8 if state == "succeeded" else None,
        "error_code": None,
        "error_message": None,
        "expires_at": None,
    }


def _response(
    status: int = 200,
    payload: dict[str, object] | None = None,
    *,
    retry_after: str | None = None,
    bytes_written: int | None = None,
) -> ConversionHttpResponse:
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    return ConversionHttpResponse(
        status, payload, headers=headers, bytes_written=bytes_written
    )


def test_reverse_parser_is_nested_without_changing_the_root_registry() -> None:
    parser = build_parser()
    request = parser.parse_args(
        (
            "jobs",
            "reverse",
            "submit",
            "source.docx",
            "--idempotency-key",
            "stable-key",
            "--retries",
            "2",
            "--profile",
            "work",
        )
    ).command_name
    assert request.command == "jobs reverse submit"
    assert request.values == {
        "profile": "work",
        "retries": "2",
        "source": "source.docx",
        "idempotency_key": "stable-key",
    }

    overwritten = parser.parse_args(
        ("jobs", "reverse", "download", JOB_ID, "result.md", "--overwrite")
    ).command_name
    safe = parser.parse_args(
        ("jobs", "reverse", "download", JOB_ID, "result.zip")
    ).command_name
    assert overwritten.values["overwrite"] is True
    assert safe.values["overwrite"] is False


@pytest.mark.parametrize(
    "arguments",
    (
        ("jobs", "reverse", "submit"),
        ("jobs", "reverse", "show"),
        ("jobs", "reverse", "download", JOB_ID),
    ),
)
def test_reverse_commands_require_positional_operands(
    arguments: tuple[str, ...],
) -> None:
    with pytest.raises(SystemExit) as raised:
        build_parser().parse_args(arguments)
    assert raised.value.code == 2


def test_capabilities_are_validated_and_rendered(mocker, capsys) -> None:
    client = mocker.Mock()
    client.reversion_capabilities.return_value = _response(payload=_capabilities())
    mocker.patch.object(reversions, "_client", return_value=client)

    assert main(("--json", "jobs", "reverse", "capabilities")) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["reversion_capabilities"]["maximum_upload_bytes"] == 32
    client.reversion_capabilities.assert_called_once_with()


@pytest.mark.parametrize(
    "mutation",
    (
        {"schema_version": True},
        {"maximum_upload_bytes": 0},
        {"format_families": []},
        {"result_package_modes": ["invented"]},
        {"admission": None},
        {"pdf": None},
        {"execution": {"local": False, "ocr": False, "hosted_fallback": False}},
        {"format_families": [None]},
        {"format_families": [{"family": "word", "extensions": []}]},
        {"format_families": [{"family": "word", "extensions": ["DOCX"]}]},
        {
            "format_families": [
                {"family": "word", "extensions": [".docx"]},
                {"family": "other", "extensions": [".docx"]},
            ]
        },
    ),
)
def test_capabilities_reject_malformed_contract(mutation: dict[str, object]) -> None:
    value = {**_capabilities(), **mutation}
    with pytest.raises(CliError, match="invalid response"):
        reversions._validated_capabilities(value)


def test_submit_preflights_capabilities_and_retries_same_request(
    tmp_path: Path, mocker, capsys
) -> None:
    source = tmp_path / "private-customer.docx"
    source.write_bytes(b"PK\x03\x04document")
    client = mocker.Mock()
    client.reversion_capabilities.return_value = _response(
        payload=_capabilities(maximum=64)
    )
    client.submit_reversion.side_effect = [
        CliError("network_error", "unavailable"),
        _response(202, _job(), retry_after="2"),
    ]
    mocker.patch.object(reversions, "_client", return_value=client)

    assert (
        main(
            (
                "--json",
                "jobs",
                "reverse",
                "submit",
                str(source),
                "--idempotency-key",
                "stable-key",
                "--retries",
                "1",
            )
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["id"] == JOB_ID
    assert result["poll_after_seconds"] == 2
    assert result["idempotency_key"] == "stable-key"
    assert client.submit_reversion.call_count == 2
    for call in client.submit_reversion.call_args_list:
        assert call.kwargs == {
            "filename": source.name,
            "idempotency_key": "stable-key",
        }
        assert call.args == (b"PK\x03\x04document",)


def test_submit_rejects_unsafe_or_oversized_source_before_post(
    tmp_path: Path, mocker, capsys
) -> None:
    client = mocker.Mock()
    client.reversion_capabilities.return_value = _response(
        payload=_capabilities(maximum=4)
    )
    mocker.patch.object(reversions, "_client", return_value=client)
    unsupported = tmp_path / "source.txt"
    unsupported.write_bytes(b"x")
    oversized = tmp_path / "source.docx"
    oversized.write_bytes(b"12345")
    empty = tmp_path / "empty.docx"
    empty.touch()
    directory = tmp_path / "directory.docx"
    directory.mkdir()
    symlink = tmp_path / "linked.docx"
    symlink.symlink_to(unsupported)

    assert main(("jobs", "reverse", "submit", str(unsupported))) == 1
    assert "not supported" in capsys.readouterr().err
    assert main(("jobs", "reverse", "submit", str(oversized))) == 1
    assert "exceeds" in capsys.readouterr().err
    assert main(("jobs", "reverse", "submit", str(empty))) == 1
    assert "empty" in capsys.readouterr().err
    assert main(("jobs", "reverse", "submit", str(directory))) == 1
    assert "unavailable" in capsys.readouterr().err
    assert main(("jobs", "reverse", "submit", str(symlink))) == 1
    assert "unsafe" in capsys.readouterr().err
    assert main(("jobs", "reverse", "submit", str(tmp_path / "missing.docx"))) == 1
    assert "unavailable" in capsys.readouterr().err
    client.submit_reversion.assert_not_called()


def test_submit_retries_require_key_and_preserve_terminal_network_error(
    tmp_path: Path, mocker, capsys
) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(b"doc")
    client = mocker.Mock()
    client.reversion_capabilities.return_value = _response(
        payload=_capabilities(maximum=4)
    )
    client.submit_reversion.side_effect = CliError("network_error", "offline")
    mocker.patch.object(reversions, "_client", return_value=client)

    assert main(("jobs", "reverse", "submit", str(source), "--retries", "1")) == 1
    assert "Retries require" in capsys.readouterr().err
    assert (
        main(
            (
                "jobs",
                "reverse",
                "submit",
                str(source),
                "--idempotency-key",
                "key",
            )
        )
        == 1
    )
    assert "offline" in capsys.readouterr().err

    client.submit_reversion.side_effect = None
    client.submit_reversion.return_value = _response(202, _job(), retry_after="1")
    assert main(("--json", "jobs", "reverse", "submit", str(source))) == 0
    assert "idempotency_key" not in json.loads(capsys.readouterr().out)


def test_list_show_cancel_and_download_use_reverse_endpoints(mocker, capsys) -> None:
    client = mocker.Mock()
    client.list_reversions.return_value = _response(
        payload={"items": [_job()], "total": 1, "offset": 0, "limit": 5}
    )
    client.get_reversion.return_value = _response(payload=_job())
    client.cancel_reversion.return_value = _response(payload=_job("cancelled"))
    client.download_reversion_result.return_value = _response(bytes_written=123)
    mocker.patch.object(reversions, "_client", return_value=client)

    assert main(("--json", "jobs", "reverse", "list", "--limit", "5")) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 1
    assert main(("jobs", "reverse", "show", JOB_ID)) == 0
    assert "is queued" in capsys.readouterr().out
    assert main(("jobs", "reverse", "cancel", JOB_ID)) == 0
    assert "is cancelled" in capsys.readouterr().out
    assert main(("--json", "jobs", "reverse", "download", JOB_ID, "result.md")) == 0
    assert json.loads(capsys.readouterr().out) == {
        "bytes": 123,
        "job_id": JOB_ID,
        "status": "downloaded",
        "type": "reversion_result",
    }
    client.list_reversions.assert_called_once_with(offset=0, limit=5)
    client.get_reversion.assert_called_once_with(JOB_ID)
    client.cancel_reversion.assert_called_once_with(JOB_ID)

    client.download_reversion_result.return_value = ConversionHttpResponse(
        200,
        headers={"x-correlation-id": CORRELATION_ID},
        bytes_written=1,
    )
    assert main(("--json", "jobs", "reverse", "download", JOB_ID, "other.md")) == 0
    assert json.loads(capsys.readouterr().out)["correlation_id"] == CORRELATION_ID

    client.download_reversion_result.return_value = _response()
    assert main(("jobs", "reverse", "download", JOB_ID, "missing.md")) == 1
    assert "invalid response" in capsys.readouterr().err

    client.download_reversion_result.return_value = _response(
        409,
        {"error": {"code": "REVERSION_CONFLICT", "message": "Not ready."}},
    )
    assert main(("jobs", "reverse", "download", JOB_ID, "conflict.md")) == 1
    assert "Not ready" in capsys.readouterr().err


def test_wait_requires_timeout_and_handles_success_failure_and_timeout(
    mocker, capsys
) -> None:
    client = mocker.Mock()
    mocker.patch.object(reversions, "_client", return_value=client)
    assert main(("jobs", "reverse", "wait", JOB_ID)) == 1
    assert "bounded --timeout" in capsys.readouterr().err

    client.get_reversion.side_effect = [
        _response(payload=_job()),
        _response(payload=_job("succeeded")),
    ]
    mocker.patch.object(reversions.time, "sleep")
    assert (
        main(
            (
                "--json",
                "--timeout",
                "1",
                "jobs",
                "reverse",
                "wait",
                JOB_ID,
                "--poll-interval",
                "0.01",
            )
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["state"] == "succeeded"

    failed = _job("failed")
    failed["error_code"] = "needs_ocr"
    failed["error_message"] = "OCR is unavailable."
    client.get_reversion.side_effect = None
    client.get_reversion.return_value = _response(payload=failed)
    assert main(("--timeout", "1", "jobs", "reverse", "wait", JOB_ID)) == 1
    assert "OCR is unavailable" in capsys.readouterr().err

    mocker.patch.object(reversions.time, "monotonic", side_effect=(0.0, 0.0, 2.0))
    client.get_reversion.return_value = _response(payload=_job())
    assert main(("--timeout", "1", "jobs", "reverse", "wait", JOB_ID)) == 1
    assert "did not finish" in capsys.readouterr().err

    mocker.patch.object(reversions.time, "monotonic", side_effect=(0.0, 2.0))
    assert main(("--timeout", "1", "jobs", "reverse", "wait", JOB_ID)) == 1
    assert "did not finish" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("payload", "expected"),
    (
        ({"items": None, "total": 0}, "invalid response"),
        ({"items": [], "total": True}, "invalid response"),
        ({"error": {"code": "NOT_FOUND", "message": "Not found."}}, "Not found"),
    ),
)
def test_lifecycle_rejects_invalid_or_safe_error_payloads(
    payload: dict[str, object], expected: str, mocker, capsys
) -> None:
    client = mocker.Mock()
    client.list_reversions.return_value = _response(
        404 if "error" in payload else 200, payload=payload
    )
    mocker.patch.object(reversions, "_client", return_value=client)
    assert main(("jobs", "reverse", "list")) == 1
    assert expected in capsys.readouterr().err


@pytest.mark.parametrize(
    "mutation",
    (
        {"id": "invalid"},
        {"owner_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"},
        {"correlation_id": None},
        {"state": "invented"},
        {"step": ""},
        {"attempt": True},
        {"cancel_requested": 0},
    ),
)
def test_job_validator_rejects_malformed_snapshots(mutation: dict[str, object]) -> None:
    with pytest.raises(CliError, match="invalid response"):
        reversions._job({**_job(), **mutation})


def test_response_validators_reject_wrong_envelope_shapes() -> None:
    with pytest.raises(CliError, match="invalid response"):
        reversions._validated_capabilities(None)
    with pytest.raises(CliError, match="invalid response"):
        reversions._job(None)
    with pytest.raises(CliError, match="invalid response"):
        reversions._payload(_response(), expected_status=200, fallback="failed")


@pytest.mark.parametrize("value", ("", "x" * 256, "has space", "line\nbreak"))
def test_idempotency_key_validation(value: str) -> None:
    with pytest.raises(CliError, match="idempotency key"):
        reversions._idempotency_key(value)


def test_reversion_multipart_preserves_unicode_basename_and_rejects_injection() -> None:
    body, content_type = conversion_http._reversion_multipart_body(
        b"source", filename="résumé.docx"
    )
    assert b'filename="r\xc3\xa9sum\xc3\xa9.docx"' in body
    assert b"source" in body
    assert content_type.startswith("multipart/form-data; boundary=markweave-")
    for invalid in ("", ".", "../source.docx", 'bad"name.docx', "bad\r.docx"):
        with pytest.raises(CliError, match="filename"):
            conversion_http._reversion_multipart_body(b"source", filename=invalid)


def test_http_client_uses_reverse_paths_csrf_and_atomic_download(
    tmp_path, mocker
) -> None:
    profile = mocker.Mock(
        service_url="https://example.test", session_state="session", csrf_state="csrf"
    )
    client = conversion_http.ConversionHttpClient(profile, timeout=2)
    request = mocker.patch.object(client, "request", return_value=_response())
    client.reversion_capabilities()
    client.list_reversions(offset=2, limit=3)
    client.get_reversion(JOB_ID)
    client.cancel_reversion(JOB_ID)
    client.submit_reversion(b"doc", filename="source.docx", idempotency_key="key")
    assert request.call_args_list[0].args == (
        "GET",
        "/api/v1/reversions/capabilities",
    )
    assert request.call_args_list[1].args == (
        "GET",
        "/api/v1/reversions?offset=2&limit=3",
    )
    assert request.call_args_list[2].args == (
        "GET",
        f"/api/v1/reversions/{JOB_ID}",
    )
    assert request.call_args_list[3].kwargs["csrf"] is True
    assert request.call_args_list[4].kwargs["headers"]["Idempotency-Key"] == "key"

    response = _DownloadResponse(b"markdown")
    mocker.patch.object(client, "_open", return_value=response)
    destination = tmp_path / "result.md"
    downloaded = client.download_reversion_result(JOB_ID, destination, overwrite=False)
    assert downloaded.bytes_written == 8
    assert destination.read_bytes() == b"markdown"
    assert stat_mode(destination) == 0o600


def test_http_client_maps_truncated_json_response_to_network_error(mocker) -> None:
    profile = mocker.Mock(
        service_url="https://example.test", session_state="session", csrf_state="csrf"
    )
    client = conversion_http.ConversionHttpClient(profile, timeout=2)
    response = mocker.Mock(status=202)
    response.read.side_effect = IncompleteRead(b'{"id":', 42)
    mocker.patch.object(client, "_open", return_value=response)

    with pytest.raises(CliError) as raised:
        client.submit_reversion(
            b"doc", filename="source.docx", idempotency_key="stable-key"
        )

    assert raised.value.code == "network_error"
    response.close.assert_called_once_with()


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


def test_identifier_numbers_retry_headers_and_terminal_states_are_bounded() -> None:
    request = reversions._Request("test", {"value": "-1", "job": "invalid"})
    with pytest.raises(CliError, match="arguments"):
        reversions._integer(request, "value", minimum=0)
    with pytest.raises(CliError, match="identifier"):
        reversions._uuid(request, "job")
    with pytest.raises(CliError, match="invalid response"):
        reversions._retry_after(_response())
    with pytest.raises(CliError, match="invalid response"):
        reversions._retry_after(_response(retry_after="not-a-number"))
    with pytest.raises(CliError, match="arguments"):
        reversions._integer(
            reversions._Request("test", {"value": "invalid"}),
            "value",
            minimum=0,
        )
    with pytest.raises(CliError, match="arguments"):
        reversions._number(
            reversions._Request("test", {"value": "invalid"}),
            "value",
            minimum=0.01,
            maximum=1,
        )
    with pytest.raises(CliError, match="arguments"):
        reversions._number(
            reversions._Request("test", {"value": "nan"}),
            "value",
            minimum=0.01,
            maximum=1,
        )
    with pytest.raises(CliError, match="arguments"):
        reversions._string(reversions._Request("test"), "missing")
    with pytest.raises(CliError, match="cancelled"):
        reversions._raise_terminal_failure(reversions._job(_job("cancelled")))
    with pytest.raises(CliError, match="expired"):
        reversions._raise_terminal_failure(reversions._job(_job("expired")))


def test_api_error_adds_only_a_canonical_correlation_identifier() -> None:
    response = ConversionHttpResponse(
        404,
        {"error": {"code": "NOT_FOUND", "message": "Not found."}},
        headers={"x-correlation-id": CORRELATION_ID},
    )
    error = reversions._api_error(response, fallback="fallback")
    assert error.code == "not_found"
    assert CORRELATION_ID in error.message
    malformed = ConversionHttpResponse(
        500, {}, headers={"x-correlation-id": str(uuid4()).upper()}
    )
    assert (
        "Correlation ID"
        in reversions._api_error(malformed, fallback="fallback").message
    )
