"""Tests for provider-neutral S3 integration setup."""

import os

import pytest
from botocore.exceptions import ClientError
from pytest_mock import MockerFixture

from scripts.ci.prepare_s3_test_bucket import main


def configure_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MD_CONVERTER_TEST_S3_BUCKET", "bucket")
    monkeypatch.setenv("MD_CONVERTER_TEST_S3_ENDPOINT_URL", "http://s3.test")
    monkeypatch.setenv("MD_CONVERTER_TEST_S3_REGION", "test-region")
    monkeypatch.setenv("MD_CONVERTER_TEST_S3_ACCESS_KEY_ID", "access")
    monkeypatch.setenv("MD_CONVERTER_TEST_S3_SECRET_ACCESS_KEY", "secret")


@pytest.mark.unit
def test_prepare_s3_bucket_creates_only_a_missing_bucket(
    monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    configure_environment(monkeypatch)
    client = mocker.Mock()
    client.head_bucket.side_effect = ClientError(
        {"Error": {"Code": "NoSuchBucket"}}, "head_bucket"
    )
    factory = mocker.patch(
        "scripts.ci.prepare_s3_test_bucket.boto3.client", return_value=client
    )
    assert main() == 0
    factory.assert_called_once_with(
        "s3",
        endpoint_url="http://s3.test",
        region_name="test-region",
        aws_access_key_id="access",
        aws_secret_access_key=os.environ["MD_CONVERTER_TEST_S3_SECRET_ACCESS_KEY"],
    )
    client.create_bucket.assert_called_once_with(Bucket="bucket")


@pytest.mark.unit
def test_prepare_s3_bucket_preserves_existing_and_unexpected_failures(
    monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    configure_environment(monkeypatch)
    client = mocker.Mock()
    mocker.patch("scripts.ci.prepare_s3_test_bucket.boto3.client", return_value=client)
    assert main() == 0
    client.create_bucket.assert_not_called()

    client.head_bucket.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied"}}, "head_bucket"
    )
    with pytest.raises(ClientError):
        main()
