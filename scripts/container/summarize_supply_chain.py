"""Record final-image evidence and gate fixable Critical vulnerabilities."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--artifacts", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    """Write bounded metadata after the complete Grype report is durable."""

    arguments = _arguments()
    inspected = json.loads(
        subprocess.run(  # noqa: S603 - fixed executable and bounded inspected image
            ["/usr/bin/podman", "image", "inspect", arguments.image],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )[0]
    report_path = arguments.artifacts / "vulnerabilities.json"
    report: dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
    severities: Counter[str] = Counter()
    critical_fixed: list[dict[str, object]] = []
    critical_unfixed: list[dict[str, object]] = []
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
        (critical_fixed if versions else critical_unfixed).append(item)
    evidence = {
        "image": {
            "id": inspected.get("Id"),
            "digest": inspected.get("Digest"),
            "repo_digests": inspected.get("RepoDigests", []),
            "size": inspected.get("Size"),
        },
        "artifacts": {
            name: _sha256(arguments.artifacts / name)
            for name in (
                "image.oci.tar",
                "sbom.cdx.json",
                "sbom.spdx.json",
                "vulnerabilities.json",
            )
        },
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
