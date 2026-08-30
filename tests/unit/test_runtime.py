"""Unit coverage for package-native container runtime assembly."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from markweave.app import AppComponents
from markweave.config import ConfigurationError, Settings
from markweave.runtime import (
    build_embedded_app,
    main,
    run_external_worker,
    run_http_service,
)
from tests.settings import template_settings

pytestmark = pytest.mark.unit


def _settings(tmp_path: Path, *, profile: str) -> Settings:
    values: dict[str, Any] = {
        "initial_admin_username": "admin",
        "initial_admin_password": "test-password",
        "conversion_upload_max_bytes": 1_000_000,
        "conversion_request_max_bytes": 1_100_000,
        "conversion_retry_after_seconds": 1,
        "job_result_retention_seconds": 3600,
        "readiness_timeout_seconds": 1,
        "storage_profile": profile,
        **template_settings(
            conversion_font_manifest_path=(
                Path("spikes/toolchain/fonts/manifest.json").resolve()
            )
        ),
    }
    if profile == "standalone":
        values["standalone_data_directory"] = tmp_path / "data"
    else:
        values.update(
            {
                "distributed_database_url": "postgresql://u:p@db/service",
                "s3_bucket": "bucket",
            }
        )
    return Settings(**values)


def test_embedded_runtime_assembles_one_owned_standalone_lifecycle(
    mocker, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, profile="standalone")
    components = mocker.Mock(spec=AppComponents)
    processor = mocker.Mock()
    worker = mocker.Mock()
    components.build_embedded_worker.return_value = worker
    expected = mocker.Mock()
    mocker.patch("markweave.runtime.build_components", return_value=components)
    mocker.patch("markweave.runtime.build_production_processor", return_value=processor)
    create = mocker.patch("markweave.runtime.create_app", return_value=expected)

    assert build_embedded_app(settings) is expected

    components.build_embedded_worker.assert_called_once_with(
        worker_id=mocker.ANY,
        processor=processor,
        thread_name="md-converter-embedded-worker",
    )
    assert create.call_args.kwargs["embedded_worker"] is worker
    assert create.call_args.kwargs["manage_components"] is True


def test_runtime_rejects_mixed_profiles_and_closes_failed_assembly(
    mocker, tmp_path: Path
) -> None:
    distributed = _settings(tmp_path, profile="distributed")
    with pytest.raises(ConfigurationError, match="standalone"):
        build_embedded_app(distributed)

    standalone_for_external = _settings(tmp_path, profile="standalone")
    with pytest.raises(ConfigurationError, match="distributed"):
        run_external_worker(standalone_for_external)

    standalone = _settings(tmp_path, profile="standalone")
    components = mocker.Mock(spec=AppComponents)
    mocker.patch("markweave.runtime.build_components", return_value=components)
    mocker.patch(
        "markweave.runtime.build_production_processor",
        side_effect=ConfigurationError("failure"),
    )
    with pytest.raises(ConfigurationError, match="failure"):
        build_embedded_app(standalone)
    components.close.assert_called_once_with()


def test_external_runtime_runs_signal_aware_loop_and_closes_components(
    mocker, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, profile="distributed")
    components = mocker.Mock(spec=AppComponents)
    processor = mocker.Mock()
    runtime = mocker.Mock()
    components.build_external_worker_runtime.return_value = runtime
    mocker.patch("markweave.runtime.build_components", return_value=components)
    mocker.patch("markweave.runtime.build_production_processor", return_value=processor)

    run_external_worker(settings)

    components.build_external_worker_runtime.assert_called_once_with(
        worker_id=mocker.ANY, processor=processor
    )
    stop = runtime.run.call_args.args[0]
    assert not stop.is_set()
    components.close.assert_called_once_with()


def test_main_dispatches_only_the_two_worker_modes(mocker, tmp_path: Path) -> None:
    app = mocker.Mock()
    settings = _settings(tmp_path, profile="standalone")
    mocker.patch("markweave.runtime.build_embedded_app", return_value=app)
    mocker.patch("markweave.runtime.Settings.load", return_value=settings)
    serve = mocker.patch("markweave.runtime.run_http_service")
    external = mocker.patch("markweave.runtime.run_external_worker")

    assert main(("embedded-worker",)) == 0
    serve.assert_called_once()
    serve.assert_called_once_with(settings)
    assert main(("external-worker",)) == 0
    external.assert_called_once_with()
    with pytest.raises(SystemExit, match="usage"):
        main(("api",))


def test_http_service_uses_embedded_or_api_assembly_by_profile(
    mocker, tmp_path: Path
) -> None:
    standalone = _settings(tmp_path, profile="standalone")
    distributed = _settings(tmp_path, profile="distributed")
    embedded_app = mocker.Mock()
    distributed_app = mocker.Mock()
    embedded = mocker.patch(
        "markweave.runtime.build_embedded_app", return_value=embedded_app
    )
    create = mocker.patch("markweave.runtime.create_app", return_value=distributed_app)
    serve = mocker.patch("markweave.runtime.uvicorn.run")

    assert run_http_service(standalone).value == "standalone"
    embedded.assert_called_once_with(standalone)
    serve.assert_called_with(
        embedded_app,
        host="0.0.0.0",  # noqa: S104 - asserting the configured container bind
        port=8080,
        proxy_headers=False,
        server_header=False,
    )

    assert run_http_service(distributed).value == "distributed"
    create.assert_called_once_with(distributed)
    serve.assert_called_with(
        distributed_app,
        host="0.0.0.0",  # noqa: S104 - asserting the configured container bind
        port=8080,
        proxy_headers=False,
        server_header=False,
    )
