"""Reproduce the T69 local anydoc compatibility and resource measurements."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

_IMPORT_WALL_START = time.perf_counter_ns()
_IMPORT_CPU_START = time.process_time_ns()
import anydoc  # noqa: E402 - import duration is part of the recorded evidence

_IMPORT_WALL_MS = round((time.perf_counter_ns() - _IMPORT_WALL_START) / 1_000_000, 3)
_IMPORT_CPU_MS = round((time.process_time_ns() - _IMPORT_CPU_START) / 1_000_000, 3)

PINNED_VERSION = "0.2.4"
MINIMUM_ITERATIONS = 2
CASES: tuple[tuple[str, str, anydoc.Format | None, str], ...] = (
    ("word-binary", "doc/text.doc", None, "document"),
    ("word-openxml", "docx/text.docx", None, "document"),
    ("powerpoint-binary", "ppt/handmade-multimaster.ppt", None, "document"),
    ("powerpoint-openxml", "pptx/pres.pptx", None, "document"),
    ("excel-binary", "xls/sheet.xls", None, "document"),
    ("excel-openxml", "xlsx/sheet.xlsx", None, "document"),
    ("excel-binary-workbook", "xlsb/handmade-sheet.xlsb", None, "document"),
    ("opendocument-text", "odt/text.odt", None, "document"),
    ("opendocument-sheet", "ods/sheet.ods", None, "document"),
    ("opendocument-presentation", "odp/pres.odp", None, "document"),
    ("rtf", "rtf/text.rtf", None, "document"),
    ("epub", "epub/book.epub", None, "document"),
    ("csv", "csv/sheet.csv", "csv", "document"),
    ("text-pdf", "pdf/text.pdf", None, "markdown"),
)


def _thread_count() -> int:
    task_path = Path("/proc/self/task")
    return (
        len(tuple(task_path.iterdir()))
        if task_path.exists()
        else threading.active_count()
    )


def _child_pids() -> list[int]:
    task_path = Path("/proc/self/task")
    if not task_path.exists():
        return []
    children: set[int] = set()
    for path in task_path.glob("*/children"):
        try:
            children.update(int(value) for value in path.read_text().split())
        except FileNotFoundError, ProcessLookupError:
            continue
    return sorted(children)


def _convert(
    data: bytes, format_name: anydoc.Format | None, mode: str
) -> tuple[int, int, int]:
    if mode == "markdown":
        markdown = anydoc.to_markdown_bytes(data, format_name, ocr="reject")
        return len(markdown.encode()), 0, 0
    document = anydoc.to_document(data, format_name)
    asset_bytes = sum(len(asset.data) for asset in document.assets)
    return len(document.blocks), len(document.assets), asset_bytes


def _asset_positions(document: Any) -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []

    def visit_inlines(inlines: list[Any] | None, path: str) -> None:
        for index, inline in enumerate(inlines or []):
            inline_path = f"{path}.inline[{index}]"
            if inline.kind == "image" and inline.source is not None:
                positions.append(
                    {
                        "path": inline_path,
                        "source_kind": inline.source.kind,
                        "asset_id": inline.source.asset_id,
                    }
                )
            visit_inlines(inline.content, inline_path)

    def visit_blocks(blocks: list[Any], path: str) -> None:
        for index, block in enumerate(blocks):
            block_path = f"{path}.block[{index}]"
            visit_inlines(block.content, block_path)
            if block.blocks:
                visit_blocks(block.blocks, f"{block_path}.quote")
            if block.list:
                for item_index, item in enumerate(block.list.items):
                    visit_blocks(item.blocks, f"{block_path}.item[{item_index}]")
            if block.table:
                for row_index, row in enumerate(block.table.grid):
                    for column_index, slot in enumerate(row):
                        if slot.kind == "origin" and slot.cell:
                            visit_blocks(
                                slot.cell.blocks,
                                f"{block_path}.cell[{row_index},{column_index}]",
                            )

    visit_blocks(document.blocks, "document")
    for note_index, note in enumerate(document.notes):
        visit_blocks(note.blocks, f"note[{note_index}]")
    return positions


def _measure_case(
    corpus: Path,
    case: tuple[str, str, anydoc.Format | None, str],
    iterations: int,
) -> dict[str, Any]:
    name, relative_path, format_name, mode = case
    data = (corpus / relative_path).read_bytes()
    measurements: list[dict[str, Any]] = []
    process_children: set[int] = set()
    peak_threads = _thread_count()
    output_shape: tuple[int, int, int] | None = None
    asset_positions: list[dict[str, Any]] = []
    for _ in range(iterations):
        rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        cpu_before = time.process_time_ns()
        wall_before = time.perf_counter_ns()
        output_shape = _convert(data, format_name, mode)
        wall_ns = time.perf_counter_ns() - wall_before
        cpu_ns = time.process_time_ns() - cpu_before
        peak_threads = max(peak_threads, _thread_count())
        process_children.update(_child_pids())
        measurements.append(
            {
                "wall_ms": round(wall_ns / 1_000_000, 3),
                "cpu_ms": round(cpu_ns / 1_000_000, 3),
                "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "rss_growth_kib": max(
                    0, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - rss_before
                ),
            }
        )
    if output_shape is None:
        raise RuntimeError("measurement produced no result")
    if mode == "document":
        asset_positions = _asset_positions(anydoc.to_document(data, format_name))
    return {
        "name": name,
        "fixture": relative_path,
        "input_bytes": len(data),
        "detected_format": anydoc.format_from_bytes(data),
        "output_units": output_shape[0],
        "asset_count": output_shape[1],
        "retained_asset_bytes": output_shape[2],
        "asset_positions": asset_positions,
        "threads_before_and_after_max": peak_threads,
        "child_processes_observed": sorted(process_children),
        "measurements": measurements,
    }


def _concurrency_probe(corpus: Path, workers: int) -> dict[str, Any]:
    data = (corpus / "pdf/text.pdf").read_bytes()
    barrier = threading.Barrier(workers)
    conversions_per_worker = 25

    def run() -> dict[str, float]:
        barrier.wait()
        cpu_before = time.thread_time_ns()
        wall_before = time.perf_counter_ns()
        for _ in range(conversions_per_worker):
            _convert(data, None, "markdown")
        return {
            "wall_ms": round((time.perf_counter_ns() - wall_before) / 1_000_000, 3),
            "thread_cpu_ms": round((time.thread_time_ns() - cpu_before) / 1_000_000, 3),
        }

    wall_before = time.perf_counter_ns()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        measurements = list(executor.map(lambda _: run(), range(workers)))
    return {
        "workers": workers,
        "fixture": "pdf/text.pdf",
        "conversions_per_worker": conversions_per_worker,
        "batch_wall_ms": round((time.perf_counter_ns() - wall_before) / 1_000_000, 3),
        "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "process_threads_after": _thread_count(),
        "child_processes_observed": _child_pids(),
        "measurements": measurements,
    }


def _offline_probe(corpus: Path) -> dict[str, Any]:
    old_environment = {
        key: os.environ.get(key) for key in ("FIRECRAWL_API_KEY", "FIRECRAWL_API_URL")
    }
    os.environ["FIRECRAWL_API_KEY"] = "must-not-be-used"
    os.environ["FIRECRAWL_API_URL"] = "http://127.0.0.1:9/forbidden"
    try:
        try:
            anydoc.to_markdown_bytes(
                (corpus / "pdf/handmade-scanned.pdf").read_bytes(), ocr="reject"
            )
        except anydoc.NeedsOcrError as error:
            return {
                "result": "needs_ocr",
                "pages": error.pages,
                "page_count": error.page_count,
                "firecrawl_environment_ignored": True,
            }
        raise RuntimeError("scanned PDF unexpectedly converted")
    finally:
        for key, value in old_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _failure_probe(corpus: Path) -> list[dict[str, Any]]:
    cases: tuple[tuple[str, bytes, anydoc.Format | None], ...] = (
        ("unsupported", b"not a supported document", None),
        ("malformed", b"not a ZIP archive", "docx"),
        ("encrypted", (corpus / "malformed/encrypted--errors.odt").read_bytes(), None),
        (
            "resource_limit",
            (corpus / "abuse/imagebomb--errors.docx").read_bytes(),
            None,
        ),
        ("needs_ocr", (corpus / "pdf/handmade-scanned.pdf").read_bytes(), None),
    )
    results: list[dict[str, Any]] = []
    for expected, data, format_name in cases:
        try:
            anydoc.to_markdown_bytes(data, format_name, ocr="reject")
        except anydoc.ConvertError as error:
            results.append(
                {
                    "expected_category": expected,
                    "exception": type(error).__name__,
                    "limit": getattr(error, "limit", None),
                    "page_count": getattr(error, "page_count", None),
                }
            )
        else:
            raise RuntimeError(f"{expected} fixture unexpectedly converted")
    return results


def _cancellation_probe(corpus: Path) -> dict[str, Any]:
    source = (corpus / "csv/sheet.csv").read_bytes()
    data = source + b"one,two,three\n" * 200_000
    completed = threading.Event()

    def run() -> None:
        try:
            anydoc.to_markdown_bytes(data, "csv")
        finally:
            completed.set()

    wall_before = time.perf_counter_ns()
    cpu_before = time.process_time_ns()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    observed_children: set[int] = set()
    peak_threads = _thread_count()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run)
        time.sleep(0.005)
        cancel_returned = future.cancel()
        still_running_after_cancel = not completed.wait(0.005)
        while not completed.wait(0.001):
            observed_children.update(_child_pids())
            peak_threads = max(peak_threads, _thread_count())
        future.result()
    return {
        "input_bytes": len(data),
        "future_cancel_returned": cancel_returned,
        "native_work_running_after_cancel_attempt": still_running_after_cancel,
        "call_completed_only_normally": completed.is_set(),
        "wall_ms": round((time.perf_counter_ns() - wall_before) / 1_000_000, 3),
        "cpu_ms": round((time.process_time_ns() - cpu_before) / 1_000_000, 3),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "rss_growth_kib": max(
            0, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - rss_before
        ),
        "peak_threads": peak_threads,
        "threads_after": _thread_count(),
        "child_processes_observed": sorted(observed_children | set(_child_pids())),
    }


def _environment() -> dict[str, Any]:
    distribution = importlib.metadata.distribution("firecrawl-anydoc")
    extension = next(
        path
        for path in distribution.files or ()
        if str(path).endswith("_anydoc.abi3.so")
    )
    extension_path = Path(str(distribution.locate_file(extension)))
    return {
        "anydoc_version": importlib.metadata.version("firecrawl-anydoc"),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "extension": extension.name,
        "extension_sha256": hashlib.sha256(extension_path.read_bytes()).hexdigest(),
        "initial_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "initial_threads": _thread_count(),
        "import_wall_ms": _IMPORT_WALL_MS,
        "import_cpu_ms": _IMPORT_CPU_MS,
        "rayon_num_threads": os.environ.get("RAYON_NUM_THREADS"),
        "visible_accelerators": {
            key: os.environ.get(key)
            for key in ("CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES")
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=Path(__file__).parent / "corpus")
    parser.add_argument("--iterations", type=int, default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if importlib.metadata.version("firecrawl-anydoc") != PINNED_VERSION:
        raise SystemExit(f"expected firecrawl-anydoc {PINNED_VERSION}")
    if args.iterations < MINIMUM_ITERATIONS:
        raise SystemExit(
            "--iterations must be at least 2 to measure cold and warm calls"
        )
    report = {
        "schema_version": 1,
        "environment": _environment(),
        "cases": [_measure_case(args.corpus, case, args.iterations) for case in CASES],
        "concurrency": [
            _concurrency_probe(args.corpus, workers) for workers in (1, 2, 4)
        ],
        "offline_no_ocr": _offline_probe(args.corpus),
        "failures": _failure_probe(args.corpus),
        "cancellation": _cancellation_probe(args.corpus),
        "process_inventory": {
            "executable": sys.executable,
            "loaded_module_names": sorted(
                name
                for name in sys.modules
                if any(
                    token in name.lower() for token in ("torch", "tensorflow", "cuda")
                )
            ),
            "engine_executables_on_path": {
                name: shutil.which(name)
                for name in (
                    "chromium",
                    "google-chrome",
                    "pandoc",
                    "libreoffice",
                    "soffice",
                )
            },
        },
    }
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(serialized)
    else:
        sys.stdout.write(serialized)


if __name__ == "__main__":
    main()
