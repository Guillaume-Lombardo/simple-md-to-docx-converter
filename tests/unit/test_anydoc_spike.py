from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

SPIKE = Path(__file__).parents[2] / "spikes" / "anydoc"


def _json(name: str) -> dict[str, Any]:
    return json.loads((SPIKE / name).read_text())


@pytest.mark.unit
def test_anydoc_corpus_is_bounded_hash_locked_and_redistributable() -> None:
    manifest = _json("corpus/manifest.json")
    files = manifest["files"]
    assert isinstance(files, list)
    assert len(files) == 17
    assert manifest["source_commit"] == "42bf1c5ecdde9eb0d96d6bd75a9e6698cf93b14c"
    assert (SPIKE / "LICENSE.anydoc").is_file()
    assert sum(item["size"] for item in files) < 500_000
    expected_paths = set()
    for item in files:
        path = SPIKE / "corpus" / item["path"]
        expected_paths.add(path)
        assert path.stat().st_size == item["size"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    actual_paths = {
        path
        for path in (SPIKE / "corpus").rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    assert actual_paths == expected_paths


@pytest.mark.unit
def test_candidate_contract_is_exact_and_explicitly_blocked() -> None:
    contract = _json("contract.json")
    assert contract["decision_status"] == "blocked_pending_product_decision"
    decisions = contract["blocking_decisions"]
    assert set(decisions) == {"execution_isolation", "asset_serialization"}
    assert all(decision["status"] == "unresolved" for decision in decisions.values())
    assert decisions["execution_isolation"]["choices"] == [
        "approve_disposable_supervised_per_attempt_isolation",
        "defer_until_upstream_execution_controls",
    ]
    assert decisions["asset_serialization"]["choices"] == [
        "defer_until_upstream_asset_aware_renderer_hook",
        "approve_bounded_maintained_adapter_or_fork_after_security_and_maintenance_review",
    ]
    engine = contract["engine"]
    assert engine["version"] == "0.2.4"
    assert engine["source_commit"] == "42bf1c5ecdde9eb0d96d6bd75a9e6698cf93b14c"
    families = contract["format_families"]
    assert [family["family"] for family in families] == [
        "word",
        "powerpoint",
        "excel",
        "opendocument",
        "rtf",
        "epub",
        "csv",
        "pdf",
    ]
    extensions = [
        extension for family in families for extension in family["extensions"]
    ]
    assert len(extensions) == len(set(extensions)) == 21
    assert contract["admission"]["extension_is_authoritative"] is False
    assert contract["pdf"]["document_model_available"] is False
    assert contract["pdf"]["image_preservation"] is False
    assert contract["output"]["with_assets"]["root_markdown"] == "document.md"
    unavailable = contract["output"]["all_assets_unavailable"]
    assert unavailable["media_type"] == "application/zip"
    assert unavailable["mode"] == "markdown_with_unavailable_assets"
    assert unavailable["entries"] == ["document.md", "manifest.json"]
    assert unavailable["asset_count"] == unavailable["asset_bytes"] == 0
    assert "occurrences" in unavailable["unavailable_asset_count"]
    execution = contract["execution"]
    assert execution["native_api_has_cancellation"] is False
    assert execution["native_api_has_deadline"] is False
    assert execution["native_api_has_memory_budget"] is False
    assert execution["production_budget"] is None
    assert execution["reverse_concurrency"] is None


@pytest.mark.unit
@pytest.mark.parametrize("name", ["measurements-host.json", "measurements-ubi9.json"])
def test_measurements_cover_formats_failures_assets_and_execution(name: str) -> None:
    report = _json(name)
    assert report["schema_version"] == 1
    environment = report["environment"]
    assert environment["anydoc_version"] == "0.2.4"
    assert environment["python"].startswith("3.14.")
    assert environment["machine"] == "x86_64"
    assert environment["rayon_num_threads"] == "1"
    assert environment["initial_threads"] == 1
    cases = report["cases"]
    assert len(cases) == 14
    assert {case["detected_format"] for case in cases} == {
        None,
        "doc",
        "docx",
        "epub",
        "odp",
        "ods",
        "odt",
        "pdf",
        "ppt",
        "pptx",
        "rtf",
        "xlsx",
    }
    assert all(case["child_processes_observed"] == [] for case in cases)
    assert any(case["asset_positions"] for case in cases)
    assert any(case["retained_asset_bytes"] > 0 for case in cases)
    assert [failure["expected_category"] for failure in report["failures"]] == [
        "unsupported",
        "malformed",
        "encrypted",
        "resource_limit",
        "needs_ocr",
    ]
    assert report["offline_no_ocr"]["result"] == "needs_ocr"
    assert report["offline_no_ocr"]["firecrawl_environment_ignored"] is True
    cancellation = report["cancellation"]
    assert cancellation["future_cancel_returned"] is False
    assert cancellation["native_work_running_after_cancel_attempt"] is True
    assert cancellation["child_processes_observed"] == []
    assert report["process_inventory"]["loaded_module_names"] == []
    assert all(sample["batch_process_cpu_ms"] > 0 for sample in report["concurrency"])
    assert all(
        sample["peak_live_process_threads"] >= sample["workers"] + 1
        for sample in report["concurrency"]
    )


@pytest.mark.unit
def test_ubi_measurement_has_no_hosted_or_document_engine_escape_hatch() -> None:
    report = _json("measurements-ubi9.json")
    inventory = report["process_inventory"]
    assert all(
        path is None for path in inventory["engine_executables_on_path"].values()
    )
    assert [sample["workers"] for sample in report["concurrency"]] == [1, 2, 4]
    assert all(
        sample["conversions_per_worker"] == 25 for sample in report["concurrency"]
    )
    assert all(sample["batch_process_cpu_ms"] > 0 for sample in report["concurrency"])
    assert all(
        sample["peak_live_process_threads"] >= sample["workers"] + 1
        for sample in report["concurrency"]
    )


@pytest.mark.unit
def test_supply_chain_record_matches_the_lock() -> None:
    evidence = _json("supply-chain.json")
    wheel = evidence["linux_x86_64_wheel"]
    lock = (SPIKE / "uv.lock").read_text()
    assert evidence["license_expression"] == "MIT"
    assert evidence["source"]["commit"] == "42bf1c5ecdde9eb0d96d6bd75a9e6698cf93b14c"
    assert wheel["sha256"] in lock
    assert evidence["source_distribution"]["sha256"] in lock
    assert (
        hashlib.sha256((SPIKE / "LICENSE.anydoc").read_bytes()).hexdigest()
        == evidence["license_sha256"]
    )


@pytest.mark.unit
def test_traceability_schema_is_closed_and_content_free() -> None:
    schema = _json("traceability-manifest.schema.json")
    assert schema["additionalProperties"] is False
    assert schema["required"] == [
        "schema_version",
        "engine",
        "source",
        "result",
        "execution",
    ]
    result = schema["properties"]["result"]
    assert result["required"] == [
        "mode",
        "asset_count",
        "asset_bytes",
        "unavailable_asset_count",
    ]
    assert result["properties"]["mode"]["enum"] == [
        "markdown_with_assets",
        "markdown_with_unavailable_assets",
    ]
    unavailable_rule = schema["allOf"][1]["then"]["properties"]["result"]["properties"]
    assert unavailable_rule["asset_count"]["const"] == 0
    assert unavailable_rule["asset_bytes"]["const"] == 0
    assert unavailable_rule["unavailable_asset_count"]["minimum"] == 1
    property_names = set(schema["properties"])
    for definition in schema["properties"].values():
        if definition.get("type") == "object":
            property_names.update(definition["properties"])
    for forbidden in (
        "filename",
        "markdown",
        "asset_name",
        "local_path",
        "digest",
        "timestamp",
        "secret",
    ):
        assert forbidden not in property_names
    for definition in schema["properties"].values():
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False
