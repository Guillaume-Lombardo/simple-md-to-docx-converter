"""Build the final CI images once and load them elsewhere only by verified ID.

`CI / build images` builds each required image role once per workflow run,
exports it with `podman save --format oci-archive`, verifies that the archive's
image configuration digest is the built image ID, and publishes that ID as a job
output. Every consumer job downloads only the archives its domain needs and,
before tagging anything, requires the archive configuration digest, the image
ID reported by `podman load`, and the ID of the tagged image to equal the build
output. A missing, renamed, substituted, or partially loaded archive fails
closed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scripts.ci.select_domains import IMAGE_DOMAIN_ROLES, IMAGE_ROLES
from scripts.container.integrity import IntegrityError, oci_identity

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

ROLE_IMAGES: Mapping[str, str] = {
    "backend": "localhost/md-converter:ci",
    "frontend": "localhost/md-converter-web:ci",
    "reverse-attempt": "localhost/md-converter-reverse-attempt:ci",
}
ROLE_CONTAINERFILES: Mapping[str, str] = {
    "backend": "Containerfile",
    "frontend": "web/Containerfile",
    "reverse-attempt": "containers/reverse-attempt/Containerfile",
}
IMAGE_ID = re.compile(r"[0-9a-f]{64}")
EXPECTED_IMAGE_ID = re.compile(r"sha256:([0-9a-f]{64})")
LOADED_IMAGE_LINE = re.compile(r"^Loaded image(?:\(s\))?:\s*(.+)$")
LOADED_REFERENCE = re.compile(r"(?:sha256:)?([0-9a-f]{64})")

DEFAULT_IMAGE_COPY_TMP_DIR = Path("/var/tmp")  # noqa: S108 - Podman's documented default
DISKSTATS = Path("/proc/diskstats")
# Each `podman load` extracts its oci-archive and stages the layer blobs in the
# image-copy directory before committing layers, so this directory receives about
# twice the archive bytes. Hosted runners cap disk writes at about 165 MB/s, so a
# memory-backed directory, when it has room, removes those writes (T147).
MEMORY_TMP_ROOT = Path("/dev/shm")  # noqa: S108 - holds a private mkdtemp subdirectory
MEMORY_TMP_FACTOR = 2
MEMORY_TMP_MARGIN_BYTES = 512 * 1024 * 1024
DISKSTATS_FIELDS = 13  # through the io_ticks (busy milliseconds) column
SECTOR_BYTES = 512
MIB = 1024 * 1024

type Runner = Callable[..., subprocess.CompletedProcess[str]]
type DiskStats = Mapping[str, tuple[int, int, int]]


class ImageTransferError(RuntimeError):
    """Raised when a final image cannot be built, exported, or loaded exactly."""


def archive_name(role: str) -> str:
    """Return the archive file name that carries one image role."""
    if role not in ROLE_IMAGES:
        raise ImageTransferError(f"unknown image role: {role!r}")
    return f"{role}.oci.tar"


def parse_roles(text: str) -> tuple[str, ...]:
    """Parse the detector's JSON role list into canonical build order."""
    try:
        value: Any = json.loads(text)
    except json.JSONDecodeError as error:
        raise ImageTransferError("image roles must be a JSON array") from error
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(role, str) for role in value)
    ):
        raise ImageTransferError("image roles must be a non-empty JSON string array")
    unknown = sorted(set(value).difference(ROLE_IMAGES))
    if unknown:
        raise ImageTransferError(f"unknown image roles: {unknown!r}")
    if len(set(value)) != len(value):
        raise ImageTransferError("image roles must not repeat")
    return tuple(role for role in IMAGE_ROLES if role in value)


def _run(
    runner: Runner, arguments: Sequence[str], env: Mapping[str, str] | None = None
) -> str:
    options: dict[str, Any] = {"check": False, "capture_output": True, "text": True}
    if env is not None:
        options["env"] = dict(env)
    completed = runner(list(arguments), **options)
    if completed.returncode != 0:
        raise ImageTransferError(
            f"command failed with status {completed.returncode}: "
            f"{' '.join(arguments[:3])}\n{completed.stderr.strip()}"
        )
    return completed.stdout


