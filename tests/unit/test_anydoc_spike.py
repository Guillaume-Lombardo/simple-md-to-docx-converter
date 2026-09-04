from __future__ import annotations

import hashlib
import json
import runpy
import shutil
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

SPIKE = Path(__file__).parents[2] / "spikes" / "anydoc"


def _json(name: str) -> dict[str, Any]:
    return json.loads((SPIKE / name).read_text())


@pytest.mark.unit
def test_anydoc_corpus_is_bounded_hash_locked_and_redistributable() -> None:
    manifest = _json("corpus/manifest.json")
    files = manifest["files"]
    assert isinstance(files, list)
    assert len(files) == 24
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
def test_extension_alias_fixtures_are_deterministically_generated(
    tmp_path: Path,
) -> None:
    source = SPIKE / "corpus/ppt/handmade-multimaster.ppt"
    generated_corpus = tmp_path / "corpus"
    (generated_corpus / "ppt").mkdir(parents=True)
    shutil.copyfile(source, generated_corpus / "ppt/handmade-multimaster.ppt")
    generator = runpy.run_path(str(SPIKE / "generate_extension_fixtures.py"))[
        "generate"
    ]
    generator(generated_corpus)

    generated_paths = {
        "docm/generated.docm",
        "pps/handmade-multimaster.pps",
        "pot/handmade-multimaster.pot",
        "pptm/generated.pptm",
        "ppsx/generated.ppsx",
        "ppsm/generated.ppsm",
        "xlsm/generated.xlsm",
    }
    manifest = _json("corpus/manifest.json")
    records = {item["path"]: item for item in manifest["files"]}
    assert generated_paths <= records.keys()
    for relative_path in generated_paths:
        expected = SPIKE / "corpus" / relative_path
        regenerated = generated_corpus / relative_path
        assert regenerated.read_bytes() == expected.read_bytes()
        record = records[relative_path]
        assert record["license_expression"] in {"Apache-2.0", "MIT"}
        if record["license_expression"] == "Apache-2.0":
            assert record["generated_by"] == "../generate_extension_fixtures.py"
        else:
            assert (
                expected.read_bytes()
                == (SPIKE / "corpus" / record["source_path"]).read_bytes()
            )


