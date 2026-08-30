"""Unit coverage for supported runtime CLI command handlers."""

from __future__ import annotations

import pytest

from markweave.cli.commands.runtime import _runtime_error
from markweave.cli.main import main
from markweave.cli.types import ExitCode
from markweave.config import ConfigurationError, StorageProfile
from markweave.persistence.errors import PersistenceError
from markweave.persistence.migrations import MigrationResult
from markweave.runtime_diagnostics import DiagnosticCheck, DiagnosticReport
from markweave.runtime_migrations import ProfileMigrationResult

pytestmark = pytest.mark.unit


def test_serve_and_worker_delegate_to_existing_runtime(mocker, capsys) -> None:
    serve = mocker.patch(
        "markweave.runtime.run_http_service", return_value=StorageProfile.STANDALONE
    )
    worker = mocker.patch("markweave.runtime.run_external_worker")

    assert main(("serve",)) is ExitCode.SUCCESS
    assert capsys.readouterr() == ("HTTP service stopped.\n", "")
    serve.assert_called_once_with()

    assert main(("--json", "worker")) is ExitCode.SUCCESS
    assert capsys.readouterr() == (
        '{"command":"worker","profile":"distributed","status":"stopped"}\n',
        "",
    )
    worker.assert_called_once_with()


def test_runtime_configuration_failures_are_redacted(mocker, capsys) -> None:
    mocker.patch(
        "markweave.runtime.run_external_worker",
        side_effect=ConfigurationError("postgresql://user:password@private/db"),
    )

    assert main(("--json", "worker")) is ExitCode.FAILURE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        '{"error":{"code":"invalid_configuration",'
        '"message":"Invalid application configuration."}}\n'
    )
    assert "password" not in captured.err


@pytest.mark.parametrize(
    ("failure", "code"),
    (
        (PersistenceError(), "persistence_failure"),
        (TimeoutError(), "operation_timeout"),
        (RuntimeError("private"), "runtime_failure"),
    ),
)
def test_runtime_failure_mapping_is_fixed(failure: Exception, code: str) -> None:
    error = _runtime_error(failure)
    assert error.code == code
    assert "private" not in error.message


def test_each_runtime_handler_sanitizes_its_boundary_failure(mocker, capsys) -> None:
    mocker.patch(
        "markweave.runtime.run_http_service", side_effect=RuntimeError("private")
    )
    assert main(("serve",)) is ExitCode.FAILURE
    assert capsys.readouterr() == ("", "error: Runtime operation failed.\n")

    mocker.patch(
        "markweave.runtime_diagnostics.run_runtime_diagnostics",
        side_effect=TimeoutError,
    )
    assert main(("doctor",)) is ExitCode.FAILURE
    assert capsys.readouterr() == ("", "error: The operation timed out.\n")

    mocker.patch(
        "markweave.runtime_migrations.migrate_configured_profile",
        side_effect=PersistenceError,
    )
    assert main(("migrate",)) is ExitCode.FAILURE
    assert capsys.readouterr() == ("", "error: Persistence operation failed.\n")


def test_doctor_reports_fixed_checks_and_fails_by_name_only(mocker, capsys) -> None:
    run = mocker.patch("markweave.runtime_diagnostics.run_runtime_diagnostics")
    run.return_value = DiagnosticReport(
        profile=StorageProfile.STANDALONE,
        checks=(
            DiagnosticCheck("configuration", True),
            DiagnosticCheck("storage", True),
        ),
    )
    assert main(("--json", "--timeout", "2", "doctor")) is ExitCode.SUCCESS
    assert capsys.readouterr() == (
        '{"checks":[{"name":"configuration","status":"passed"},'
        '{"name":"storage","status":"passed"}],"profile":"standalone",'
        '"status":"passed"}\n',
        "",
    )
    run.assert_called_once_with(timeout_seconds=2.0)

    run.return_value = DiagnosticReport(
        profile=StorageProfile.DISTRIBUTED,
        checks=(DiagnosticCheck("scanner", False),),
    )
    assert main(("doctor",)) is ExitCode.FAILURE
    assert capsys.readouterr() == (
        "",
        "error: Runtime checks failed: scanner.\n",
    )


def test_migrate_reports_idempotent_revision_without_database_details(
    mocker, capsys
) -> None:
    migrate = mocker.patch(
        "markweave.runtime_migrations.migrate_configured_profile",
        return_value=ProfileMigrationResult(
            profile=StorageProfile.STANDALONE,
            previous_revision="20260829_14",
            current_revision="20260829_14",
            changed=False,
        ),
    )

    assert main(("--json", "migrate")) is ExitCode.SUCCESS
    assert capsys.readouterr() == (
        '{"changed":false,"command":"migrate","profile":"standalone",'
        '"revision":"20260829_14","status":"completed"}\n',
        "",
    )
    migrate.assert_called_once_with(timeout_seconds=None)


def test_profile_migration_result_uses_locked_migration_observation() -> None:
    result = ProfileMigrationResult(
        profile=StorageProfile.DISTRIBUTED,
        previous_revision=None,
        current_revision="head",
        changed=True,
    )
    migration = MigrationResult(None, "head")
    assert result.changed
    assert migration.current_revision == result.current_revision
