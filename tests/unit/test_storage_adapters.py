"""Deterministic object-store and readiness adapter failures."""

from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from pytest_mock import MockerFixture

from md_converter.app import ProfileReadinessProbe, build_components
from md_converter.config import Settings
from md_converter.persistence.migrations import (
    POSTGRES_MIGRATION_LOCK,
    upgrade_database,
)
from md_converter.persistence.sql import DatabaseReadinessProbe
from md_converter.storage import (
    FilesystemObjectStore,
    ObjectKey,
    ObjectNotFoundError,
    ObjectScope,
    ObjectStoreError,
    S3ObjectStore,
)
from tests.settings import template_settings


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code}}, "operation")


@pytest.mark.unit
def test_filesystem_reads_deletes_and_readiness_delegate_without_raw_names(
    mocker: MockerFixture,
) -> None:
    ensure_directory = mocker.patch.object(FilesystemObjectStore, "_ensure_directory")
    store = FilesystemObjectStore(Path("/data"))
    root = mocker.MagicMock(spec=Path)
    target = mocker.MagicMock(spec=Path)
    store._root = root
    mocker.patch.object(store, "_path", return_value=target)
    sync_directory = mocker.patch.object(store, "_sync_directory")
    key = ObjectKey(ObjectScope.UPLOAD, uuid4(), uuid4())
    target.read_bytes.return_value = b"content"
    target.is_file.return_value = True
    root.is_dir.return_value = True
    access = mocker.patch("md_converter.storage.os.access", return_value=True)

    assert store.get(key) == b"content"
    assert store.exists(key)
    store.delete(key)
    assert store.is_ready()
    target.unlink.assert_called_once_with()
    sync_directory.assert_called_once_with(target.parent)
    ensure_directory.assert_called_once_with(Path("/data/objects"))
    access.assert_called_once()


@pytest.mark.unit
def test_filesystem_nested_directory_creation_syncs_each_parent_in_order(
    mocker: MockerFixture,
) -> None:
    directory = Path("/data/objects/uploads/owner")
    mocker.patch.object(
        Path, "exists", autospec=True, side_effect=lambda path: path == Path("/")
    )
    mkdir = mocker.patch.object(Path, "mkdir", autospec=True)
    sync_directory = mocker.patch.object(FilesystemObjectStore, "_sync_directory")

    FilesystemObjectStore._ensure_directory(directory)

    mkdir.assert_called_once_with(directory, mode=0o700, parents=True, exist_ok=True)
    assert [call.args[0] for call in sync_directory.call_args_list] == [
        Path("/"),
        Path("/data"),
        Path("/data/objects"),
        Path("/data/objects/uploads"),
    ]


