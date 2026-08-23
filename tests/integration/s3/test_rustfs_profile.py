"""Real RustFS coverage through the provider-neutral S3 contract."""

import os

import boto3
import pytest

from md_converter.storage import S3ObjectStore
from tests.storage_contracts import exercise_object_store_contract


def rustfs_client() -> object:
    return boto3.client(
        "s3",
        endpoint_url=os.environ["MD_CONVERTER_TEST_S3_ENDPOINT_URL"],
        region_name=os.environ.get("MD_CONVERTER_TEST_S3_REGION", "us-east-1"),
        aws_access_key_id=os.environ["MD_CONVERTER_TEST_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["MD_CONVERTER_TEST_S3_SECRET_ACCESS_KEY"],
    )


@pytest.mark.integration
@pytest.mark.requires_s3
def test_rustfs_object_store_contract() -> None:
    bucket = os.environ["MD_CONVERTER_TEST_S3_BUCKET"]
    store = S3ObjectStore(rustfs_client(), bucket)
    assert store.is_ready()
    exercise_object_store_contract(store)


@pytest.mark.integration
@pytest.mark.requires_s3
def test_rustfs_missing_bucket_is_a_sanitized_readiness_failure() -> None:
    store = S3ObjectStore(rustfs_client(), "t12-bucket-that-does-not-exist")
    assert not store.is_ready()