@pytest.mark.unit
def test_candidate_contract_records_approved_product_decisions() -> None:
    contract = _json("contract.json")
    assert contract["decision_status"] == "approved_for_t70_implementation"
    decisions = contract["product_decisions"]
    assert decisions == {
        "execution_isolation": {
            "status": "approved",
            "selected": "external_isolation_broker_managed_disposable_runtime_unit",
            "requirements": [
                "the anydoc native call runs in-process only inside one immutable-image fixed-argument disposable Podman container or Kubernetes workload placed in a dedicated kernel isolation unit for one attempt",
                "a trusted external isolation broker exclusively holds Podman or Kubernetes workload authority and the application receives no raw OCI socket or workload-mutating service account",
                "the worker-side supervisor reaches the broker only through a narrow authenticated owner-restricted Unix socket or mutually authenticated TLS protocol",
                "the child has no network, service-account token, Secret, ConfigMap, PVC, persistence or broker credential, runtime socket, or publication capability and receives and returns only bounded local data",
                "T71 supplies reviewed per-attempt CPU, memory, PID and descendant, and workspace or ephemeral-storage budgets enforced by the broker at the runtime and kernel boundary",
                "cancellation, deadline, lease loss, broker disconnect, or a hard resource limit stops output acceptance and makes the broker hard-kill the complete stable unit",
                "the broker proves the stable isolation unit empty and removed and T71 durably records that proof before recovery or another attempt may start",
                "the runtime autonomously enforces the reviewed T71-configured wall-time deadline applied at unit creation even if the worker or broker process crashes",
                "broker startup and reconnect discover and sweep every managed orphan and refuse readiness and creation until reconciliation and termination proof complete",
                "a crash between kill or removal and proof resumes from authenticated managed identity and runtime state while worker recovery remains blocked until proof is durably recorded",
                "the worker-side supervisor owns heartbeat and the attempt token, validates bounded output, revalidates the lease and token, and is the only publisher",
            ],
        },
        "asset_serialization": {
            "status": "approved",
            "selected": "bounded_maintained_internal_adapter_around_pinned_anydoc_internals",
            "requirements": [
                "consume the single parsed anydoc Document and never introduce a second document parser",
                "confine all private or mirrored upstream renderer behavior to one owned compatibility boundary",
                "fail closed on an unknown anydoc version or document-model variant",
                "pass security review and serializer-parity, asset-position, and upstream-version compatibility tests",
                "inventory the exact private surface and copied upstream code in SBOM and license evidence",
                "assign maintenance ownership to T70 and remove the adapter when upstream provides an official asset-aware hook",
            ],
        },
    }
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
    assert execution == {
        "cpu_only": True,
        "ocr": False,
        "hosted_fallback": False,
        "native_api_has_cancellation": False,
        "native_api_has_deadline": False,
        "native_api_has_memory_budget": False,
        "hard_control_decision": (
            "external_isolation_broker_managed_disposable_runtime_unit"
        ),
        "isolation_unit_sharing": "one attempt only",
        "isolation_unit_identity": (
            "stable kernel isolation unit or cgroup identity, never PID identity alone"
        ),
        "native_call_scope": "in-process inside the disposable isolation unit only",
        "runtime_authority_owner": "external_isolation_broker",
        "broker_transports": ["authenticated_unix_socket", "mutual_tls"],
        "application_has_raw_runtime_authority": False,
        "attempt_image_selection": "immutable_digest_fixed_by_broker",
        "attempt_argv_selection": "fixed_by_broker",
        "runtime_deadline_enforcement": (
            "applied_at_creation_and_independent_of_worker_or_broker_liveness"
        ),
        "managed_unit_discovery": [
            "bounded_persistent_content_free_broker_inventory",
            "immutable_broker_authored_runtime_labels_plus_authenticated_worker_identity",
        ],
        "managed_unit_metadata_user_controlled": False,
        "startup_and_reconnect_action": (
            "reconcile_sweep_hard_kill_and_prove_every_managed_orphan"
        ),
        "readiness_and_create_during_incomplete_sweep": "refused",
        "crash_between_kill_or_removal_and_proof": (
            "resume_reconciliation_and_reconstruct_proof_from_authenticated_managed_identity_and_runtime_state"
        ),
        "child_forbidden_resources": [
            "network",
            "service_account_token",
            "secrets",
            "config_maps",
            "persistent_volumes",
            "persistence_credentials",
            "broker_credentials",
            "runtime_socket",
            "publication_capability",
        ],
        "supervisor_owns_heartbeat_and_publication": True,
        "supervisor_role": "worker_side_attempt_supervisor",
        "hard_kill_target": "complete stable isolation unit including every descendant",
        "broker_termination_proof_requires": [
            "runtime_confirmed_exit",
            "stable_unit_empty",
            "stable_unit_removed",
        ],
        "recovery_precondition": (
            "broker termination proof for stable isolation unit is durably recorded"
        ),
        "pid_only_termination_proof_allowed": False,
        "kernel_enforced_configurable_budgets": [
            "cpu",
            "memory",
            "pids_and_descendants",
            "workspace_or_ephemeral_storage",
        ],
        "budget_configuration_owner": "T71",
        "child_has_network_or_persistence_credentials": False,
        "production_budget": None,
        "reverse_concurrency": None,
    }


