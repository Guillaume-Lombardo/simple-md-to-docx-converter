"""Unit coverage for the stable T31 command-line contract."""

from __future__ import annotations

import math

import pytest

from markweave.cli.errors import CliError
from markweave.cli.http import ApiResponse
from markweave.cli.main import _positive_timeout, build_parser, main
from markweave.cli.output import OutputWriter
from markweave.cli.types import ConnectionProfile, ExitCode, OutputFormat
from markweave.version import VERSION

pytestmark = pytest.mark.unit


def test_root_help_snapshot(capsys: pytest.CaptureFixture[str]) -> None:
    """The root registry exposes every family before its implementation lands."""
    with pytest.raises(SystemExit) as raised:
        build_parser().parse_args(("--help",))

    assert raised.value.code == 0
    assert capsys.readouterr().out == (
        "usage: markweave [-h] [--version] [--json] [--non-interactive]\n"
        "                 [--timeout SECONDS]\n"
        "                 COMMAND ...\n\n"
        "Command-line interface for Markweave.\n\n"
        "positional arguments:\n"
        "  COMMAND\n"
        "    login            Sign in to a remote service.\n"
        "    logout           Sign out of a remote service.\n"
        "    whoami           Show the active remote session.\n"
        "    password         Manage the current account password.\n"
        "    convert          Submit a conversion.\n"
        "    jobs             Inspect and manage conversion jobs.\n"
        "    templates        Discover and manage document templates.\n"
        "    users            Administer local users.\n"
        "    audit            Inspect audit records.\n"
        "    health           Inspect service health.\n"
        "    serve            Run the local HTTP service.\n"
        "    worker           Run a local worker.\n"
        "    doctor           Check local prerequisites.\n"
        "    migrate          Apply database migrations.\n"
        "    backup           Create a local backup.\n"
        "    restore          Restore a local backup.\n\n"
        "options:\n"
        "  -h, --help         show this help message and exit\n"
        "  --version          Show version.\n"
        "  --json             Write machine-readable output.\n"
        "  --non-interactive  Fail instead of prompting for input.\n"
        "  --timeout SECONDS  Bound this command's network or operational wait.\n"
    )


def test_version_and_missing_command_use_the_documented_streams(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Version is standard output, while an incomplete invocation is usage."""
    with pytest.raises(SystemExit) as raised:
        main(("--version",))
    assert raised.value.code == 0
    assert capsys.readouterr().out == f"markweave {VERSION}\n"

    assert main(()) is ExitCode.USAGE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("usage: markweave")


@pytest.mark.parametrize(
    ("arguments", "expected_command"),
    (
        (("convert",), "convert"),
        (("jobs", "wait"), "jobs wait"),
        (("templates", "restore"), "templates restore"),
        (("users", "deactivate"), "users deactivate"),
        (("health", "metrics"), "health metrics"),
        (("serve",), "serve"),
        (("backup",), "backup"),
    ),
)
def test_pre_registered_commands_fail_stably(
    arguments: tuple[str, ...],
    expected_command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Family placeholders never accidentally execute a backend."""
    assert main(arguments) is ExitCode.UNAVAILABLE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err
        == f"error: The '{expected_command}' command is not available in this release.\n"
    )


def test_json_output_timeout_and_non_interactive_context(
    mocker, capsys: pytest.CaptureFixture[str]
) -> None:
    """Shared command options are parsed once and passed to a family handler."""
    handler = mocker.Mock()
    parser = build_parser()
    parser.set_defaults(command_name="test", command_handler=handler)
    mocker.patch("markweave.cli.main.build_parser", return_value=parser)

    assert main(("--json", "--non-interactive", "--timeout", "1.5")) is ExitCode.SUCCESS
    context, writer, command = handler.call_args.args
    assert context.output_format is OutputFormat.JSON
    assert context.non_interactive is True
    assert context.timeout_seconds == 1.5
    assert command == "test"
    assert isinstance(writer, OutputWriter)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("value", ("0", "-1", "nan", "inf", "invalid"))
