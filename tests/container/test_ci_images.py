"""Unit coverage for building CI images once and loading them by verified ID."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
import threading
from pathlib import Path
from typing import Any

import pytest

from scripts.ci.select_domains import IMAGE_DOMAIN_ROLES
from scripts.container import ci_images
from scripts.container.ci_images import ImageTransferError

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_memory_copy_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep loads off the host's /dev/shm unless a test opts into a memory root."""
    monkeypatch.setattr(ci_images, "MEMORY_TMP_ROOT", tmp_path / "no-shm")


def _config(role: str) -> bytes:
    return json.dumps(
        {"rootfs": {"type": "layers", "diff_ids": []}, "role": role}
    ).encode()


def _image_id(role: str) -> str:
    return hashlib.sha256(_config(role)).hexdigest()


def _add(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(payload)
    archive.addfile(member, io.BytesIO(payload))


def _write_archive(path: Path, role: str) -> None:
    config = _config(role)
    config_hex = hashlib.sha256(config).hexdigest()
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "config": {"digest": f"sha256:{config_hex}", "size": len(config)},
            "layers": [],
        }
    ).encode()
    manifest_hex = hashlib.sha256(manifest).hexdigest()
    index = json.dumps(
        {
            "schemaVersion": 2,
            "manifests": [{"digest": f"sha256:{manifest_hex}", "size": len(manifest)}],
        }
    ).encode()
    with tarfile.open(path, "w:") as archive:
        _add(archive, "index.json", index)
        _add(archive, f"blobs/sha256/{manifest_hex}", manifest)
        _add(archive, f"blobs/sha256/{config_hex}", config)


