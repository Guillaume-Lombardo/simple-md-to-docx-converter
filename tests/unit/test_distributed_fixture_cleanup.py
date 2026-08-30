"""Failure-path coverage for distributed integration resource isolation."""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from pytest_mock import MockerFixture

from tests.integration.postgres import conftest as distributed_conftest

pytestmark = pytest.mark.unit


def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MD_CONVERTER_TEST_POSTGRES_URL", "postgresql://test/db")
    monkeypatch.setenv("MD_CONVERTER_TEST_S3_ENDPOINT_URL", "http://s3.invalid")
    monkeypatch.setenv("MD_CONVERTER_TEST_S3_REGION", "test-region")
    monkeypatch.setenv("MD_CONVERTER_TEST_S3_ACCESS_KEY_ID", "test-access")
    monkeypatch.setenv("MD_CONVERTER_TEST_S3_SECRET_ACCESS_KEY", "test-secret")


def _requires_s3_request(mocker: MockerFixture) -> object:
    node = mocker.Mock()
    node.get_closest_marker.return_value = object()
    return SimpleNamespace(node=node)


def _fixture_generator(
    request: object,
    monkeypatch: pytest.MonkeyPatch,
):
    fixture = cast(Any, distributed_conftest.isolated_postgresql_and_s3_resources)
    return fixture.__wrapped__(request, monkeypatch)


def test_distributed_fixture_drops_schema_when_s3_client_creation_fails(
    mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _environment(monkeypatch)
    engine = mocker.patch.object(
        distributed_conftest, "create_database_engine"
    ).return_value
    execute = engine.begin.return_value.__enter__.return_value.execute
    mocker.patch.object(
        distributed_conftest.boto3,
        "client",
        side_effect=RuntimeError("client creation failed"),
    )

    generator = _fixture_generator(_requires_s3_request(mocker), monkeypatch)
    with pytest.raises(RuntimeError, match="client creation"):
        next(generator)

    assert execute.call_count == 2
    engine.dispose.assert_called_once_with()


def test_distributed_fixture_drops_schema_when_bucket_creation_fails(
    mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _environment(monkeypatch)
    engine = mocker.patch.object(
        distributed_conftest, "create_database_engine"
    ).return_value
    execute = engine.begin.return_value.__enter__.return_value.execute
    client = mocker.patch.object(distributed_conftest.boto3, "client").return_value
    client.create_bucket.side_effect = RuntimeError("bucket creation failed")
    client.list_objects_v2.return_value = {"IsTruncated": False}

    generator = _fixture_generator(_requires_s3_request(mocker), monkeypatch)
    with pytest.raises(RuntimeError, match="bucket creation"):
        next(generator)

    client.delete_bucket.assert_called_once()
    client.close.assert_called_once_with()
    assert execute.call_count == 2
    engine.dispose.assert_called_once_with()


def test_distributed_fixture_drops_schema_when_s3_cleanup_fails(
    mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _environment(monkeypatch)
    engine = mocker.patch.object(
        distributed_conftest, "create_database_engine"
    ).return_value
    execute = engine.begin.return_value.__enter__.return_value.execute
    client = mocker.patch.object(distributed_conftest.boto3, "client").return_value
    client.list_objects_v2.side_effect = RuntimeError("cleanup failed")

    generator = _fixture_generator(_requires_s3_request(mocker), monkeypatch)
    next(generator)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        generator.close()

    assert execute.call_count == 2
    engine.dispose.assert_called_once_with()
    client.close.assert_called_once_with()
