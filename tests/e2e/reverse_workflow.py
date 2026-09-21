"""Authenticated reverse API proof against the deployed final-image workflow."""

from __future__ import annotations

import argparse
import hashlib
import time
from pathlib import Path

from scripts.container.api_workflow_smoke import multipart
from tests.e2e.service_workflow import (
    ServiceClient,
    create_user,
    decode_object,
    expect,
)


def exercise_primary(base_url: str, profile: str) -> None:
    """Submit a redistributable CSV through scanning, queue, broker and attempt."""
    admin = ServiceClient(base_url)
    admin.login("e2e-admin", "e2e-admin-password")
    username = f"t73-api-owner-{profile}"
    password = "T73-api-owner-fixture-password"  # noqa: S105 - isolated E2E identity
    create_user(admin, username, password)
    owner = ServiceClient(base_url)
    owner.login(username, password)
    capabilities_path = "/api/v1/reversions/capabilities"
    expect(
        ServiceClient(base_url).request("GET", capabilities_path),
        401,
        "private capabilities",
    )
    capability_response = owner.request("GET", capabilities_path)
    expect(capability_response, 200, "reverse capabilities")
    assert "private" in capability_response.headers["cache-control"]
    assert "no-store" in capability_response.headers["cache-control"]
    capabilities = decode_object(capability_response, "reverse capabilities")
    assert capabilities["schema_version"] == 1
    assert capabilities["maximum_upload_bytes"] == 1_000_000
    assert owner.request("GET", capabilities_path).body == capability_response.body
    capability_digest = hashlib.sha256(capability_response.body).hexdigest()
    print(f"T73 {profile} capabilities SHA-256: {capability_digest}")
    source = Path("spikes/anydoc/corpus/csv/sheet.csv").read_bytes()
    body, content_type = multipart([], [("source", "sheet.csv", source)])
    submitted = owner.request(
        "POST",
        "/api/v1/reversions",
        body=body,
        content_type=content_type,
        mutate=True,
        headers={"Idempotency-Key": f"t73-api-primary-{profile}"},
    )
    expect(submitted, 202, "reverse submission")
    job = decode_object(submitted, "reverse submission")
    job_id = job["id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        response = owner.request("GET", f"/api/v1/reversions/{job_id}")
        expect(response, 200, "reverse status")
        job = decode_object(response, "reverse status")
        if job["state"] in {"succeeded", "failed", "cancelled", "expired"}:
            break
        time.sleep(0.1)
    if job["state"] != "succeeded":
        raise AssertionError(f"Reverse primary path did not succeed: {job['state']}")
    result = owner.request("GET", f"/api/v1/reversions/{job_id}/result")
    expect(result, 200, "reverse result")
    assert result.headers["content-type"].startswith("text/markdown")
    assert "no-store" in result.headers["cache-control"]
    assert result.headers["x-content-type-options"] == "nosniff"
    markdown = result.body.decode("utf-8")
    assert "|" in markdown
    for retained in ("Kind", "Value", "Note", "Percent", "Currency", "Boolean"):
        assert retained in markdown
    denied = admin.request("GET", f"/api/v1/reversions/{job_id}/result")
    expect(denied, 404, "administrator reverse content denial")
    print(f"T73 reverse API primary path passed for {profile}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--profile", choices=("standalone", "distributed"), required=True
    )
    args = parser.parse_args()
    exercise_primary(args.base_url, args.profile)


if __name__ == "__main__":
    main()