class FakeRuntime:
    """Record commands and emulate the rootless Podman and Git boundary."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.images: dict[str, str] = {}
        self.rootless = "true"
        self.loaded_override: str | None = None
        self.tag_override: str | None = None
        self.failing: tuple[str, ...] | None = None
        self.info_json: str | None = None
        self.experiment: list[list[str]] = []

    def __call__(
        self, arguments: list[str], **_options: Any
    ) -> subprocess.CompletedProcess[str]:
        match arguments:
            case ["podman", "push", "--compression-format", "zstd", image_id, target]:
                self.experiment.append(arguments)
                role = next(
                    role
                    for role in ci_images.ROLE_IMAGES
                    if _image_id(role) == image_id
                )
                _write_archive(Path(target.removeprefix("oci-archive:")), role)
                return subprocess.CompletedProcess(arguments, 0, "", "")
            case ["podman", "--root", _, "--runroot", _, "load", *_] | [
                "podman",
                "unshare",
                "rm",
                *_,
            ]:
                self.experiment.append(arguments)
                return subprocess.CompletedProcess(arguments, 0, "", "")
        return self._respond(arguments)

    def _respond(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(arguments)
        if self.failing is not None and tuple(arguments[: len(self.failing)]) == (
            self.failing
        ):
            return subprocess.CompletedProcess(arguments, 3, "", "failed")
        stdout = ""
        status = 0
        match arguments:
            case ["git", "show", *_]:
                stdout = "1789000000\n"
            case ["podman", "info", "--format", format_]:
                json_info = format_ == "json" and self.info_json
                stdout = json_info or f"{self.rootless}\n"
            case ["bash", script, image, *_] if script.startswith(
                "scripts/container/build"
            ):
                role = next(
                    role
                    for role, name in ci_images.ROLE_IMAGES.items()
                    if name == image
                )
                self.images[image] = _image_id(role)
            case ["podman", "image", "inspect", reference, *_]:
                image_id = self.images.get(reference)
                if image_id is None and reference in self.images.values():
                    image_id = reference
                stdout = f"{image_id}\n" if image_id else ""
                status = 0 if image_id else 1
            case ["podman", "save", *_, "--output", output, image_id]:
                role = next(
                    role
                    for role in ci_images.ROLE_IMAGES
                    if _image_id(role) == image_id
                )
                _write_archive(Path(output), role)
            case ["podman", "image", "exists", name]:
                status = 0 if name in self.images else 1
            case ["podman", "load", "--input", archive]:
                role = Path(archive).name.removesuffix(".oci.tar")
                image_id = self.loaded_override or _image_id(role)
                self.images[image_id] = image_id
                stdout = f"Loaded image: sha256:{image_id}\n"
            case ["podman", "tag", image_id, name]:
                self.images[name] = self.tag_override or image_id
            case ["bash", *_]:
                pass
            case _:
                raise AssertionError(f"unexpected command: {arguments}")
        return subprocess.CompletedProcess(arguments, status, stdout, "")


def _clock() -> float:
    return 0.0


def _build(tmp_path: Path, runtime: FakeRuntime, roles: tuple[str, ...]) -> Path:
    output = tmp_path / "github-output"
    ci_images.build(
        roles,
        tmp_path / "archives",
        output,
        runner=runtime,
        clock=_clock,
    )
    return output


def test_roles_parse_into_canonical_build_order() -> None:
    assert ci_images.parse_roles('["reverse-attempt","backend"]') == (
        "backend",
        "reverse-attempt",
    )
    assert ci_images.parse_roles('["frontend"]') == ("frontend",)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "JSON array"),
        ("[]", "non-empty"),
        ('{"backend": true}', "non-empty"),
        ("[1]", "non-empty"),
        ('["backend","database"]', "unknown image roles"),
        ('["backend","backend"]', "must not repeat"),
    ],
)
def test_roles_reject_malformed_or_unknown_input(text: str, message: str) -> None:
    with pytest.raises(ImageTransferError, match=message):
        ci_images.parse_roles(text)


def test_archive_names_are_role_bound() -> None:
    assert ci_images.archive_name("backend") == "backend.oci.tar"
    with pytest.raises(ImageTransferError, match="unknown image role"):
        ci_images.archive_name("database")


def test_build_runs_each_role_once_and_publishes_verified_ids(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = FakeRuntime()
    output = _build(tmp_path, runtime, ("backend", "frontend", "reverse-attempt"))

    assert output.read_text(encoding="utf-8").splitlines() == [
        f"{role}-image-id=sha256:{_image_id(role)}"
        for role in ("backend", "frontend", "reverse-attempt")
    ]
    builds = [
        call for call in runtime.calls if call[1].startswith("scripts/container/b")
    ]
    assert builds == [
        ["bash", "scripts/container/build.sh", "localhost/md-converter:ci"],
        [
            "bash",
            "scripts/container/build-frontend.sh",
            "localhost/md-converter-web:ci",
            "1789000000",
        ],
        [
            "bash",
            "scripts/container/build-reverse-attempt.sh",
            "localhost/md-converter-reverse-attempt:ci",
        ],
    ]
    pulls = [call for call in runtime.calls if "pull-base-images.sh" in " ".join(call)]
    assert pulls == [
        [
            "bash",
            "scripts/container/pull-base-images.sh",
            "Containerfile",
            "web/Containerfile",
            "containers/reverse-attempt/Containerfile",
        ]
    ]
    assert runtime.calls.index(pulls[0]) < runtime.calls.index(builds[0])
    assert (tmp_path / "archives").stat().st_mode & 0o777 == 0o700
    assert "built role=frontend" in capsys.readouterr().out


def test_build_selects_only_requested_roles(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    output = _build(tmp_path, runtime, ("frontend",))

    assert output.read_text(encoding="utf-8") == (
        f"frontend-image-id=sha256:{_image_id('frontend')}\n"
    )
    assert sorted(path.name for path in (tmp_path / "archives").iterdir()) == [
        "frontend.oci.tar"
    ]


def test_build_refuses_an_existing_archive_directory(tmp_path: Path) -> None:
    (tmp_path / "archives").mkdir()
    with pytest.raises(ImageTransferError, match="must not already exist"):
        _build(tmp_path, FakeRuntime(), ("backend",))


def test_build_requires_rootless_runtime_and_reviewed_timestamp(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    runtime.rootless = "false"
    with pytest.raises(ImageTransferError, match="rootless"):
        _build(tmp_path, runtime, ("backend",))

    runtime = FakeRuntime()
    runtime.failing = ("git",)
    (tmp_path / "other").mkdir()
    with pytest.raises(ImageTransferError, match="command failed"):
        _build(tmp_path / "other", runtime, ("backend",))


def test_build_rejects_a_non_numeric_commit_timestamp(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    original = runtime.__call__

    def runner(
        arguments: list[str], **options: Any
    ) -> subprocess.CompletedProcess[str]:
        if arguments[0] == "git":
            return subprocess.CompletedProcess(arguments, 0, "yesterday\n", "")
        return original(arguments, **options)

    with pytest.raises(ImageTransferError, match="timestamp"):
        ci_images.build(
            ("backend",), tmp_path / "archives", tmp_path / "out", runner=runner
        )


def test_build_fails_closed_when_a_build_or_export_fails(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    runtime.failing = ("bash", "scripts/container/build.sh")
    with pytest.raises(ImageTransferError, match="command failed"):
        _build(tmp_path, runtime, ("backend",))
    assert not (tmp_path / "github-output").exists()


def test_build_rejects_an_archive_that_does_not_carry_the_built_id(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    original = runtime.__call__

    def runner(
        arguments: list[str], **options: Any
    ) -> subprocess.CompletedProcess[str]:
        result = original(arguments, **options)
        if arguments[:2] == ["podman", "save"]:
            _write_archive(Path(arguments[-2]), "frontend")
        return result

    with pytest.raises(ImageTransferError, match="does not match the expected"):
        ci_images.build(
            ("backend",),
            tmp_path / "archives",
            tmp_path / "out",
            runner=runner,
            clock=_clock,
        )


def _archives(tmp_path: Path, roles: tuple[str, ...]) -> Path:
    directory = tmp_path / "downloaded"
    directory.mkdir()
    for role in roles:
        _write_archive(directory / ci_images.archive_name(role), role)
    return directory


def _expected(*roles: str) -> dict[str, str]:
    return {role: f"sha256:{_image_id(role)}" for role in roles}


@pytest.mark.parametrize("domain", sorted(IMAGE_DOMAIN_ROLES))
def test_load_tags_only_the_domain_roles_after_exact_id_checks(
    tmp_path: Path, domain: str, capsys: pytest.CaptureFixture[str]
) -> None:
    roles = IMAGE_DOMAIN_ROLES[domain]
    runtime = FakeRuntime()
    directory = _archives(tmp_path, ("backend", "frontend", "reverse-attempt"))

    loaded = ci_images.load(
        domain,
        directory,
        _expected("backend", "frontend", "reverse-attempt"),
        runner=runtime,
        clock=_clock,
    )

    assert loaded == {role: ci_images.ROLE_IMAGES[role] for role in roles}
    loads = [call for call in runtime.calls if call[:2] == ["podman", "load"]]
    assert sorted(Path(call[-1]).name for call in loads) == sorted(
        ci_images.archive_name(role) for role in roles
    )
    for role in roles:
        tag = ["podman", "tag", _image_id(role), ci_images.ROLE_IMAGES[role]]
        load = next(call for call in loads if call[-1].endswith(f"/{role}.oci.tar"))
        assert runtime.calls.index(load) < runtime.calls.index(tag)
    assert "load_seconds=" in capsys.readouterr().out


def test_load_rejects_unknown_domains_and_non_rootless_runtime(tmp_path: Path) -> None:
    with pytest.raises(ImageTransferError, match="consumes no final image"):
        ci_images.load("functional", tmp_path, {}, runner=FakeRuntime())
    runtime = FakeRuntime()
    runtime.rootless = "false"
    with pytest.raises(ImageTransferError, match="rootless"):
        ci_images.load("frontend", tmp_path, {}, runner=runtime)


@pytest.mark.parametrize("expected", ["", "latest", f"sha256:{'A' * 64}", "a" * 64])
def test_load_requires_a_published_build_id(tmp_path: Path, expected: str) -> None:
    directory = _archives(tmp_path, ("frontend",))
    with pytest.raises(ImageTransferError, match="no built image ID"):
        ci_images.load(
            "frontend", directory, {"frontend": expected}, runner=FakeRuntime()
        )


def test_load_requires_the_downloaded_archive(tmp_path: Path) -> None:
    directory = _archives(tmp_path, ())
    with pytest.raises(ImageTransferError, match="was not downloaded"):
        ci_images.load(
            "frontend", directory, _expected("frontend"), runner=FakeRuntime()
        )
    (directory / "frontend.oci.tar").symlink_to(tmp_path)
    with pytest.raises(ImageTransferError, match="was not downloaded"):
        ci_images.load(
            "frontend", directory, _expected("frontend"), runner=FakeRuntime()
        )


def test_load_rejects_a_substituted_archive_before_loading(tmp_path: Path) -> None:
    directory = _archives(tmp_path, ())
    _write_archive(directory / "frontend.oci.tar", "backend")
    runtime = FakeRuntime()
    with pytest.raises(ImageTransferError, match="does not match the expected"):
        ci_images.load("frontend", directory, _expected("frontend"), runner=runtime)
    assert not any(call[:2] == ["podman", "load"] for call in runtime.calls)


def test_load_rejects_a_corrupt_archive(tmp_path: Path) -> None:
    directory = _archives(tmp_path, ())
    (directory / "frontend.oci.tar").write_bytes(b"not an archive")
    with pytest.raises(ImageTransferError, match=r"frontend\.oci\.tar"):
        ci_images.load(
            "frontend", directory, _expected("frontend"), runner=FakeRuntime()
        )


def test_load_refuses_to_replace_an_existing_name(tmp_path: Path) -> None:
    directory = _archives(tmp_path, ("frontend",))
    runtime = FakeRuntime()
    runtime.images[ci_images.ROLE_IMAGES["frontend"]] = "0" * 64
    with pytest.raises(ImageTransferError, match="refusing to replace"):
        ci_images.load("frontend", directory, _expected("frontend"), runner=runtime)


def test_load_rejects_a_different_loaded_image(tmp_path: Path) -> None:
    directory = _archives(tmp_path, ("frontend",))
    runtime = FakeRuntime()
    runtime.loaded_override = "f" * 64
    with pytest.raises(ImageTransferError, match="did not report exactly"):
        ci_images.load("frontend", directory, _expected("frontend"), runner=runtime)
    assert not any(call[:2] == ["podman", "tag"] for call in runtime.calls)


def test_load_rejects_a_name_that_resolves_elsewhere(tmp_path: Path) -> None:
    directory = _archives(tmp_path, ("frontend",))
    runtime = FakeRuntime()
    runtime.tag_override = "e" * 64
    with pytest.raises(ImageTransferError, match="does not resolve"):
        ci_images.load("frontend", directory, _expected("frontend"), runner=runtime)


def test_load_rejects_a_loaded_id_that_inspects_differently(tmp_path: Path) -> None:
    directory = _archives(tmp_path, ("frontend",))
    runtime = FakeRuntime()
    original = runtime.__call__
    image_id = _image_id("frontend")

    def runner(
        arguments: list[str], **options: Any
    ) -> subprocess.CompletedProcess[str]:
        if arguments[:4] == ["podman", "image", "inspect", image_id]:
            return subprocess.CompletedProcess(arguments, 0, f"{'d' * 64}\n", "")
        return original(arguments, **options)

    with pytest.raises(ImageTransferError, match="loaded frontend image ID differs"):
        ci_images.load("frontend", directory, _expected("frontend"), runner=runner)


def test_load_rejects_an_ambiguous_inspection(tmp_path: Path) -> None:
    directory = _archives(tmp_path, ("frontend",))
    runtime = FakeRuntime()
    original = runtime.__call__

    def runner(
        arguments: list[str], **options: Any
    ) -> subprocess.CompletedProcess[str]:
        if arguments[:3] == ["podman", "image", "inspect"]:
            return subprocess.CompletedProcess(arguments, 0, "two\nids\n", "")
        return original(arguments, **options)

    with pytest.raises(ImageTransferError, match="no unambiguous image ID"):
        ci_images.load("frontend", directory, _expected("frontend"), runner=runner)


def test_command_line_build_and_load_report_failures(
    tmp_path: Path, mocker: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    build = mocker.patch.object(ci_images, "build")
    load = mocker.patch.object(ci_images, "load")
    assert (
        ci_images.main(
            [
                "build",
                "--roles",
                '["backend"]',
                "--output-directory",
                str(tmp_path / "a"),
                "--github-output",
                str(tmp_path / "o"),
            ]
        )
        == 0
    )
    build.assert_called_once_with(("backend",), tmp_path / "a", tmp_path / "o")
    assert (
        ci_images.main(
            [
                "load",
                "--domain",
                "frontend",
                "--archive-directory",
                str(tmp_path),
                "--frontend-image-id",
                "sha256:x",
            ]
        )
        == 0
    )
    load.assert_called_once_with(
        "frontend",
        tmp_path,
        {"backend": "", "frontend": "sha256:x", "reverse-attempt": ""},
    )
    load.side_effect = ImageTransferError("archive mismatch")
    assert (
        ci_images.main(
            ["load", "--domain", "frontend", "--archive-directory", str(tmp_path)]
        )
        == 1
    )
    assert "error: archive mismatch" in capsys.readouterr().err


def test_load_runs_every_domain_archive_concurrently(tmp_path: Path) -> None:
    roles = IMAGE_DOMAIN_ROLES["e2e-standalone-1"]
    directory = _archives(tmp_path, roles)
    runtime = FakeRuntime()
    # Each load waits until all of them are running; sequential loads time out.
    barrier = threading.Barrier(len(roles), timeout=5)

    def runner(
        arguments: list[str], **options: Any
    ) -> subprocess.CompletedProcess[str]:
        if arguments[:2] == ["podman", "load"]:
            barrier.wait()
        return runtime(arguments, **options)

    loaded = ci_images.load(
        "e2e-standalone-1", directory, _expected(*roles), runner=runner, clock=_clock
    )

    assert loaded == {role: ci_images.ROLE_IMAGES[role] for role in roles}


@pytest.mark.parametrize("failing", ["backend", "frontend", "reverse-attempt"])
def test_load_failure_of_one_role_tags_no_image(tmp_path: Path, failing: str) -> None:
    roles = IMAGE_DOMAIN_ROLES["e2e-distributed-2"]
    directory = _archives(tmp_path, roles)
    runtime = FakeRuntime()
    runtime.failing = (
        "podman",
        "load",
        "--input",
        str(directory / ci_images.archive_name(failing)),
    )

    with pytest.raises(ImageTransferError, match="command failed with status 3"):
        ci_images.load(
            "e2e-distributed-2", directory, _expected(*roles), runner=runtime
        )

    assert len([call for call in runtime.calls if call[:2] == ["podman", "load"]]) == 3
    assert not any(call[:2] == ["podman", "tag"] for call in runtime.calls)


@pytest.mark.parametrize("role", ["backend", "frontend", "reverse-attempt"])
def test_load_checks_every_role_before_loading_any_archive(
    tmp_path: Path, role: str
) -> None:
    roles = IMAGE_DOMAIN_ROLES["e2e-standalone-2"]
    missing = _archives(tmp_path, tuple(item for item in roles if item != role))
    runtime = FakeRuntime()
    with pytest.raises(ImageTransferError, match="was not downloaded"):
        ci_images.load("e2e-standalone-2", missing, _expected(*roles), runner=runtime)
    occupied = FakeRuntime()
    occupied.images[ci_images.ROLE_IMAGES[role]] = "0" * 64
    complete = tmp_path / "complete"
    complete.mkdir()
    for item in roles:
        _write_archive(complete / ci_images.archive_name(item), item)
    with pytest.raises(ImageTransferError, match="refusing to replace"):
        ci_images.load("e2e-standalone-2", complete, _expected(*roles), runner=occupied)

    for calls in (runtime.calls, occupied.calls):
        assert not any(call[:2] == ["podman", "load"] for call in calls)


def test_read_diskstats_keeps_read_written_and_busy_counters(tmp_path: Path) -> None:
    stats = tmp_path / "diskstats"
    stats.write_text(
        "   8       0 sda 10 0 400 5 20 0 800 9 0 30 14\n"
        "   8       1 sda1 1 0 2 3 4 0 6 7 0 9 10 11 12 13 14 15 16 17\n"
        "   7       0 loop0 1 2\n",
        encoding="ascii",
    )

    assert ci_images.read_diskstats(stats) == {
        "8:0": (400, 800, 30),
        "8:1": (2, 6, 9),
    }


def test_load_reports_the_store_layout_and_disk_traffic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    roles = IMAGE_DOMAIN_ROLES["e2e-standalone-1"]
    directory = _archives(tmp_path, roles)
    graph_root = tmp_path / "storage"
    graph_root.mkdir()
    device = ci_images._device(directory)
    runtime = FakeRuntime()
    runtime.info_json = json.dumps(
        {
            "version": {"Version": "5.4.2"},
            "store": {
                "graphDriverName": "overlay",
                "graphOptions": {"overlay.mount_program": "fuse-overlayfs"},
                "graphRoot": str(graph_root),
                "imageCopyTmpDir": str(tmp_path),
            },
        }
    )
    samples = iter([{device: (0, 0, 0)}, {device: (4 * 2048, 6 * 2048, 500)}])
    times = iter([0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 2.0, 2.0])

    ci_images.load(
        "e2e-standalone-1",
        directory,
        _expected(*roles),
        runner=runtime,
        clock=lambda: next(times),
        diskstats=lambda: next(samples),
    )

    output = capsys.readouterr().out
    assert "storage podman=5.4.2 driver=overlay " in output
    assert '"overlay.mount_program": "fuse-overlayfs"' in output
    assert f"archives={directory}@{device}" in output
    assert f"graph_root={graph_root}@{device}" in output
    assert f"disk device={device} read_mib=4 written_mib=6 busy_percent=25" in output


def test_storage_diagnostics_never_block_a_load(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    roles = IMAGE_DOMAIN_ROLES["e2e-standalone-2"]
    directory = _archives(tmp_path, roles)
    runtime = FakeRuntime()
    runtime.info_json = json.dumps({"store": {"graphRoot": str(tmp_path)}})

    def unreadable() -> dict[str, tuple[int, int, int]]:
        raise OSError("no diskstats")

    loaded = ci_images.load(
        "e2e-standalone-2",
        directory,
        _expected(*roles),
        runner=runtime,
        clock=_clock,
        diskstats=unreadable,
    )

    assert loaded == {role: ci_images.ROLE_IMAGES[role] for role in roles}
    output = capsys.readouterr().out
    assert "storage podman=unknown driver=unknown" in output
    assert "storage diagnostics unavailable: no diskstats" in output


def test_storage_report_marks_a_missing_path_unknown(tmp_path: Path) -> None:
    assert ci_images._device(tmp_path / "missing") == "unknown"


@pytest.mark.parametrize("after", [{}, None])
def test_disk_traffic_skips_unmeasured_or_unreadable_devices(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    after: dict[str, tuple[int, int, int]] | None,
) -> None:
    roles = IMAGE_DOMAIN_ROLES["e2e-standalone-2"]
    directory = _archives(tmp_path, roles)
    runtime = FakeRuntime()
    runtime.info_json = json.dumps({"store": {"graphRoot": str(tmp_path)}})
    samples: list[dict[str, tuple[int, int, int]] | None] = [{}, after]

    def diskstats() -> dict[str, tuple[int, int, int]]:
        sample = samples.pop(0)
        if sample is None:
            raise OSError("diskstats vanished")
        return sample

    ci_images.load(
        "e2e-standalone-2",
        directory,
        _expected(*roles),
        runner=runtime,
        clock=_clock,
        diskstats=diskstats,
    )

    output = capsys.readouterr().out
    assert "disk device=" not in output
    assert ("diskstats vanished" in output) is (after is None)


class EnvRecorder:
    """Record the environment and copy directory seen by each `podman load`."""

    def __init__(self, runtime: FakeRuntime) -> None:
        self.runtime = runtime
        self.environments: list[dict[str, str] | None] = []
        self.present: list[bool] = []

    def __call__(
        self, arguments: list[str], **options: Any
    ) -> subprocess.CompletedProcess[str]:
        if arguments[:2] == ["podman", "load"]:
            env = options.get("env")
            self.environments.append(env)
            self.present.append(env is not None and Path(env["TMPDIR"]).is_dir())
        return self.runtime(arguments, **options)


def test_load_stages_archives_in_a_private_memory_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    roles = IMAGE_DOMAIN_ROLES["e2e-standalone-1"]
    directory = _archives(tmp_path, roles)
    memory = tmp_path / "shm"
    memory.mkdir()
    runtime = FakeRuntime()
    runtime.info_json = json.dumps({"store": {"graphRoot": str(tmp_path)}})
    recorder = EnvRecorder(runtime)

    loaded = ci_images.load(
        "e2e-standalone-1",
        directory,
        _expected(*roles),
        runner=recorder,
        clock=_clock,
        memory_root=memory,
    )

    assert loaded == {role: ci_images.ROLE_IMAGES[role] for role in roles}
    copy_dirs = {Path(env["TMPDIR"]) for env in recorder.environments if env}
    assert len(recorder.environments) == len(roles)
    assert len(copy_dirs) == 1
    copy_dir = copy_dirs.pop()
    assert copy_dir.parent == memory
    assert copy_dir.name.startswith("markweave-load-")
    assert recorder.present == [True] * len(roles)
    assert not copy_dir.exists()
    output = capsys.readouterr().out
    assert f"load_copy_dir={copy_dir} " in output
    assert f"image_copy_tmp_dir={copy_dir}@" in output


@pytest.mark.parametrize("free", [0, None])
def test_load_keeps_the_default_copy_directory_without_memory_room(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mocker: Any,
    free: int | None,
) -> None:
    roles = IMAGE_DOMAIN_ROLES["e2e-standalone-2"]
    directory = _archives(tmp_path, roles)
    memory = tmp_path / "shm"
    memory.mkdir()
    archive_bytes = sum(path.stat().st_size for path in directory.iterdir())
    required = 2 * archive_bytes + ci_images.MEMORY_TMP_MARGIN_BYTES
    usage = mocker.patch.object(ci_images.shutil, "disk_usage")
    if free is None:
        usage.side_effect = OSError("no statvfs")
    else:
        usage.return_value.free = required - 1
    recorder = EnvRecorder(FakeRuntime())

    ci_images.load(
        "e2e-standalone-2",
        directory,
        _expected(*roles),
        runner=recorder,
        clock=_clock,
        memory_root=memory,
    )

    assert recorder.environments == [None] * len(roles)
    assert list(memory.iterdir()) == []
    output = capsys.readouterr().out
    assert f"load_copy_dir=default root={memory} " in output
    assert f"required_mib={required // ci_images.MIB}" in output


def test_load_without_a_memory_root_keeps_the_default_copy_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    roles = IMAGE_DOMAIN_ROLES["e2e-standalone-2"]
    recorder = EnvRecorder(FakeRuntime())

    ci_images.load(
        "e2e-standalone-2",
        _archives(tmp_path, roles),
        _expected(*roles),
        runner=recorder,
        clock=_clock,
    )

    assert recorder.environments == [None] * len(roles)
    assert f"load_copy_dir=default root={tmp_path / 'no-shm'} " in (
        capsys.readouterr().out
    )


def test_load_failure_removes_the_memory_copy_directory(tmp_path: Path) -> None:
    roles = IMAGE_DOMAIN_ROLES["e2e-distributed-2"]
    directory = _archives(tmp_path, roles)
    memory = tmp_path / "shm"
    memory.mkdir()
    runtime = FakeRuntime()
    runtime.failing = (
        "podman",
        "load",
        "--input",
        str(directory / ci_images.archive_name("frontend")),
    )
    recorder = EnvRecorder(runtime)

    with pytest.raises(ImageTransferError, match="command failed with status 3"):
        ci_images.load(
            "e2e-distributed-2",
            directory,
            _expected(*roles),
            runner=recorder,
            memory_root=memory,
        )

    assert recorder.present == [True] * len(roles)
    assert list(memory.iterdir()) == []


def test_read_cpustats_keeps_busy_iowait_and_total_jiffies(tmp_path: Path) -> None:
    stats = tmp_path / "stat"
    stats.write_text(
        "cpu  100 5 50 800 40 3 2 0 0 0\ncpu0 1 2 3 4 5 6 7 8 9 10\n",
        encoding="ascii",
    )

    assert ci_images.read_cpustats(stats) == (160, 40, 1000)


def test_load_reports_cpu_busy_and_iowait_shares(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    roles = IMAGE_DOMAIN_ROLES["e2e-standalone-2"]
    directory = _archives(tmp_path, roles)
    runtime = FakeRuntime()
    runtime.info_json = json.dumps({"store": {"graphRoot": str(tmp_path)}})
    device = ci_images._device(directory)
    disks = iter([{device: (0, 0, 0)}, {device: (0, 0, 0)}])
    cpus = iter([(100, 10, 1000), (850, 60, 2000)])

    ci_images.load(
        "e2e-standalone-2",
        directory,
        _expected(*roles),
        runner=runtime,
        clock=_clock,
        diskstats=lambda: next(disks),
        cpustats=lambda: next(cpus),
    )

    assert "cpu busy_percent=75 iowait_percent=5 cpus=" in capsys.readouterr().out


def test_container_domain_compares_gzip_and_zstd_solo_loads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    roles = IMAGE_DOMAIN_ROLES["container"]
    directory = _archives(tmp_path, roles)
    runtime = FakeRuntime()

    ci_images.load(
        "container", directory, _expected(*roles), runner=runtime, clock=_clock
    )

    pushes = [call for call in runtime.experiment if call[1] == "push"]
    loads = [call for call in runtime.experiment if "load" in call]
    removals = [call for call in runtime.experiment if call[1] == "unshare"]
    assert len(pushes) == 1
    assert len(loads) == len(removals) == 2
    assert all(Path(call[3]).parent == Path(call[5]).parent for call in loads)
    output = capsys.readouterr().out
    assert "experiment role=" in output
    for variant in ("gzip", "zstd"):
        assert f"variant={variant} " in output
    assert "layers=0 media= largest_mib=0 solo_load_seconds=0.0" in output


def test_other_domains_skip_the_compression_experiment(tmp_path: Path) -> None:
    roles = IMAGE_DOMAIN_ROLES["e2e-standalone-1"]
    runtime = FakeRuntime()

    ci_images.load(
        "e2e-standalone-1",
        _archives(tmp_path, roles),
        _expected(*roles),
        runner=runtime,
        clock=_clock,
    )

    assert runtime.experiment == []


def test_compression_experiment_failure_never_blocks_a_load(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    roles = IMAGE_DOMAIN_ROLES["container"]
    runtime = FakeRuntime()

    def runner(
        arguments: list[str], **options: Any
    ) -> subprocess.CompletedProcess[str]:
        if arguments[:2] == ["podman", "push"]:
            return subprocess.CompletedProcess(arguments, 125, "", "no zstd")
        return runtime(arguments, **options)

    loaded = ci_images.load(
        "container",
        _archives(tmp_path, roles),
        _expected(*roles),
        runner=runner,
        clock=_clock,
    )

    assert loaded == {role: ci_images.ROLE_IMAGES[role] for role in roles}
    assert "experiment unavailable: command failed with status 125" in (
        capsys.readouterr().out
    )


def test_layer_media_rejects_a_non_regular_member(tmp_path: Path) -> None:
    archive = tmp_path / "directory.oci.tar"
    with tarfile.open(archive, "w:") as entries:
        member = tarfile.TarInfo("index.json")
        member.type = tarfile.DIRTYPE
        entries.addfile(member)

    with pytest.raises(ValueError, match="not a regular archive member"):
        ci_images._layer_media(archive)
