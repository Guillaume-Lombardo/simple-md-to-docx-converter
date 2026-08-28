"""Deterministic object-store and readiness adapter failures."""

import logging
from io import BytesIO
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from pytest_mock import MockerFixture

from markweave.app import ProfileReadinessProbe, build_components, build_upload_scanner
from markweave.config import MalwareScanningMode, Settings
from markweave.malware import ClamAVUploadScanner, TrustedUpstreamUploadScanner
from markweave.persistence.migrations import (
    POSTGRES_MIGRATION_LOCK,
    upgrade_database,
)
from markweave.persistence.sql import DatabaseReadinessProbe
from markweave.storage import (
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
    access = mocker.patch("markweave.storage.os.access", return_value=True)

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


def _standalone_component_settings(**overrides: object) -> Settings:
    values: dict[str, object] = dict(
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
    values.update(overrides)
    return Settings.model_validate(values)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failure_stage", "created_count"),
    [
        ("main_engine", 0),
        ("migration", 1),
        ("readiness_engine", 1),
        ("observation_engine", 2),
        ("validator", 3),
        ("reclaim", 3),
        ("components", 3),
    ],
)
def test_component_assembly_disposes_each_created_engine_on_failure(
    mocker: MockerFixture, failure_stage: str, created_count: int
) -> None:
    mocker.patch("markweave.app.FilesystemObjectStore", return_value=mocker.Mock())
    disposed: list[str] = []
    engines = tuple(
        mocker.MagicMock(name=name) for name in ("main", "readiness", "observation")
    )
    for engine in engines:
        engine.dialect.name = "sqlite"
        name = engine._mock_name
        engine.dispose.side_effect = lambda name=name: disposed.append(name)

    created = list(engines[:created_count])
    if failure_stage in {"main_engine", "readiness_engine", "observation_engine"}:
        created.append(RuntimeError(f"{failure_stage} failed"))
    create_engine = mocker.patch(
        "markweave.app.create_database_engine", side_effect=created
    )
    upgrade = mocker.patch("markweave.app.upgrade_database")
    validator = mocker.patch("markweave.app.build_template_validator")
    reclaim = mocker.patch("markweave.app.TemplateService.reclaim_pending")
    components = mocker.patch("markweave.app.AppComponents", wraps=None)
    if failure_stage == "migration":
        upgrade.side_effect = RuntimeError("migration failed")
    elif failure_stage == "validator":
        validator.side_effect = RuntimeError("validator failed")
    elif failure_stage == "reclaim":
        reclaim.side_effect = RuntimeError("reclaim failed")
    elif failure_stage == "components":
        components.side_effect = RuntimeError("components failed")

    with pytest.raises(RuntimeError, match=rf"{failure_stage} failed"):
        build_components(_standalone_component_settings())

    assert create_engine.call_count == created_count + (
        failure_stage in {"main_engine", "readiness_engine", "observation_engine"}
    )
    assert disposed == [
        engine._mock_name for engine in reversed(engines[:created_count])
    ]


@pytest.mark.unit
def test_component_assembly_attempts_every_engine_disposal_when_one_fails(
    mocker: MockerFixture,
) -> None:
    mocker.patch("markweave.app.FilesystemObjectStore", return_value=mocker.Mock())
    disposed: list[str] = []
    engines = tuple(
        mocker.MagicMock(name=name) for name in ("main", "readiness", "observation")
    )
    for engine in engines:
        engine.dialect.name = "sqlite"
        name = engine._mock_name
        engine.dispose.side_effect = lambda name=name: disposed.append(name)

    def fail_readiness_disposal() -> None:
        disposed.append("readiness")
        raise RuntimeError("dispose failed")

    engines[1].dispose.side_effect = fail_readiness_disposal
    mocker.patch("markweave.app.create_database_engine", side_effect=engines)
    mocker.patch("markweave.app.upgrade_database")
    mocker.patch("markweave.app.TemplateService.reclaim_pending")
    mocker.patch(
        "markweave.app.AppComponents",
        side_effect=RuntimeError("components failed"),
    )

    with pytest.raises(RuntimeError, match="dispose failed"):
        build_components(_standalone_component_settings())

    assert disposed == ["observation", "readiness", "main"]


@pytest.mark.unit
def test_distributed_wiring_allows_aws_credential_provider_defaults(
    mocker: MockerFixture,
) -> None:
    reclaim = mocker.patch("markweave.app.TemplateService.reclaim_pending")
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
    engine = mocker.MagicMock()
    engine.dialect.name = "postgresql"
    readiness_engine = mocker.MagicMock()
    readiness_engine.dialect.name = "postgresql"
    observation_engine = mocker.MagicMock()
    observation_engine.dialect.name = "postgresql"
    create_engine = mocker.patch(
        "markweave.app.create_database_engine",
        side_effect=(engine, readiness_engine, observation_engine),
    )
    upgrade = mocker.patch("markweave.app.upgrade_database")
    normal_s3 = mocker.Mock()
    readiness_s3 = mocker.Mock()
    s3_client = mocker.patch(
        "markweave.app.boto3.client", side_effect=(normal_s3, readiness_s3)
    )
    components = build_components(settings)
    assert create_engine.call_args_list == [
        mocker.call(database_url),
        mocker.call(database_url, timeout_seconds=2.0, pool_pre_ping=False),
        mocker.call(database_url, timeout_seconds=2.0, pool_pre_ping=False),
    ]
    upgrade.assert_called_once_with(engine)
    assert s3_client.call_count == 2
    assert s3_client.call_args_list[0] == mocker.call("s3")
    bounded = s3_client.call_args_list[1].kwargs["config"]
    assert bounded.connect_timeout == bounded.read_timeout == 2.0
    assert bounded.retries["max_attempts"] == 0
    object_store = cast(S3ObjectStore, components.object_store)
    readiness = cast(ProfileReadinessProbe, components.readiness)
    assert object_store._client is normal_s3
    assert cast(S3ObjectStore, readiness._objects)._client is readiness_s3
    assert cast(DatabaseReadinessProbe, readiness._database)._engine is readiness_engine
    assert components.readiness.is_ready()
    readiness_engine.connect.assert_called_once_with()
    readiness_engine.connect.return_value.__enter__.return_value.execute.assert_called_once()
    readiness_s3.head_bucket.assert_called_once_with(Bucket="objects")
    engine.connect.assert_not_called()
    observation_engine.connect.assert_not_called()
    normal_s3.head_bucket.assert_not_called()
    reclaim.assert_called_once_with()
    assert components.object_store is not None
    assert components.job_repository is not None
    assert components.retention is not None


@pytest.mark.unit
def test_profile_wiring_covers_standalone_and_explicit_s3_options(
    mocker: MockerFixture,
) -> None:
    reclaim = mocker.patch("markweave.app.TemplateService.reclaim_pending")
    engine = mocker.Mock()
    engine.dialect.name = "sqlite"
    mocker.patch("markweave.app.create_database_engine", return_value=engine)
    mocker.patch("markweave.app.upgrade_database")
    normal_files = mocker.Mock()
    readiness_files = mocker.Mock()
    files = mocker.patch(
        "markweave.app.FilesystemObjectStore",
        side_effect=(normal_files, readiness_files),
    )
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
    standalone_components = build_components(standalone)
    assert standalone_components.object_store is normal_files
    standalone_readiness = cast(ProfileReadinessProbe, standalone_components.readiness)
    assert standalone_readiness._objects is readiness_files
    assert files.call_args_list == [mocker.call(Path("/data"))] * 2

    s3_client = mocker.patch("markweave.app.boto3.client")
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
    common = {
        "endpoint_url": "http://s3.test",
        "region_name": "test-region",
        "aws_access_key_id": "access",
        "aws_secret_access_key": "secret-" + "key",
    }
    assert s3_client.call_args_list == [
        mocker.call("s3", **common),
        mocker.call("s3", **common, config=mocker.ANY),
    ]


@pytest.mark.unit
def test_upload_scanner_assembly_defaults_to_fail_closed_clamav() -> None:
    scanner = build_upload_scanner(_standalone_component_settings())

    assert scanner == ClamAVUploadScanner("127.0.0.1", 3310, 5.0)


@pytest.mark.unit
def test_upload_scanner_assembly_accepts_explicit_trusted_upstream_boundary(
    mocker: MockerFixture,
) -> None:
    settings = _standalone_component_settings(
        malware_scanning_mode=MalwareScanningMode.TRUSTED_UPSTREAM
    )
    warning = mocker.patch("markweave.app.log_event")

    scanner = build_upload_scanner(settings)

    assert isinstance(scanner, TrustedUpstreamUploadScanner)
    warning.assert_called_once_with(
        "malware_scanning_delegated_to_trusted_upstream",
        level=logging.WARNING,
    )


@pytest.mark.unit
def test_upload_scanner_assembly_accepts_explicit_insecure_evaluation_mode(
    mocker: MockerFixture,
) -> None:
    settings = _standalone_component_settings(insecure_evaluation_mode=True)
    warning = mocker.patch("markweave.app.log_event")

    scanner = build_upload_scanner(settings)

    assert isinstance(scanner, TrustedUpstreamUploadScanner)
    warning.assert_called_once_with(
        "insecure_evaluation_mode_enabled",
        level=logging.WARNING,
    )


@pytest.mark.unit
def test_postgresql_migrations_take_a_transaction_advisory_lock(
    mocker: MockerFixture,
) -> None:
    engine = mocker.MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.dialect.name = "postgresql"
    command = mocker.patch("markweave.persistence.migrations.command.upgrade")
    upgrade_database(engine)
    assert connection.execute.call_args.args[1] == {"lock_id": POSTGRES_MIGRATION_LOCK}
    command.assert_called_once()
