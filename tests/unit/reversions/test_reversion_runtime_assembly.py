"""Configuration and client assembly for production reverse workers."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from pytest_mock import MockerFixture

from markweave.broker.mtls_transport import MtlsBrokerClient
from markweave.broker.unix_transport import UnixBrokerClient
from markweave.config import Settings
from markweave.reversion_jobs.runtime import (
    build_reversion_broker_client,
    build_reversion_execution_policies,
)
from tests.settings import template_settings

pytestmark = pytest.mark.unit


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        **template_settings(),
        "initial_admin_username": "admin",
        "initial_admin_password": "password",
        "conversion_upload_max_bytes": 1_000,
        "conversion_request_max_bytes": 2_000,
        "conversion_retry_after_seconds": 1,
        "job_result_retention_seconds": 3_600,
        "storage_profile": "standalone",
        "standalone_data_directory": tmp_path / "data",
        "reversion_upload_max_bytes": 10_000,
        "reversion_request_max_bytes": 11_000,
        "reversion_retry_after_seconds": 2,
        "reversion_result_retention_seconds": 3_600,
        "reversion_active_limit_per_user": 2,
        "reversion_broker_transport": "unix",
        "reversion_broker_principal_id": UUID("10000000-0000-4000-8000-000000000001"),
        "reversion_broker_policy_revision": "production-v1",
        "reversion_broker_image_digest": "sha256:" + "a" * 64,
        "reversion_broker_operation_timeout_seconds": 5,
        "reversion_broker_socket_path": (tmp_path / "broker.sock").resolve(),
        "reversion_cpu_quota_micros": 100_000,
        "reversion_cpu_period_micros": 100_000,
        "reversion_memory_bytes": 512_000_000,
        "reversion_pid_limit": 32,
        "reversion_workspace_bytes": 40_000,
        "reversion_wall_time_millis": 30_000,
        "reversion_output_max_bytes": 30_000,
        "reversion_image_max_source_bytes": 10_000,
        "reversion_image_max_width_pixels": 100,
        "reversion_image_max_height_pixels": 100,
        "reversion_image_max_pixels": 10_000,
        "reversion_image_max_svg_elements": 100,
        "reversion_image_max_svg_depth": 16,
        "reversion_asset_max_count": 8,
        "reversion_asset_max_total_source_bytes": 20_000,
        "reversion_asset_max_total_output_bytes": 20_000,
        "reversion_markdown_max_bytes": 10_000,
        "reversion_package_max_bytes": 30_000,
        "reversion_running_limit": 1,
        "reversion_worker_lease_seconds": 30,
        "reversion_worker_heartbeat_seconds": 5,
        "reversion_worker_max_duration_seconds": 120,
        "reversion_worker_incomplete_submission_seconds": 300,
        "reversion_worker_collect_poll_seconds": 1,
        "reversion_worker_recovery_lease_seconds": 30,
        "reversion_worker_recovery_batch_size": 16,
        "reversion_worker_cleanup_lease_seconds": 30,
        "reversion_worker_cleanup_interval_seconds": 60,
        "reversion_worker_error_backoff_seconds": 2,
        "reversion_worker_cleanup_batch_size": 16,
        "reversion_worker_reconciliation_ack_batch_size": 8,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_unix_execution_policy_and_client_use_every_explicit_ceiling(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    policies = build_reversion_execution_policies(settings)
    client = build_reversion_broker_client(settings, policies)

    assert isinstance(client, UnixBrokerClient)
    assert policies.worker.running_limit == 1
    assert policies.content_limits.max_input_bytes == 10_000
    assert policies.broker_policy.channel_limits.max_output_bytes == 30_000
    assert policies.broker_policy.limits.memory_bytes == 512_000_000


def test_execution_settings_are_all_or_none_without_affecting_http_only(
    tmp_path: Path,
) -> None:
    values = _settings(tmp_path).model_dump()
    lifecycle = {
        "reversion_upload_max_bytes",
        "reversion_request_max_bytes",
        "reversion_retry_after_seconds",
        "reversion_result_retention_seconds",
        "reversion_active_limit_per_user",
    }
    for name in values.keys() - lifecycle:
        if name.startswith("reversion_"):
            values[name] = None
    http_only = Settings.model_validate(values)
    assert not http_only.reversion_execution_configured

    with pytest.raises(ValueError, match="complete"):
        _settings(tmp_path, reversion_memory_bytes=None)


def test_mtls_client_uses_distinct_worker_and_server_identity(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch(
        "markweave.broker.mtls_transport._tls_context", return_value=mocker.Mock()
    )
    settings = _settings(
        tmp_path,
        reversion_broker_transport="mtls",
        reversion_broker_socket_path=None,
        reversion_broker_endpoint_host="127.0.0.1",
        reversion_broker_endpoint_port=9443,
        reversion_broker_ca_certificate_path=(tmp_path / "ca.pem").resolve(),
        reversion_broker_certificate_chain_path=(tmp_path / "worker.pem").resolve(),
        reversion_broker_private_key_path=(tmp_path / "worker.key").resolve(),
        reversion_broker_worker_uri_san="spiffe://markweave/worker",
        reversion_broker_server_uri_san="spiffe://markweave/broker",
        reversion_broker_server_principal_id=UUID(
            "20000000-0000-4000-8000-000000000002"
        ),
        reversion_broker_server_leaf_sha256=("sha256:" + "b" * 64,),
    )

    client = build_reversion_broker_client(
        settings, build_reversion_execution_policies(settings)
    )

    assert isinstance(client, MtlsBrokerClient)
