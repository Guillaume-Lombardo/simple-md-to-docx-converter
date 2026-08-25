"""Create and restore a bounded test-only RustFS bucket snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import boto3

if TYPE_CHECKING:
    from collections.abc import Sequence


def _client(arguments: argparse.Namespace) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=arguments.endpoint_url,
        region_name=arguments.region,
        aws_access_key_id=arguments.access_key_id,
        aws_secret_access_key=arguments.secret_access_key,
    )


def _objects(client: Any, bucket: str) -> list[dict[str, object]]:
    paginator = client.get_paginator("list_objects_v2")
    return [
        item
        for page in paginator.paginate(Bucket=bucket)
        for item in page.get("Contents", [])
    ]


def backup(arguments: argparse.Namespace) -> None:
    """Download every object and write a content-addressed manifest."""
    client = _client(arguments)
    arguments.directory.mkdir(parents=True, mode=0o700)
    manifest: list[dict[str, object]] = []
    for item in _objects(client, arguments.bucket):
        key = str(item["Key"])
        payload = client.get_object(Bucket=arguments.bucket, Key=key)["Body"].read()
        digest = hashlib.sha256(payload).hexdigest()
        (arguments.directory / digest).write_bytes(payload)
        manifest.append({"key": key, "sha256": digest, "size": len(payload)})
    manifest.sort(key=lambda item: str(item["key"]))
    (arguments.directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def restore(arguments: argparse.Namespace) -> None:
    """Replace the bucket contents with the verified snapshot."""
    client = _client(arguments)
    manifest = json.loads(
        (arguments.directory / "manifest.json").read_text(encoding="utf-8")
    )
    for item in _objects(client, arguments.bucket):
        client.delete_object(Bucket=arguments.bucket, Key=str(item["Key"]))
    for item in manifest:
        payload = (arguments.directory / item["sha256"]).read_bytes()
        if len(payload) != item["size"]:
            raise RuntimeError("S3 backup object size mismatch")
        if hashlib.sha256(payload).hexdigest() != item["sha256"]:
            raise RuntimeError("S3 backup object digest mismatch")
        client.put_object(Bucket=arguments.bucket, Key=item["key"], Body=payload)


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the explicit test snapshot contract."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("backup", "restore"))
    parser.add_argument("--endpoint-url", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--access-key-id", required=True)
    parser.add_argument("--secret-access-key", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--directory", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the requested snapshot operation."""
    arguments = parse_arguments(argv)
    if arguments.operation == "backup":
        backup(arguments)
    else:
        restore(arguments)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
