"""Focused stream-contract regressions for the reverse CLI final-image driver."""

from __future__ import annotations

import subprocess

import pytest

from tests.e2e import reverse_cli_workflow as workflow

pytestmark = pytest.mark.unit


def _result(*, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(("markweave",), 0, stdout, stderr)


def test_human_success_requires_stdout_without_stderr() -> None:
    workflow._require_human(
        _result(stdout="Reverse job is succeeded.\n"), "Reverse job"
    )

    with pytest.raises(workflow.WorkflowFailure, match="human command output"):
        workflow._require_human(
            _result(stderr="Reverse job is succeeded.\n"), "Reverse job"
        )


def test_human_error_requires_stderr_without_stdout() -> None:
    workflow._require_human_error(
        _result(stderr="error: The source extension is not supported.\n"),
        "not supported",
    )

    with pytest.raises(workflow.WorkflowFailure, match="human error output"):
        workflow._require_human_error(
            _result(stdout="error: The source extension is not supported.\n"),
            "not supported",
        )


def test_owner_denial_is_a_stderr_only_human_error() -> None:
    workflow._require_human_error(_result(stderr="error: Not found.\n"), "not found")

    with pytest.raises(workflow.WorkflowFailure, match="human error output"):
        workflow._require_human_error(
            _result(stdout="error: Not found.\n"), "not found"
        )


def test_json_error_reads_stderr_and_requires_the_stable_code(mocker) -> None:
    mocker.patch.object(
        workflow,
        "_command",
        return_value=_result(
            stderr='{"error":{"code":"needs_ocr","message":"OCR is unavailable."}}\n'
        ),
    )

    error = workflow._error_json(("markweave",), ("--json", "jobs", "reverse"))
    workflow._require_error_code(error, "needs_ocr")

    with pytest.raises(workflow.WorkflowFailure, match="reverse CLI error code"):
        workflow._require_error_code(error, "source_type_invalid")
