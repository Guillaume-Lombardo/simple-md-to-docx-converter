"""Static contracts for the T20 final-image assets."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tarfile
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from scripts.container import (
    api_workflow_smoke,
    integrity,
    summarize_supply_chain,
    verify_supply_chain,
)
from tests.e2e.recovery_cli_setup import _listed_object_count

pytestmark = pytest.mark.unit
LEGACY_TEMPLATE_ID = "00000000-0000-4000-8000-000000000029"


def smoke_pdf_client(
    mocker,
    schema_version: object,
    job_template_mode: str | None,
    manifest_template_mode: str | None = None,
):
    pdf = b"%PDF-legacy"
    payload: dict[str, object] = {
        "schema_version": schema_version,
        "output_format": "pdf",
        "output_pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        "output_pdf_bytes": len(pdf),
        "template_id": LEGACY_TEMPLATE_ID,
        "template_version": "3",
        "template_sha256": "a" * 64,
    }
    job: dict[str, object] = {
        "id": "legacy-job",
        "template_id": LEGACY_TEMPLATE_ID,
    }
    if job_template_mode is not None:
        job["template_mode"] = job_template_mode
    resolved_manifest_mode = manifest_template_mode or job_template_mode
    if resolved_manifest_mode is not None:
        payload["template_mode"] = resolved_manifest_mode
    client = mocker.Mock(spec=api_workflow_smoke.Client)
    client.request.side_effect = (
        (200, {"cache-control": "private, no-store"}, pdf),
        (200, {}, json.dumps(payload).encode()),
    )
    return client, job


def test_container_domain_is_active_and_runs_rootless_harness() -> None:
    registry = json.loads(Path(".github/ci/domains.json").read_text(encoding="utf-8"))
    assert registry["container"] == {
        "activation_ticket": "T20",
        "command": ["bash", "scripts/container/run-ci.sh"],
        "status": "active",
    }


@pytest.mark.parametrize(
    ("job_template_mode", "accepted"), ((None, True), ("versioned", False))
)
def test_container_workflow_limits_legacy_manifest_to_pre_t29_api(
    mocker, job_template_mode: str | None, accepted: bool
) -> None:
    client, job = smoke_pdf_client(mocker, 1, job_template_mode)
    if accepted:
        api_workflow_smoke.validate_result(client, job, "pdf")
    else:
        with pytest.raises(RuntimeError, match="manifest invariants mismatch"):
            api_workflow_smoke.validate_result(client, job, "pdf")


@pytest.mark.parametrize(
    ("schema_version", "job_template_mode"),
    ((True, None), (1.0, None), (2.0, "versioned")),
)
def test_container_workflow_rejects_non_integer_manifest_schema(
    mocker, schema_version: object, job_template_mode: str | None
) -> None:
    client, job = smoke_pdf_client(mocker, schema_version, job_template_mode)

    with pytest.raises(RuntimeError, match="manifest invariants mismatch"):
        api_workflow_smoke.validate_result(client, job, "pdf")


def test_container_workflow_rejects_mixed_v1_v2_manifest(mocker) -> None:
    client, job = smoke_pdf_client(mocker, 1, None, "versioned")

    with pytest.raises(RuntimeError, match="manifest invariants mismatch"):
        api_workflow_smoke.validate_result(client, job, "pdf")


@pytest.mark.parametrize(
    "script",
    [
        "container/entrypoint.sh",
        "container/preflight.sh",
        "scripts/container/build.sh",
        "scripts/container/blocking-mmdc.sh",
        "scripts/container/api-smoke.sh",
        "scripts/container/distributed-api-smoke.sh",
        "scripts/container/recovery-cli-smoke.sh",
        "scripts/container/run-ci.sh",
        "scripts/container/smoke.sh",
        "scripts/container/supply-chain.sh",
    ],
)
def test_container_shell_assets_are_syntactically_valid(script: str) -> None:
    subprocess.run(["bash", "-n", script], check=True)


def test_final_image_pins_all_downloaded_artifacts() -> None:
    containerfile = Path("Containerfile").read_text(encoding="utf-8")
    assert "ubi9/python-314@sha256:" in containerfile
    for artifact in ("PANDOC", "CHROME", "LIBREOFFICE", "UV"):
        assert f"ARG {artifact}_SHA256=" in containerfile
    assert "rpm --checksig /tmp/google-chrome.rpm" in containerfile
    assert "RPM_INVENTORY_SHA256" in containerfile
    assert "uv sync --locked --no-dev --no-editable --extra all" in containerfile


def test_recovery_smoke_is_a_required_ci_and_release_final_image_gate() -> None:
    """Both reviewed final-image paths execute the complete recovery contract."""

    command = 'bash scripts/container/recovery-cli-smoke.sh "$image"'
    run_ci = Path("scripts/container/run-ci.sh").read_text(encoding="utf-8")
    ci_command = command.replace('"$image"', '"$final_image"')
    release = yaml.safe_load(
        Path(".github/workflows/container-release.yml").read_text(encoding="utf-8")
    )
    release_run = next(
        step["run"]
        for step in release["jobs"]["build-and-publish"]["steps"]
        if step["name"] == "Build and validate the final rootless image"
    )

    assert run_ci.count(ci_command) == 1
    assert (
        run_ci.index('bash scripts/container/build.sh "$final_image"')
        < run_ci.index(ci_command)
        < run_ci.index(
            'bash scripts/container/supply-chain.sh "$final_image" artifacts/container'
        )
    )
    assert release_run.count(command) == 1
    assert release_run.index(
        'bash scripts/container/build.sh "$image"'
    ) < release_run.index(command)


def test_recovery_smoke_uses_private_volume_and_real_rollback() -> None:
    smoke = Path("scripts/container/recovery-cli-smoke.sh").read_text(encoding="utf-8")

    assert 'chmod 0777 "$workspace"' not in smoke
    assert smoke.count('"$workspace:/e2e:U,Z"') == 2
    assert "--entrypoint markweave" not in smoke
    assert '"$image" "$@"' in smoke
    for boundary in (
        '--user "$runtime_uid:0"',
        "--read-only",
        "--cap-drop=all",
        "--security-opt=no-new-privileges",
        "--pids-limit=256",
        "--tmpfs /work:",
    ):
        assert boundary in smoke
    assert '.error.message == "Distributed restore target is not isolated"' in smoke
    assert 'run_setup "${common_s3[@]}" -- distributed-cleanup-verify' in smoke


def test_recovery_smoke_does_not_require_optional_s3_key_count() -> None:
    assert _listed_object_count({}) == 0
    assert _listed_object_count({"Contents": []}) == 0
    assert _listed_object_count({"Contents": [{"Key": "one"}]}) == 1
    with pytest.raises(RuntimeError, match="listing is invalid"):
        _listed_object_count({"Contents": "not-a-list"})


def test_final_image_does_not_bake_canonical_runtime_aliases() -> None:
    containerfile = Path("Containerfile").read_text(encoding="utf-8")
    smoke = Path("scripts/container/api-smoke.sh").read_text(encoding="utf-8")

    assert "MARKWEAVE_HOST=" not in containerfile
    assert "MARKWEAVE_PORT=" not in containerfile
    assert 'legacy_settings+=("${setting/MARKWEAVE_/MD_CONVERTER_}")' in smoke
    assert "--env MD_CONVERTER_HOST=127.0.0.1" in smoke
    assert "--env MD_CONVERTER_PORT=18080" in smoke
    assert 'urllib.request.urlopen("http://127.0.0.1:18080/health/live"' in smoke
    assert (
        '(Settings.load().host, Settings.load().port) == ("127.0.0.1", 18080)' in smoke
    )


def test_final_image_version_comes_from_project_metadata() -> None:
    containerfile = Path("Containerfile").read_text(encoding="utf-8")
    build = Path("scripts/container/build.sh").read_text(encoding="utf-8")
    smoke = Path("scripts/container/smoke.sh").read_text(encoding="utf-8")

    assert "ARG APPLICATION_VERSION\n" in containerfile
    assert 'org.opencontainers.image.version="${APPLICATION_VERSION}"' in containerfile
    assert "markweave.__version__ == sys.argv[1]" in containerfile
    assert 'application_version="$(uv version --short --locked)"' in build
    assert "env -u SOURCE_DATE_EPOCH podman build" in build
    assert '--build-arg "APPLICATION_VERSION=$application_version"' in build
    assert 'application_version="$(uv version --short --locked)"' in smoke
    assert '--env "EXPECTED_APPLICATION_VERSION=$application_version"' in smoke
    assert 'os.environ[\\"EXPECTED_APPLICATION_VERSION\\"]' in smoke


def test_final_image_e2e_pulls_and_verifies_the_pinned_base_before_build() -> None:
    script = Path("scripts/e2e/run.sh").read_text(encoding="utf-8")
    containerfile = Path("Containerfile").read_text(encoding="utf-8")
    digest = "194df4e35e0e5467e1b57266f4d61f821e1b1f567135f074d23066d3604ae653"
    assert f"sha256:{digest}" in containerfile
    assert f"readonly base_digest=sha256:{digest}" in script
    pull = 'podman pull --quiet "$base_image"'
    verification = (
        'test "$(podman image inspect "$base_image" --format \'{{.Digest}}\')" '
        '= "$base_digest"'
    )
    build = 'bash scripts/container/build.sh "$image"'
    assert verification in script
    assert script.index(pull) < script.index(verification) < script.index(build)


def test_entrypoint_delegates_every_supported_command_to_markweave() -> None:
    entrypoint = Path("container/entrypoint.sh").read_text(encoding="utf-8")
    assert "md-converter-preflight" in entrypoint
    assert 'exec /opt/md-converter/venv/bin/markweave "$@"' in entrypoint
    assert "api|embedded-worker|external-worker" not in entrypoint
    assert "markweave.runtime" not in entrypoint
    containerfile = Path("Containerfile").read_text(encoding="utf-8")
    assert 'ENTRYPOINT ["md-converter-entrypoint"]' in containerfile
    assert 'CMD ["serve"]' in containerfile


def test_final_image_e2e_uses_cli_roles_and_default_remote_client_entrypoint() -> None:
    runner = Path("scripts/e2e/run.sh").read_text(encoding="utf-8")
    assert "application_mode=serve" in runner
    assert '"$image" worker' in runner
    assert 'arguments[-1] == b"serve"' in runner
    assert 'arguments[-1] == b"worker"' in runner
    assert runner.count('"$image" --json health') == 2
    for legacy_mode in ("application_mode=api", "application_mode=embedded-worker"):
        assert legacy_mode not in runner


def test_distributed_api_deployment_uses_cli_serve_role() -> None:
    documents = tuple(
        yaml.safe_load_all(
            Path("deploy/distributed.yaml.example").read_text(encoding="utf-8")
        )
    )
    application = documents[0]["spec"]["template"]["spec"]["containers"][0]
    assert application["args"] == ["serve"]


def test_smoke_enforces_rootless_read_only_bounded_runtime() -> None:
    smoke = Path("scripts/container/smoke.sh").read_text(encoding="utf-8")
    for contract in (
        "--read-only",
        "--cap-drop=all",
        "--security-opt=no-new-privileges",
        "--memory=768m",
        "--pids-limit=256",
        "--tmpfs /tmp:",
        "--tmpfs /work:",
        "--shm-size=128m",
    ):
        assert contract in smoke


@pytest.mark.parametrize(
    ("manifest", "mode"),
    [
        ("deploy/standalone.yaml.example", "serve"),
        ("deploy/distributed.yaml.example", "worker"),
    ],
)
def test_deployment_examples_apply_worker_security_and_t18_limits(
    manifest: str, mode: str
) -> None:
    documents = tuple(yaml.safe_load_all(Path(manifest).read_text(encoding="utf-8")))
    worker = next(
        container
        for document in documents
        for container in document["spec"]["template"]["spec"]["containers"]
        if container["args"] == [mode]
    )
    security = worker["securityContext"]
    assert security == {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "readOnlyRootFilesystem": True,
        "runAsNonRoot": True,
    }
    assert worker["resources"]["limits"] == {
        "memory": "${WORKER_MEMORY_BUDGET_BYTES}",
        "ephemeral-storage": "${WORKER_EPHEMERAL_STORAGE_BUDGET_BYTES}",
    }


def test_distributed_test_profile_is_provider_neutral_rustfs() -> None:
    deployment = Path("deploy/rustfs-ci.yaml").read_text(encoding="utf-8")
    assert "ghcr.io/rustfs/rustfs:" in deployment
    assert "minio" not in deployment.casefold()


def test_distributed_smoke_covers_active_shutdown_and_lease_recovery() -> None:
    smoke = Path("scripts/container/distributed-api-smoke.sh").read_text(
        encoding="utf-8"
    )
    for contract in (
        "--submit-blocking-job",
        "blocking-mmdc.sh",
        "podman stop --time 8",
        "{{.State.Pid}}",
        "--assert-running-job",
        "--recover-job",
    ):
        assert contract in smoke


def test_supply_chain_retains_complete_scan_and_ci_evidence() -> None:
    script = Path("scripts/container/supply-chain.sh").read_text(encoding="utf-8")
    workflow = yaml.safe_load(
        Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    assert "--only-fixed" not in script
    assert "vulnerabilities.json" in script
    heavy_steps = workflow["jobs"]["heavy"]["steps"]
    upload = next(
        step
        for step in heavy_steps
        if step["name"] == "Retain final-image verification evidence"
    )
    assert upload["if"] == "${{ always() && matrix.domain == 'container' }}"
    assert upload["with"]["retention-days"] == 30
    assert upload["with"]["if-no-files-found"] == "error"
    for artifact in (
        "sbom.cdx.json",
        "sbom.spdx.json",
        "vulnerabilities.json",
        "image-metadata.json",
    ):
        assert artifact in upload["with"]["path"]
    assert workflow["permissions"] == {"contents": "read"}


def test_supply_chain_produces_in_private_staging_before_atomic_publication() -> None:
    script = Path("scripts/container/supply-chain.sh").read_text(encoding="utf-8")
    for contract in (
        "umask 077",
        '[[ -e "$output_directory" || -L "$output_directory" ]]',
        '[[ -L "$output_parent" ]]',
        'mktemp -d "$output_parent/.supply-chain.XXXXXX"',
        'podman save --format oci-archive --output "$image_archive" "$image_id"',
        'readonly canonical_image_id="sha256:$image_id"',
        'mv --no-target-directory -- "$staging_directory" "$output_directory"',
        "release_bundle_manifest_sha256=%s",
    ):
        assert contract in script
    assert (
        script.index("mktemp -d")
        < script.index("podman save")
        < script.index("mv --no-target-directory")
    )


def test_supply_chain_summary_gates_fixable_and_records_unfixed_critical(
    mocker, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("image.oci.tar", "sbom.cdx.json", "sbom.spdx.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    report = {
        "matches": [
            {
                "artifact": {"name": "fixable"},
                "vulnerability": {
                    "id": "CVE-FIXED",
                    "severity": "Critical",
                    "fix": {"versions": ["2"]},
                },
            },
            {
                "artifact": {"name": "unfixed"},
                "vulnerability": {
                    "id": "CVE-UNFIXED",
                    "severity": "Critical",
                    "fix": {"versions": []},
                },
            },
        ]
    }
    (tmp_path / "vulnerabilities.json").write_text(json.dumps(report), encoding="utf-8")
    inspected = mocker.patch("scripts.container.summarize_supply_chain.subprocess.run")
    inspected.return_value.stdout = json.dumps([{"Id": "2" * 64, "Size": 123}])
    mocker.patch(
        "scripts.container.summarize_supply_chain.oci_identity",
        return_value=(f"sha256:{'1' * 64}", f"sha256:{'2' * 64}"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "summary",
            "--image",
            "image:test",
            "--artifacts",
            str(tmp_path),
            "--expected-image-id",
            f"sha256:{'2' * 64}",
        ],
    )

    assert summarize_supply_chain.main() == 1
    inspected.assert_called_once_with(
        ["/usr/bin/podman", "image", "inspect", "2" * 64],
        check=True,
        capture_output=True,
        text=True,
    )
    evidence = json.loads((tmp_path / "image-metadata.json").read_text())
    assert evidence["vulnerabilities"]["counts_by_severity"] == {"Critical": 2}
    assert evidence["vulnerabilities"]["critical_with_fix"][0]["id"] == "CVE-FIXED"
    assert evidence["vulnerabilities"]["critical_without_fix"][0]["id"] == "CVE-UNFIXED"


def test_release_summary_rejects_unfixed_critical_and_archive_identity_mismatch(
    mocker, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("image.oci.tar", "sbom.cdx.json", "sbom.spdx.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    (tmp_path / "vulnerabilities.json").write_text(
        json.dumps(
            {
                "matches": [
                    {
                        "artifact": {"name": "unfixed"},
                        "vulnerability": {
                            "id": "CVE-UNFIXED",
                            "severity": "Critical",
                            "fix": {"versions": []},
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    inspect = mocker.patch("scripts.container.summarize_supply_chain.subprocess.run")
    inspect.return_value.stdout = json.dumps([{"Id": "2" * 64, "Size": 123}])
    identity = mocker.patch(
        "scripts.container.summarize_supply_chain.oci_identity",
        return_value=(f"sha256:{'1' * 64}", f"sha256:{'2' * 64}"),
    )
    arguments = [
        "summary",
        "--image",
        "image:test",
        "--artifacts",
        str(tmp_path),
        "--expected-image-id",
        f"sha256:{'2' * 64}",
    ]
    monkeypatch.setattr(sys, "argv", arguments)
    assert summarize_supply_chain.main() == 0

    monkeypatch.setattr(sys, "argv", [*arguments, "--release"])
    assert summarize_supply_chain.main() == 1

    identity.return_value = (f"sha256:{'1' * 64}", f"sha256:{'3' * 64}")
    assert summarize_supply_chain.main() == 1


def _write_release_bundle(path: Path) -> str:
    source_names = (
        "image.oci.tar",
        "sbom.cdx.json",
        "sbom.spdx.json",
        "vulnerabilities.json",
    )
    for name in source_names:
        (path / name).write_bytes(f"content for {name}".encode())
    source_digests = {name: integrity.sha256_file(path / name) for name in source_names}
    (path / "image-metadata.json").write_text(
        json.dumps({"artifacts": source_digests}), encoding="utf-8"
    )
    return verify_supply_chain.create_manifest(path)


def test_release_bundle_verifier_accepts_exact_digest_bound_artifacts(
    tmp_path: Path,
) -> None:
    expected = _write_release_bundle(tmp_path)

    verify_supply_chain.verify_bundle(tmp_path, expected_manifest_sha256=expected)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda path: (path / "image.oci.tar").write_bytes(b"changed"), "mismatch"),
        (
            lambda path: (path / "release-bundle.sha256").write_text(
                "unsafe", encoding="ascii"
            ),
            "trust anchor mismatch",
        ),
        (
            lambda path: (path / "release-bundle.sha256").write_text(
                (path / "release-bundle.sha256").read_text(encoding="ascii")
                + ("0" * 64)
                + "  extra.txt\n",
                encoding="ascii",
            ),
            "trust anchor mismatch",
        ),
        (lambda path: (path / "extra.txt").write_text("extra"), "exact artifact set"),
    ],
)
def test_release_bundle_verifier_rejects_substitution_and_malformed_evidence(
    tmp_path: Path, mutation: Callable[[Path], object], message: str
) -> None:
    expected = _write_release_bundle(tmp_path)
    mutation(tmp_path)

    with pytest.raises(verify_supply_chain.SupplyChainVerificationError, match=message):
        verify_supply_chain.verify_bundle(tmp_path, expected_manifest_sha256=expected)


def test_release_bundle_verifier_rejects_internally_inconsistent_metadata(
    tmp_path: Path,
) -> None:
    _write_release_bundle(tmp_path)
    metadata_path = tmp_path / "image-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["artifacts"]["image.oci.tar"] = "0" * 64
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    manifest_path = tmp_path / "release-bundle.sha256"
    lines = manifest_path.read_text(encoding="ascii").splitlines()
    manifest_path.write_text(
        "\n".join(
            (
                f"{integrity.sha256_file(metadata_path)}  image-metadata.json"
                if line.endswith("  image-metadata.json")
                else line
            )
            for line in lines
        )
        + "\n",
        encoding="ascii",
    )

    forged_anchor = integrity.sha256_file(manifest_path)
    with pytest.raises(
        verify_supply_chain.SupplyChainVerificationError,
        match="image metadata digest mismatch",
    ):
        verify_supply_chain.verify_bundle(
            tmp_path, expected_manifest_sha256=forged_anchor
        )


def test_release_bundle_verifier_rejects_symlinked_artifact(tmp_path: Path) -> None:
    expected = _write_release_bundle(tmp_path)
    target = tmp_path.parent / f"{tmp_path.name}-outside.tar"
    target.write_bytes((tmp_path / "image.oci.tar").read_bytes())
    (tmp_path / "image.oci.tar").unlink()
    (tmp_path / "image.oci.tar").symlink_to(target)

    with pytest.raises(
        verify_supply_chain.SupplyChainVerificationError, match="artifact is unsafe"
    ):
        verify_supply_chain.verify_bundle(tmp_path, expected_manifest_sha256=expected)


def test_release_bundle_verifier_rejects_forged_bundle_without_trusted_anchor(
    tmp_path: Path,
) -> None:
    trusted_anchor = _write_release_bundle(tmp_path)
    (tmp_path / "image.oci.tar").write_bytes(b"coherent forgery")
    metadata = json.loads((tmp_path / "image-metadata.json").read_text())
    metadata["artifacts"]["image.oci.tar"] = integrity.sha256_file(
        tmp_path / "image.oci.tar"
    )
    (tmp_path / "image-metadata.json").write_text(json.dumps(metadata))
    (tmp_path / "release-bundle.sha256").unlink()
    verify_supply_chain.create_manifest(tmp_path)

    with pytest.raises(
        verify_supply_chain.SupplyChainVerificationError, match="trust anchor mismatch"
    ):
        verify_supply_chain.verify_bundle(
            tmp_path, expected_manifest_sha256=trusted_anchor
        )


def test_release_bundle_verifier_rejects_symlinked_manifest_and_directory(
    tmp_path: Path,
) -> None:
    expected = _write_release_bundle(tmp_path)
    manifest = tmp_path / "release-bundle.sha256"
    external = tmp_path.parent / f"{tmp_path.name}-manifest"
    manifest.rename(external)
    manifest.symlink_to(external)
    with pytest.raises(
        verify_supply_chain.SupplyChainVerificationError, match="manifest is unsafe"
    ):
        verify_supply_chain.verify_bundle(tmp_path, expected_manifest_sha256=expected)

    manifest.unlink()
    external.rename(manifest)
    directory_link = tmp_path.parent / f"{tmp_path.name}-link"
    directory_link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(
        verify_supply_chain.SupplyChainVerificationError, match="directory is unsafe"
    ):
        verify_supply_chain.verify_bundle(
            directory_link, expected_manifest_sha256=expected
        )


def test_sha256_file_streams_without_path_read_bytes(mocker, tmp_path: Path) -> None:
    artifact = tmp_path / "large.oci.tar"
    payload = b"a" * (2 * 1024 * 1024 + 17)
    artifact.write_bytes(payload)
    read_bytes = mocker.patch.object(Path, "read_bytes", side_effect=AssertionError)

    assert integrity.sha256_file(artifact) == hashlib.sha256(payload).hexdigest()
    read_bytes.assert_not_called()


def _add_tar_bytes(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(payload)
    archive.addfile(member, io.BytesIO(payload))


def test_oci_identity_is_derived_from_verified_archive(tmp_path: Path) -> None:
    config = json.dumps({"rootfs": {"type": "layers", "diff_ids": []}}).encode()
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
    archive_path = tmp_path / "image.oci.tar"
    with tarfile.open(archive_path, "w:") as archive:
        _add_tar_bytes(archive, "index.json", index)
        _add_tar_bytes(archive, f"blobs/sha256/{manifest_hex}", manifest)
        _add_tar_bytes(archive, f"blobs/sha256/{config_hex}", config)

    assert integrity.oci_identity(archive_path) == (
        f"sha256:{manifest_hex}",
        f"sha256:{config_hex}",
    )

    tampered = json.dumps({"rootfs": {"type": "layers", "diff_ids": ["bad"]}}).encode()
    with tarfile.open(archive_path, "w:") as archive:
        _add_tar_bytes(archive, "index.json", index)
        _add_tar_bytes(archive, f"blobs/sha256/{manifest_hex}", manifest)
        _add_tar_bytes(archive, f"blobs/sha256/{config_hex}", tampered)
    with pytest.raises(integrity.IntegrityError, match="digest mismatch"):
        integrity.oci_identity(archive_path)
