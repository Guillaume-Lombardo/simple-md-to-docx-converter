"""Per-test PostgreSQL schema and RustFS bucket isolation."""

import os
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import boto3
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from md_converter.persistence.sql import create_database_engine


@pytest.fixture(autouse=True)
def isolated_postgresql_and_s3_resources(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Give every distributed test unique resources and deterministic teardown."""

    base_url = make_url(os.environ["MD_CONVERTER_TEST_POSTGRES_URL"])
    schema = f"test_{uuid4().hex}"
    admin_engine = create_database_engine(base_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated_url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv(
        "MD_CONVERTER_TEST_POSTGRES_URL",
        isolated_url.render_as_string(hide_password=False),
    )

    s3_client: Any | None = None
    bucket: str | None = None
    if request.node.get_closest_marker("requires_s3") is not None:
        s3_client = boto3.client(
            "s3",
            endpoint_url=os.environ["MD_CONVERTER_TEST_S3_ENDPOINT_URL"],
            region_name=os.environ["MD_CONVERTER_TEST_S3_REGION"],
            aws_access_key_id=os.environ["MD_CONVERTER_TEST_S3_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["MD_CONVERTER_TEST_S3_SECRET_ACCESS_KEY"],
        )
        bucket = f"test-{uuid4().hex}"
        s3_client.create_bucket(Bucket=bucket)
        monkeypatch.setenv("MD_CONVERTER_TEST_S3_BUCKET", bucket)

    try:
        yield
    finally:
        if s3_client is not None and bucket is not None:
            continuation: str | None = None
            while True:
                arguments: dict[str, object] = {"Bucket": bucket}
                if continuation is not None:
                    arguments["ContinuationToken"] = continuation
                listed = s3_client.list_objects_v2(**arguments)
                objects = [{"Key": item["Key"]} for item in listed.get("Contents", [])]
                if objects:
                    s3_client.delete_objects(Bucket=bucket, Delete={"Objects": objects})
                if not listed.get("IsTruncated"):
                    break
                continuation = listed["NextContinuationToken"]
            s3_client.delete_bucket(Bucket=bucket)
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()
