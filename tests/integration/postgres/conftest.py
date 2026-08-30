"""Per-test PostgreSQL schema and RustFS bucket isolation."""

import os
from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any
from uuid import uuid4

import boto3
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from markweave.persistence.sql import create_database_engine


def _drop_schema(admin_engine: Any, schema: str) -> None:
    with admin_engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def _delete_bucket(s3_client: Any, bucket: str) -> None:
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


@pytest.fixture(autouse=True)
def isolated_postgresql_and_s3_resources(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Give every distributed test unique resources and deterministic teardown."""

    base_url = make_url(os.environ["MD_CONVERTER_TEST_POSTGRES_URL"])
    schema = f"test_{uuid4().hex}"
    with ExitStack() as cleanup:
        admin_engine = create_database_engine(base_url)
        cleanup.callback(admin_engine.dispose)
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        cleanup.callback(_drop_schema, admin_engine, schema)
        isolated_url = base_url.update_query_dict(
            {"options": f"-csearch_path={schema}"}
        )
        monkeypatch.setenv(
            "MD_CONVERTER_TEST_POSTGRES_URL",
            isolated_url.render_as_string(hide_password=False),
        )

        if request.node.get_closest_marker("requires_s3") is not None:
            s3_client = boto3.client(
                "s3",
                endpoint_url=os.environ["MD_CONVERTER_TEST_S3_ENDPOINT_URL"],
                region_name=os.environ["MD_CONVERTER_TEST_S3_REGION"],
                aws_access_key_id=os.environ["MD_CONVERTER_TEST_S3_ACCESS_KEY_ID"],
                aws_secret_access_key=os.environ[
                    "MD_CONVERTER_TEST_S3_SECRET_ACCESS_KEY"
                ],
            )
            cleanup.callback(s3_client.close)
            bucket = f"test-{uuid4().hex}"
            cleanup.callback(_delete_bucket, s3_client, bucket)
            s3_client.create_bucket(Bucket=bucket)
            monkeypatch.setenv("MD_CONVERTER_TEST_S3_BUCKET", bucket)

        yield