def _stream(runner: Runner, arguments: Sequence[str]) -> None:
    completed = runner(list(arguments), check=False)
    if completed.returncode != 0:
        raise ImageTransferError(
            f"command failed with status {completed.returncode}: "
            f"{' '.join(arguments[:3])}"
        )


def _image_id(runner: Runner, reference: str) -> str:
    image_id = _run(
        runner, ["podman", "image", "inspect", reference, "--format", "{{.Id}}"]
    ).strip()
    if IMAGE_ID.fullmatch(image_id) is None:
        raise ImageTransferError(f"image {reference} has no unambiguous image ID")
    return image_id


def _require_rootless(runner: Runner) -> None:
    rootless = _run(
        runner, ["podman", "info", "--format", "{{.Host.Security.Rootless}}"]
    ).strip()
    if rootless != "true":
        raise ImageTransferError("final images must use a rootless Podman runtime")


def _verified_archive_id(archive: Path, expected_id: str) -> None:
    try:
        _manifest, config = oci_identity(archive)
    except IntegrityError as error:
        raise ImageTransferError(f"{archive.name}: {error}") from error
    if config != f"sha256:{expected_id}":
        raise ImageTransferError(
            f"{archive.name}: archive image configuration {config} does not match "
            f"the expected image ID sha256:{expected_id}"
        )


def _build_command(role: str, source_date_epoch: str) -> list[str]:
    image = ROLE_IMAGES[role]
    if role == "backend":
        return ["bash", "scripts/container/build.sh", image]
    if role == "reverse-attempt":
        return ["bash", "scripts/container/build-reverse-attempt.sh", image]
    return ["bash", "scripts/container/build-frontend.sh", image, source_date_epoch]


def build(
    roles: Sequence[str],
    output_directory: Path,
    github_output: Path,
    *,
    runner: Runner = subprocess.run,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, str]:
    """Build each role once, export it, and publish its verified image ID."""
    if output_directory.exists() or output_directory.is_symlink():
        raise ImageTransferError("the archive directory must not already exist")
    output_directory.mkdir(mode=0o700, parents=False)
    _require_rootless(runner)
    source_date_epoch = _run(runner, ["git", "show", "-s", "--format=%ct", "HEAD"])
    source_date_epoch = source_date_epoch.strip()
    if not source_date_epoch.isdecimal():
        raise ImageTransferError("the reviewed commit has no build timestamp")
    _stream(
        runner,
        [
            "bash",
            "scripts/container/pull-base-images.sh",
            *(ROLE_CONTAINERFILES[role] for role in roles),
        ],
    )
    identities: dict[str, str] = {}
    for role in roles:
        started = clock()
        _stream(runner, _build_command(role, source_date_epoch))
        built = clock()
        image_id = _image_id(runner, ROLE_IMAGES[role])
        archive = output_directory / archive_name(role)
        _run(
            runner,
            [
                "podman",
                "save",
                "--format",
                "oci-archive",
                "--output",
                str(archive),
                image_id,
            ],
        )
        _verified_archive_id(archive, image_id)
        exported = clock()
        identities[role] = f"sha256:{image_id}"
        print(
            f"built role={role} image_id=sha256:{image_id} "
            f"archive_bytes={archive.stat().st_size} "
            f"build_seconds={built - started:.1f} "
            f"export_seconds={exported - built:.1f}",
            flush=True,
        )
    with github_output.open("a", encoding="utf-8") as output:
        for role, image_id in identities.items():
            output.write(f"{role}-image-id={image_id}\n")
    return identities


def _pending_load(
    role: str,
    archive_directory: Path,
    expected: Mapping[str, str],
    names: Mapping[str, str],
    runner: Runner,
) -> tuple[str, str, Path]:
    """Check one role completely before any archive of the domain is loaded."""
    match = EXPECTED_IMAGE_ID.fullmatch(expected.get(role, ""))
    if match is None:
        raise ImageTransferError(
            f"no built image ID was published for the {role} image"
        )
    image_id = match.group(1)
    archive = archive_directory / archive_name(role)
    if archive.is_symlink() or not archive.is_file():
        raise ImageTransferError(f"the {role} archive was not downloaded")
    _verified_archive_id(archive, image_id)
    name = names[role]
    exists = runner(
        ["podman", "image", "exists", name], check=False, capture_output=True
    )
    if exists.returncode == 0:
        raise ImageTransferError(f"refusing to replace the existing image {name}")
    return role, image_id, archive


