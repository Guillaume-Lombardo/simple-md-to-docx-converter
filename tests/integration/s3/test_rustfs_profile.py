"""Real RustFS coverage through the provider-neutral S3 contract."""

import os
from collections.abc import Callable
from uuid import uuid4

import boto3
import pytest

from markweave.storage import (
    ObjectKey,
    ObjectScope,
    ObjectStoreError,
    S3ObjectStore,
)
from tests.storage_contracts import exercise_object_store_contract


def rustfs_client(
    *, access_key: str | None = None, secret_key: str | None = None
) -> object:
    return boto3.client(
        "s3",
        endpoint_url=os.environ["MARKWEAVE_TEST_S3_ENDPOINT_URL"],
        region_name=os.environ.get("MARKWEAVE_TEST_S3_REGION", "us-east-1"),
        aws_access_key_id=(access_key or os.environ["MARKWEAVE_TEST_S3_ACCESS_KEY_ID"]),
        aws_secret_access_key=(
            secret_key or os.environ["MARKWEAVE_TEST_S3_SECRET_ACCESS_KEY"]
        ),
    )


@pytest.mark.integration
@pytest.mark.requires_s3
def test_rustfs_object_store_contract() -> None:
    bucket = os.environ["MARKWEAVE_TEST_S3_BUCKET"]
    store = S3ObjectStore(rustfs_client(), bucket)
    assert store.is_ready()
    exercise_object_store_contract(store)


@pytest.mark.integration
@pytest.mark.requires_s3
def test_rustfs_missing_bucket_is_a_sanitized_readiness_failure() -> None:
    store = S3ObjectStore(rustfs_client(), "t12-bucket-that-does-not-exist")
    assert not store.is_ready()
    key = ObjectKey(ObjectScope.UPLOAD, uuid4(), uuid4())
    marker = b"must-not-leak"
    for operation in (
        lambda: store.put(key, marker),
        lambda: store.get(key),
        lambda: store.delete(key),
    ):
        assert_operation_fails(operation, marker)
    # RustFS reports the same generic HEAD 404 for a missing key and bucket.
    assert not store.exists(key)


@pytest.mark.integration
@pytest.mark.requires_s3
def test_rustfs_wrong_credentials_deny_every_object_operation() -> None:
    store = S3ObjectStore(
        rustfs_client(
            access_key=os.environ["MARKWEAVE_TEST_S3_ACCESS_KEY_ID"] + "-wrong",
            secret_key=os.environ["MARKWEAVE_TEST_S3_SECRET_ACCESS_KEY"] + "-wrong",
        ),
        os.environ["MARKWEAVE_TEST_S3_BUCKET"],
    )
    assert not store.is_ready()
    assert_store_operations_fail(store)


def assert_store_operations_fail(store: S3ObjectStore) -> None:
    key = ObjectKey(ObjectScope.UPLOAD, uuid4(), uuid4())
    marker = b"must-not-leak"
    operations = (
        lambda: store.put(key, marker),
        lambda: store.get(key),
        lambda: store.exists(key),
        lambda: store.delete(key),
    )
    for operation in operations:
        assert_operation_fails(operation, marker)


def assert_operation_fails(operation: Callable[[], object], marker: bytes) -> None:
    with pytest.raises(ObjectStoreError) as caught:
        operation()
    assert marker.decode() not in repr(caught.value)