@pytest.mark.unit
def test_filesystem_delete_syncs_after_unlink_and_reports_sync_failure(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(FilesystemObjectStore, "_ensure_directory")
    store = FilesystemObjectStore(Path("/data"))
    target = mocker.MagicMock(spec=Path)
    target.parent = Path("/data/objects/uploads/owner")
    mocker.patch.object(store, "_path", return_value=target)
    manager = mocker.Mock()
    manager.attach_mock(target.unlink, "unlink")
    sync_directory = mocker.patch.object(
        store, "_sync_directory", side_effect=PermissionError
    )
    manager.attach_mock(sync_directory, "sync")

    with pytest.raises(ObjectStoreError, match="Object storage operation failed"):
        store.delete(ObjectKey(ObjectScope.UPLOAD, uuid4(), uuid4()))

    assert [call[0] for call in manager.mock_calls] == ["unlink", "sync"]


@pytest.mark.unit
def test_s3_success_uses_only_the_stable_identifier_key(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    client.get_object.return_value = {"Body": BytesIO(b"content")}
    store = S3ObjectStore(client, "bucket")
    key = ObjectKey(ObjectScope.TEMPLATE_VERSION, uuid4(), uuid4())

    store.put(key, b"content")
    assert store.get(key) == b"content"
    assert store.exists(key)
    store.delete(key)
    assert store.is_ready()
    expected = key.as_posix()
    client.put_object.assert_called_once_with(
        Bucket="bucket", Key=expected, Body=b"content"
    )
    client.get_object.assert_called_once_with(Bucket="bucket", Key=expected)
    client.head_object.assert_called_once_with(Bucket="bucket", Key=expected)
    client.delete_object.assert_called_once_with(Bucket="bucket", Key=expected)


@pytest.mark.unit
@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
def test_s3_missing_object_mapping(mocker: MockerFixture, code: str) -> None:
    client = mocker.Mock()
    client.get_object.side_effect = client_error(code)
    client.head_object.side_effect = client_error(code)
    store = S3ObjectStore(client, "bucket")
    key = ObjectKey(ObjectScope.RESULT, uuid4(), uuid4())
    with pytest.raises(ObjectNotFoundError, match="Object does not exist"):
        store.get(key)
    assert not store.exists(key)


@pytest.mark.unit
@pytest.mark.parametrize("operation", ["put", "get", "delete", "exists"])
def test_s3_adapter_sanitizes_provider_failures(
    mocker: MockerFixture, operation: str
) -> None:
    client = mocker.Mock()
    method_name = {
        "put": "put_object",
        "get": "get_object",
        "delete": "delete_object",
        "exists": "head_object",
    }[operation]
    getattr(client, method_name).side_effect = client_error("AccessDenied")
    store = S3ObjectStore(client, "bucket")
    key = ObjectKey(ObjectScope.UPLOAD, uuid4(), uuid4())
    with pytest.raises(ObjectStoreError, match="Object storage operation failed"):
        getattr(store, operation)(key, b"content") if operation == "put" else getattr(
            store, operation
        )(key)


@pytest.mark.unit
def test_s3_transport_failure_and_database_failure_make_readiness_false(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    client.head_bucket.side_effect = EndpointConnectionError(endpoint_url="test")
    assert not S3ObjectStore(client, "bucket").is_ready()

    engine = mocker.MagicMock()
    engine.connect.side_effect = RuntimeError("unavailable")
    assert not DatabaseReadinessProbe(engine).is_ready()


@pytest.mark.unit
def test_profile_readiness_short_circuits_failed_metadata(
    mocker: MockerFixture,
) -> None:
    database = mocker.Mock()
    objects = mocker.Mock()
    database.is_ready.return_value = False
    assert not ProfileReadinessProbe(database, objects).is_ready()
    objects.is_ready.assert_not_called()
    database.is_ready.return_value = True
    objects.is_ready.return_value = True
    assert ProfileReadinessProbe(database, objects).is_ready()


@pytest.mark.unit
def test_distributed_wiring_allows_aws_credential_provider_defaults(
    mocker: MockerFixture,
) -> None:
    reclaim = mocker.patch("md_converter.app.TemplateService.reclaim_pending")
    database_url = "postgresql+psycopg://database/app"
    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password="admin-" + "password",
        storage_profile="distributed",
        distributed_database_url=database_url,
        s3_bucket="objects",
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3_600,
    )
    engine = mocker.Mock()
    engine.dialect.name = "postgresql"
    create_engine = mocker.patch(
        "md_converter.app.create_database_engine", return_value=engine
    )
    upgrade = mocker.patch("md_converter.app.upgrade_database")
    s3_client = mocker.patch("md_converter.app.boto3.client")
    components = build_components(settings)
    create_engine.assert_called_once_with(database_url)
    upgrade.assert_called_once_with(engine)
    s3_client.assert_called_once_with("s3")
    reclaim.assert_called_once_with()
    assert components.object_store is not None
    assert components.job_repository is not None
    assert components.retention is not None


@pytest.mark.unit
def test_profile_wiring_covers_standalone_and_explicit_s3_options(
    mocker: MockerFixture,
) -> None:
    reclaim = mocker.patch("md_converter.app.TemplateService.reclaim_pending")
    engine = mocker.Mock()
    engine.dialect.name = "sqlite"
    mocker.patch("md_converter.app.create_database_engine", return_value=engine)
    mocker.patch("md_converter.app.upgrade_database")
    files = mocker.patch("md_converter.app.FilesystemObjectStore")
    standalone = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password="admin-" + "password",
        storage_profile="standalone",
        standalone_data_directory="/data",
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3_600,
    )
    assert build_components(standalone).object_store is files.return_value
    files.assert_called_once_with(Path("/data"))

    s3_client = mocker.patch("md_converter.app.boto3.client")
    distributed = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password="admin-" + "password",
        storage_profile="distributed",
        distributed_database_url="postgresql+psycopg://database/app",
        s3_bucket="objects",
        s3_endpoint_url="http://s3.test",
        s3_region="test-region",
        s3_access_key_id="access",
        s3_secret_access_key="secret-" + "key",
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3_600,
    )
    build_components(distributed)
    assert reclaim.call_count == 2
    s3_client.assert_called_once_with(
        "s3",
        endpoint_url="http://s3.test",
        region_name="test-region",
        aws_access_key_id="access",
        aws_secret_access_key="secret-" + "key",
    )


@pytest.mark.unit
def test_postgresql_migrations_take_a_transaction_advisory_lock(
    mocker: MockerFixture,
) -> None:
    engine = mocker.MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.dialect.name = "postgresql"
    command = mocker.patch("md_converter.persistence.migrations.command.upgrade")
    upgrade_database(engine)
    assert connection.execute.call_args.args[1] == {"lock_id": POSTGRES_MIGRATION_LOCK}
    command.assert_called_once()