def read_diskstats(path: Path = DISKSTATS) -> dict[str, tuple[int, int, int]]:
    """Return sectors read, sectors written and busy milliseconds per device."""
    stats: dict[str, tuple[int, int, int]] = {}
    for line in path.read_text(encoding="ascii").splitlines():
        fields = line.split()
        if len(fields) >= DISKSTATS_FIELDS:
            stats[f"{fields[0]}:{fields[1]}"] = (
                int(fields[5]),
                int(fields[9]),
                int(fields[12]),
            )
    return stats


def _device(path: Path) -> str:
    try:
        device = path.stat().st_dev
    except OSError:
        return "unknown"
    return f"{os.major(device)}:{os.minor(device)}"


def _storage_report(
    runner: Runner, archive_directory: Path, copy_dir: Path | None
) -> tuple[str, ...]:
    """Print the Podman store layout and return the devices worth measuring.

    These facts only explain load throughput (T147); they never gate a load.
    """
    info: Any = json.loads(_run(runner, ["podman", "info", "--format", "json"]))
    store = info.get("store", {})
    graph_root = Path(store.get("graphRoot", ""))
    copy_dir = copy_dir or Path(
        store.get("imageCopyTmpDir") or DEFAULT_IMAGE_COPY_TMP_DIR
    )
    places = {
        "archives": archive_directory,
        "graph_root": graph_root,
        "image_copy_tmp_dir": copy_dir,
    }
    devices = {name: _device(path) for name, path in places.items()}
    print(
        f"storage podman={info.get('version', {}).get('Version', 'unknown')} "
        f"driver={store.get('graphDriverName', 'unknown')} "
        f"options={json.dumps(store.get('graphOptions', {}), sort_keys=True)} "
        + " ".join(f"{name}={places[name]}@{devices[name]}" for name in places),
        flush=True,
    )
    return tuple(sorted(set(devices.values()) - {"unknown"}))


def _memory_copy_dir(root: Path, archive_bytes: int) -> Path | None:
    """Return a private memory-backed image-copy directory when it has room."""
    required = MEMORY_TMP_FACTOR * archive_bytes + MEMORY_TMP_MARGIN_BYTES
    try:
        free = shutil.disk_usage(root).free if root.is_dir() else 0
    except OSError:
        free = 0
    if free < required:
        print(
            f"load_copy_dir=default root={root} free_mib={free // MIB} "
            f"required_mib={required // MIB}",
            flush=True,
        )
        return None
    directory = Path(tempfile.mkdtemp(prefix="markweave-load-", dir=root))
    print(
        f"load_copy_dir={directory} free_mib={free // MIB} "
        f"required_mib={required // MIB}",
        flush=True,
    )
    return directory


def _print_disk_delta(
    devices: Sequence[str], before: DiskStats, after: DiskStats, seconds: float
) -> None:
    for device in devices:
        if device not in before or device not in after:
            continue
        read, written, busy = (
            later - earlier
            for earlier, later in zip(before[device], after[device], strict=True)
        )
        print(
            f"disk device={device} read_mib={read * SECTOR_BYTES / MIB:.0f} "
            f"written_mib={written * SECTOR_BYTES / MIB:.0f} "
            f"busy_percent={100 * busy / max(seconds * 1000, 1):.0f}",
            flush=True,
        )