@pytest.mark.unit
def test_approved_decisions_are_reflected_in_spec_and_t70_contract() -> None:
    root = SPIKE.parent.parent
    product_spec = " ".join(
        (root / "docs/product-specification.md").read_text().split()
    )
    t70 = " ".join(
        (root / "tickets/T70-secure-asset-aware-anydoc.md").read_text().split()
    )
    expected_spec_decisions = (
        "A trusted external isolation broker, separate from the application worker and "
        "any attempt unit, exclusively owns Podman or Kubernetes workload authority.",
        "Use one narrowly bounded maintained internal adapter around the pinned anydoc "
        "document model and renderer behavior; prohibit a second parser or broad fork, "
        "require security/parity/compatibility/SBOM/license ownership, and remove it when "
        "upstream provides a supported asset-aware hook",
    )
    for decision in expected_spec_decisions:
        assert decision in product_spec

    expected_t70_requirements = (
        "Implement a trusted external isolation broker as the only holder of Podman/"
        "Kubernetes workload authority.",
        "Implement one narrowly bounded maintained internal compatibility adapter around "
        "the pinned anydoc `Document` model and renderer behavior. Consume the single "
        "parsed document; never reparse source bytes or add a second parser. Inventory "
        "every private symbol and minimally mirrored upstream renderer behavior in one "
        "fail-closed module boundary, including applicable license notices, and reject "
        "unknown anydoc versions or document-model variants.",
        "Require security review, asset-free serializer parity against the pinned upstream "
        "renderer, source-position asset-link goldens, and compatibility tests for every "
        "anydoc update. Include the adapter and exact upstream surface in dependency, "
        "SBOM, license, and vulnerability evidence. T70 owns maintenance and removes the "
        "adapter once upstream supplies a supported asset-aware hook; a broad fork is not "
        "authorized.",
    )
    for requirement in expected_t70_requirements:
        assert requirement in t70
    for invariant in (
        "authenticated owner-restricted Unix socket or mutually authenticated TLS",
        "no raw OCI socket or workload-mutating service account",
        "no network, service-account token, Secret, ConfigMap, PVC",
        "prove it empty, remove it, and return a content-free proof before recovery",
        "runtime at creation, so",
        "refuses readiness and every create request until reconciliation completes",
        "crash between kill/removal and proof",
    ):
        assert invariant in t70


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
    assert len(cases) == 21
    contract = _json("contract.json")
    admitted_extensions = {
        extension
        for family in contract["format_families"]
        for extension in family["extensions"]
    }
    assert {Path(case["fixture"]).suffix for case in cases} == admitted_extensions
    family_by_extension = {
        extension: family
        for family in contract["format_families"]
        for extension in family["extensions"]
    }
    for case in cases:
        family = family_by_extension[Path(case["fixture"]).suffix]
        if family["family"] == "csv":
            assert case["detected_format"] is None
        else:
            assert case["detected_format"] in family["detected_formats"]
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
    serialized = json.dumps(report)
    assert "/home/" not in serialized
    if name == "measurements-host.json":
        assert report["process_inventory"]["executable"].startswith("<home>/")
    else:
        assert report["process_inventory"]["executable"] == (
            "/opt/anydoc-spike/.venv/bin/python"
        )
    cases_by_fixture = {case["fixture"]: case for case in cases}
    for fixture in (
        "pptm/generated.pptm",
        "ppsx/generated.ppsx",
        "ppsm/generated.ppsm",
    ):
        assert cases_by_fixture[fixture]["detected_format"] == "pptx"
        assert cases_by_fixture[fixture]["output_units"] == 0
    assert all(sample["batch_process_cpu_ms"] > 0 for sample in report["concurrency"])
    assert all(
        sample["peak_live_process_threads"] >= sample["workers"] + 1
        for sample in report["concurrency"]
    )


@pytest.mark.unit
def test_ubi_measurement_has_no_hosted_or_document_engine_escape_hatch() -> None:
    report = _json("measurements-ubi9.json")
    inventory = report["process_inventory"]
    assert set(inventory["engine_executables_on_path"]) == {
        "chromium",
        "google-chrome",
        "libreoffice",
        "pandoc",
        "soffice",
    }
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
    lock = tomllib.loads((SPIKE / "uv.lock").read_text())
    packages = [
        package
        for package in lock["package"]
        if package["name"] == "firecrawl-anydoc" and package["version"] == "0.2.4"
    ]
    assert len(packages) == 1
    package = packages[0]
    locked_wheels = {item["url"]: item for item in package["wheels"]}
    assert wheel["url"] in locked_wheels
    locked_wheel = locked_wheels[wheel["url"]]
    assert Path(urlparse(wheel["url"]).path).name == wheel["filename"]
    assert locked_wheel["hash"] == f"sha256:{wheel['sha256']}"
    assert locked_wheel["size"] == wheel["size"]
    source_distribution = evidence["source_distribution"]
    assert package["sdist"]["url"] == source_distribution["url"]
    assert (
        Path(urlparse(source_distribution["url"]).path).name
        == source_distribution["filename"]
    )
    assert package["sdist"]["hash"] == f"sha256:{source_distribution['sha256']}"
    assert package["sdist"]["size"] == source_distribution["size"]
    assert evidence["license_expression"] == "MIT"
    assert evidence["source"]["commit"] == "42bf1c5ecdde9eb0d96d6bd75a9e6698cf93b14c"
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
    source = schema["properties"]["source"]
    schema_pairs: set[tuple[str, str]] = set()
    for option in source["oneOf"]:
        properties = option["properties"]
        family = properties["family"]["const"]
        detected = properties["detected_format"]
        formats = detected.get("enum", [detected.get("const")])
        schema_pairs.update((family, format_name) for format_name in formats)
    contract_pairs: set[tuple[str, str]] = set()
    for family in _json("contract.json")["format_families"]:
        formats = family["detected_formats"] or [family["selected_parser_format"]]
        contract_pairs.update(
            (family["family"], format_name) for format_name in formats
        )
    assert schema_pairs == contract_pairs
    assert ("word", "pptx") not in schema_pairs
    assert ("powerpoint", "docx") not in schema_pairs
    assert ("csv", "pdf") not in schema_pairs
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
