"""Supported quickstart Compose modes and the file stacks their helpers select."""

from __future__ import annotations

import itertools
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECURE_QUICKSTART = ROOT / "scripts/quickstart.sh"
SIMPLE_QUICKSTART = ROOT / "scripts/quickstart-simple.sh"


@dataclass(frozen=True)
class ComposeMode:
    """One supported quickstart mode and its reviewed Compose file stack."""

    name: str
    script: Path
    runtime: str
    antivirus: str
    composer: bool
    files: tuple[str, ...]

    @property
    def trusted_upstream(self) -> bool:
        """Return whether local ClamAV is replaced by a trusted upstream scanner."""
        return self.antivirus != "clamav"

    @property
    def insecure(self) -> bool:
        """Return whether the explicit insecure evaluation exception is active."""
        return self.antivirus == "insecure"


def _simple_files(runtime: str, antivirus: str, *, composer: bool) -> tuple[str, ...]:
    files = ["compose.yaml", "compose.simple.yaml"]
    if composer:
        files.append("compose.simple-composer.yaml")
    if runtime == "podman":
        files.append("compose.podman.yaml")
    if antivirus != "clamav":
        files.append("compose.trusted-upstream.yaml")
        if runtime == "podman":
            files.append("compose.podman-trusted-upstream.yaml")
    return tuple(files)


SUPPORTED_MODES: tuple[ComposeMode, ...] = (
    ComposeMode(
        name="secure-docker",
        script=SECURE_QUICKSTART,
        runtime="docker",
        antivirus="clamav",
        composer=False,
        files=("compose.yaml",),
    ),
    *(
        ComposeMode(
            name=f"simple-{runtime}-{antivirus}" + ("-composer" if composer else ""),
            script=SIMPLE_QUICKSTART,
            runtime=runtime,
            antivirus=antivirus,
            composer=composer,
            files=_simple_files(runtime, antivirus, composer=composer),
        )
        for runtime, antivirus, composer in itertools.product(
            ("docker", "podman"),
            ("clamav", "trusted-upstream", "insecure"),
            (False, True),
        )
    ),
)


def script_compose_files(mode: ComposeMode) -> tuple[str, ...]:
    """Return the Compose files that the mode's real helper function selects."""
    script = mode.script.read_text(encoding="utf-8")
    start = script.index("compose() {")
    function = script[start : script.index("\n}", start) + 2]
    harness = "\n".join(
        (
            f"repository={str(ROOT)!r}",
            "runtime_name=$1",
            "trusted_upstream_antivirus=$2",
            "insecure=$3",
            "composer_setup=$4",
            "port=8080",
            "public_origin=http://localhost:8080",
            "project=markweave-mode-probe",
            "runtime_env=/dev/null",
            "cutover_backend_image=backend",
            "cutover_frontend_image=frontend",
            "compose_command=(printf '%s\\n')",
            "router_public_host() { printf 'localhost:8080\\n'; }",
            "docker() { printf '%s\\n' \"$@\"; }",
            function,
            "compose config",
        )
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            harness,
            "bash",
            mode.runtime,
            str(mode.trusted_upstream).lower(),
            str(mode.insecure).lower(),
            str(mode.composer).lower(),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    arguments = result.stdout.splitlines()
    return tuple(
        str(Path(arguments[index + 1]).relative_to(ROOT))
        for index, argument in enumerate(arguments)
        if argument == "--file"
    )