def load(  # noqa: PLR0913 - explicit expected-identity and test seams
    domain: str,
    archive_directory: Path,
    expected: Mapping[str, str],
    *,
    names: Mapping[str, str] = ROLE_IMAGES,
    runner: Runner = subprocess.run,
    clock: Callable[[], float] = time.monotonic,
    diskstats: Callable[[], DiskStats] = read_diskstats,
    memory_root: Path | None = None,
) -> dict[str, str]:
    """Load only a domain's archives and tag them after exact ID checks.

    Every role passes its preconditions before any archive is loaded. The
    `podman load` commands then run concurrently, with their image-copy
    directory in memory when it has room (T147), and every loaded ID is checked
    before its tag.
    """
    try:
        roles = IMAGE_DOMAIN_ROLES[domain]
    except KeyError as error:
        raise ImageTransferError(
            f"domain {domain!r} consumes no final image"
        ) from error
    _require_rootless(runner)
    pending = [
        _pending_load(role, archive_directory, expected, names, runner)
        for role in roles
    ]

    copy_dir = _memory_copy_dir(
        memory_root or MEMORY_TMP_ROOT,
        sum(archive.stat().st_size for _, _, archive in pending),
    )
    env = None if copy_dir is None else {**os.environ, "TMPDIR": str(copy_dir)}
    try:
        devices = _storage_report(runner, archive_directory, copy_dir)
        before = diskstats()
    except (ImageTransferError, OSError, ValueError, AttributeError) as error:
        print(f"storage diagnostics unavailable: {error}", flush=True)
        devices, before = (), {}

    def load_archive(archive: Path) -> tuple[str, float]:
        started = clock()
        output = _run(runner, ["podman", "load", "--input", str(archive)], env)
        return output, clock() - started

    started = clock()
    try:
        with ThreadPoolExecutor(max_workers=len(pending)) as pool:
            futures = [pool.submit(load_archive, archive) for _, _, archive in pending]
        # Leaving the pool waited for every load; report the first failure in
        # role order.
        results = [future.result() for future in futures]
    finally:
        if copy_dir is not None:
            shutil.rmtree(copy_dir, ignore_errors=True)
    if devices:
        try:
            _print_disk_delta(devices, before, diskstats(), clock() - started)
        except (OSError, ValueError) as error:
            print(f"storage diagnostics unavailable: {error}", flush=True)
    loaded: dict[str, str] = {}
    for (role, image_id, archive), (output, seconds) in zip(
        pending, results, strict=True
    ):
        reported = {
            reference.group(1)
            for line in output.splitlines()
            if (entry := LOADED_IMAGE_LINE.match(line.strip())) is not None
            for item in entry.group(1).split(",")
            if (reference := LOADED_REFERENCE.fullmatch(item.strip())) is not None
        }
        if reported != {image_id}:
            raise ImageTransferError(
                f"podman load did not report exactly the expected {role} image ID"
            )
        if _image_id(runner, image_id) != image_id:
            raise ImageTransferError(f"the loaded {role} image ID differs")
        name = names[role]
        _run(runner, ["podman", "tag", image_id, name])
        if _image_id(runner, name) != image_id:
            raise ImageTransferError(f"{name} does not resolve to the built image")
        loaded[role] = name
        print(
            f"loaded role={role} image={name} image_id=sha256:{image_id} "
            f"archive_bytes={archive.stat().st_size} "
            f"load_seconds={seconds:.1f}",
            flush=True,
        )
    print(
        f"loaded domain={domain} roles={len(loaded)} "
        f"concurrent_load_seconds={clock() - started:.1f}",
        flush=True,
    )
    return loaded


def _arguments(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--roles", required=True)
    build_parser.add_argument("--output-directory", type=Path, required=True)
    build_parser.add_argument("--github-output", type=Path, required=True)
    load_parser = commands.add_parser("load")
    load_parser.add_argument("--domain", required=True)
    load_parser.add_argument("--archive-directory", type=Path, required=True)
    for role in IMAGE_ROLES:
        load_parser.add_argument(f"--{role}-image-id", default="")
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    """Run one build or load phase for GitHub Actions."""
    options = _arguments(arguments)
    try:
        if options.command == "build":
            build(
                parse_roles(options.roles),
                options.output_directory,
                options.github_output,
            )
        else:
            load(
                options.domain,
                options.archive_directory,
                {
                    role: getattr(options, f"{role.replace('-', '_')}_image_id")
                    for role in IMAGE_ROLES
                },
            )
    except ImageTransferError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by GitHub Actions
    raise SystemExit(main())