def test_timeout_rejects_non_positive_or_non_finite_values(value: str) -> None:
    """Timeouts remain bounded before any command implementation receives them."""
    with pytest.raises(Exception, match="positive"):
        _positive_timeout(value)


def test_timeout_accepts_positive_finite_value() -> None:
    """A usable timeout round-trips exactly through the parser helper."""
    assert _positive_timeout("2.25") == 2.25
    assert math.isfinite(_positive_timeout("1"))


def test_json_errors_and_sanitized_unexpected_failure(
    mocker, capsys: pytest.CaptureFixture[str]
) -> None:
    """Errors never share stdout with data or expose implementation details."""
    parser = build_parser()
    parser.set_defaults(
        command_name="test",
        command_handler=mocker.Mock(
            side_effect=CliError("bad_request", "Safe explanation.")
        ),
    )
    mocker.patch("markweave.cli.main.build_parser", return_value=parser)
    assert main(("--json",)) is ExitCode.FAILURE
    assert capsys.readouterr() == (
        "",
        '{"error":{"code":"bad_request","message":"Safe explanation."}}\n',
    )

    parser.set_defaults(
        command_handler=mocker.Mock(side_effect=RuntimeError("secret=not-disclosed"))
    )
    assert main(()) is ExitCode.FAILURE
    assert capsys.readouterr() == ("", "error: An internal error occurred.\n")


def test_output_writer_success_details_and_interrupt_are_safe(
    mocker, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both renderers preserve their stream boundary for every shared outcome."""
    human = OutputWriter(OutputFormat.HUMAN)
    human.success("Done.")
    assert capsys.readouterr() == ("Done.\n", "")

    machine = OutputWriter(OutputFormat.JSON)
    machine.success("ignored", {"status": "ok"})
    machine.error(CliError("invalid", "Safe.", details={"field": "value"}))
    assert capsys.readouterr() == (
        '{"status":"ok"}\n',
        '{"error":{"code":"invalid","message":"Safe."}}\n',
    )

    parser = build_parser()
    parser.set_defaults(
        command_name="test", command_handler=mocker.Mock(side_effect=KeyboardInterrupt)
    )
    mocker.patch("markweave.cli.main.build_parser", return_value=parser)
    assert main(()) is ExitCode.FAILURE
    assert capsys.readouterr() == ("", "error: Command interrupted.\n")


def test_connection_profile_keeps_opaque_session_values_out_of_repr() -> None:
    """Profile logging cannot expose the bounded state T32 will persist."""
    profile = ConnectionProfile(
        name="default",
        service_url="https://example.test",
        session_state="session-secret",
        csrf_state="csrf-secret",
    )
    assert "secret" not in repr(profile)


def test_http_response_keeps_cookie_and_session_values_out_of_repr() -> None:
    """Transport result diagnostics cannot disclose HTTP authentication state."""
    response = ApiResponse(
        200,
        {"csrf_token": "csrf-secret"},
        "session-secret",
        (("session", "cookie-secret"),),
    )
    assert "secret" not in repr(response)


@pytest.mark.parametrize("output_format", (OutputFormat.HUMAN, OutputFormat.JSON))
def test_error_rendering_ignores_unserializable_sensitive_details(
    output_format: OutputFormat, capsys: pytest.CaptureFixture[str]
) -> None:
    """Controlled details cannot cause a traceback or disclose their contents."""

    class UnserializableDetail:
        def __repr__(self) -> str:
            return "password=do-not-disclose"

        def __str__(self) -> str:
            return "password=do-not-disclose"

    OutputWriter(output_format).error(
        CliError(
            "invalid_request",
            "Request could not be completed.",
            details={"cause": UnserializableDetail()},
        )
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "password=do-not-disclose" not in captured.err
    if output_format is OutputFormat.JSON:
        assert captured.err == (
            '{"error":{"code":"invalid_request","message":"Request could not be completed."}}\n'
        )
    else:
        assert captured.err == "error: Request could not be completed.\n"
