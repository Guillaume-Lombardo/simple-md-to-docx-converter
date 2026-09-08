"""Record final-image evidence and gate fixable Critical vulnerabilities."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.container.integrity import IntegrityError, oci_identity, sha256_file


class EvidenceError(ValueError):
    """Raised when image evidence cannot be bound to one immutable identity."""


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--expected-image-id", required=True)
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--additional-artifact", action="append", default=[])
    parser.add_argument(
        "--additional-vulnerability-report", action="append", default=[]
    )
    return parser.parse_args()


def _image_identity(artifacts: Path, expected_image_id: str) -> tuple[str, str, dict]:
    try:
        manifest_digest, config_digest = oci_identity(artifacts / "image.oci.tar")
    except IntegrityError as error:
        raise EvidenceError(str(error)) from error
    if config_digest != expected_image_id:
        raise EvidenceError("exported OCI config does not match the resolved image ID")
    podman_image_id = expected_image_id.removeprefix("sha256:")
    try:
        inspection: Any = json.loads(
            subprocess.run(  # noqa: S603 - fixed executable and immutable image ID
                ["/usr/bin/podman", "image", "inspect", podman_image_id],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
        inspected = inspection[0]
    except (
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        IndexError,
        KeyError,
        TypeError,
    ) as error:
        raise EvidenceError(f"immutable image inspection failed: {error}") from error
    if not isinstance(inspected, dict) or inspected.get("Id") != podman_image_id:
        raise EvidenceError("immutable image inspection returned another identity")
    return manifest_digest, config_digest, inspected


def _artifact_digests(
    artifacts: Path, additional_artifacts: tuple[str, ...]
) -> dict[str, str]:
    try:
        return {
            name: sha256_file(artifacts / name)
            for name in (
                "image.oci.tar",
                "sbom.cdx.json",
                "sbom.spdx.json",
                "vulnerabilities.json",
                *additional_artifacts,
            )
        }
    except IntegrityError as error:
        raise EvidenceError(str(error)) from error


def main() -> int:
    """Write bounded metadata after the complete Grype report is durable."""

    arguments = _arguments()
    try:
        manifest_digest, config_digest, inspected = _image_identity(
            arguments.artifacts, arguments.expected_image_id
        )
        additional_artifacts = tuple(arguments.additional_artifact)
        additional_reports = tuple(arguments.additional_vulnerability_report)
        if (
            len(additional_artifacts) != len(set(additional_artifacts))
            or len(additional_reports) != len(set(additional_reports))
            or not set(additional_reports).issubset(additional_artifacts)
        ):
            raise EvidenceError(
                "additional evidence names must be unique and every report must be bound"
            )
        artifact_digests = _artifact_digests(arguments.artifacts, additional_artifacts)
    except EvidenceError as error:
        print(f"error: {error}")
        return 1
    severities: Counter[str] = Counter()
    critical_fixed: list[dict[str, object]] = []
    critical_unfixed: list[dict[str, object]] = []
    report_names = (
        "vulnerabilities.json",
        *additional_reports,
    )
    for report_name in report_names:
        report_path = arguments.artifacts / report_name
        report: dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
        for match in report.get("matches", []):
            vulnerability = match.get("vulnerability", {})
            severity = vulnerability.get("severity", "Unknown")
            severities[severity] += 1
            if severity != "Critical":
                continue
            versions = vulnerability.get("fix", {}).get("versions", [])
            item = {
                "id": vulnerability.get("id", "unknown"),
                "package": match.get("artifact", {}).get("name", "unknown"),
                "fix_versions": versions,
            }
            if len(report_names) > 1:
                item["report"] = report_name
            (critical_fixed if versions else critical_unfixed).append(item)
    evidence = {
        "image": {
            "id": inspected.get("Id"),
            "oci_manifest_digest": manifest_digest,
            "oci_config_digest": config_digest,
            "repo_digests": inspected.get("RepoDigests", []),
            "size": inspected.get("Size"),
        },
        "artifacts": artifact_digests,
        "vulnerabilities": {
            "counts_by_severity": dict(sorted(severities.items())),
            "critical_with_fix": critical_fixed,
            "critical_without_fix": critical_unfixed,
            "unfixed_required_action": (
                "Record mitigation or roll back before release when non-empty."
            ),
        },
    }
    (arguments.artifacts / "image-metadata.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if critical_fixed:
        print("error: fixable Critical vulnerabilities found")
        return 1
    if arguments.release and critical_unfixed:
        print("error: release has unmitigated Critical vulnerabilities")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
