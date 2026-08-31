"""Run a quarterly exercise through the production ``markweave restore`` command."""

from __future__ import annotations

import argparse

from markweave.cli.main import main as markweave_main


def main() -> int:
    """Forward only structured restore arguments to the package command."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float)
    parser.add_argument("restore_arguments", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    restore_arguments = arguments.restore_arguments
    if restore_arguments and restore_arguments[0] == "--":
        restore_arguments = restore_arguments[1:]
    required = {
        "--profile",
        "--source",
        "--offline-proof",
        "--yes",
        "--report-directory",
        "--evidence-id",
    }
    if not restore_arguments or not required.issubset(restore_arguments):
        parser.error(
            "a confirmed production restore with immutable exercise evidence is required"
        )
    command = ["--non-interactive"]
    if arguments.timeout is not None:
        command.extend(("--timeout", str(arguments.timeout)))
    command.extend(("restore", *restore_arguments))
    return int(markweave_main(command))


if __name__ == "__main__":  # pragma: no cover - executable compatibility wrapper
    raise SystemExit(main())
