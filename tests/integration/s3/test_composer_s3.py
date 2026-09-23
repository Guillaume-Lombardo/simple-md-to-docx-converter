"""Composer object namespaces retain immutable keys on the real S3 adapter."""

import os
from uuid import uuid4

import boto3
import pytest

from markweave.storage import ObjectKey, ObjectScope, S3ObjectStore

pytestmark = [pytest.mark.integration, pytest.mark.requires_s3]


def test_composer_source_and_artifact_keys_are_distinct_and_restart_readable() -> None:
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["MARKWEAVE_TEST_S3_ENDPOINT_URL"],
        region_name=os.environ["MARKWEAVE_TEST_S3_REGION"],
        aws_access_key_id=os.environ["MARKWEAVE_TEST_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["MARKWEAVE_TEST_S3_SECRET_ACCESS_KEY"],
    )
    store = S3ObjectStore(client, os.environ["MARKWEAVE_TEST_S3_BUCKET"])
    owner, object_id = uuid4(), uuid4()
    source = ObjectKey(ObjectScope.COMPOSER_SOURCE, owner, object_id)
    artifact = ObjectKey(ObjectScope.COMPOSER_ARTIFACT, owner, object_id)
    try:
        store.put(source, b"scanned source")
        store.put(artifact, b"rendered artifact")
        assert store.get(source) == b"scanned source"
        assert store.get(artifact) == b"rendered artifact"
        assert source.as_posix() != artifact.as_posix()
        store.delete(source)
        assert store.exists(artifact)
    finally:
        store.close()
