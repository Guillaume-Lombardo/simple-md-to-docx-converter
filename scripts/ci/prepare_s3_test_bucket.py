"""Create the configured S3-compatible integration-test bucket."""

from __future__ import annotations

import os

import boto3
from botocore.exceptions import ClientError


def main() -> int:
    """Create the bucket exactly once without provider-specific operations."""
    bucket = os.environ["MARKWEAVE_TEST_S3_BUCKET"]
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["MARKWEAVE_TEST_S3_ENDPOINT_URL"],
        region_name=os.environ["MARKWEAVE_TEST_S3_REGION"],
        aws_access_key_id=os.environ["MARKWEAVE_TEST_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["MARKWEAVE_TEST_S3_SECRET_ACCESS_KEY"],
    )
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") not in {
            "404",
            "NoSuchBucket",
            "NotFound",
        }:
            raise
        client.create_bucket(Bucket=bucket)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by GitHub Actions
    raise SystemExit(main())
