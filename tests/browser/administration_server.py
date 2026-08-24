"""Real standalone application for the T17 Chromium acceptance scenario."""

from __future__ import annotations

import argparse
import stat
from pathlib import Path

import uvicorn

from md_converter.app import create_app
from md_converter.config import Settings
from md_converter.malware import TrustingUploadScanner
from tests.settings import template_settings


def build_app(data_directory: Path):
    """Build production HTTP, persistence, storage, and authentication boundaries."""
    data_directory.mkdir(parents=True, exist_ok=True)
    engine = data_directory / "template-engine"
    engine.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, shutil, sys\n"
        "args=sys.argv[1:]\n"
        "reference=next((x.split('=',1)[1] for x in args if x.startswith('--reference-doc=')), None)\n"
        "output=next((x.split('=',1)[1] for x in args if x.startswith('--output=')), None)\n"
        "if reference and output: shutil.copyfile(reference, output)\n"
        "elif '--outdir' in args:\n"
        " source=pathlib.Path(args[-1]); target=pathlib.Path(args[args.index('--outdir')+1])/source.name; shutil.copyfile(source,target)\n",
        encoding="utf-8",
    )
    engine.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return create_app(
        Settings(
            **template_settings(
                template_pandoc_executable=str(engine),
                template_libreoffice_executable=str(engine),
            ),
            initial_admin_username="browser-admin",
            initial_admin_password="browser-" + "password",
            argon2_memory_cost=8,
            argon2_time_cost=1,
            storage_profile="standalone",
            standalone_data_directory=data_directory,
            conversion_upload_max_bytes=1_000_000,
            conversion_request_max_bytes=1_100_000,
            conversion_retry_after_seconds=1,
            job_result_retention_seconds=3_600,
        ),
        scanner=TrustingUploadScanner(),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--data", required=True, type=Path)
    arguments = parser.parse_args()
    uvicorn.run(build_app(arguments.data), host="127.0.0.1", port=arguments.port)


if __name__ == "__main__":
    main()
