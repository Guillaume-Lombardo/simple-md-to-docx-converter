# T69 anydoc feasibility evidence

## Decision

`firecrawl-anydoc==0.2.4` is compatible with CPython 3.14 and the pinned x86-64 UBI 9 base, parses
the requested local format families, exposes embedded bytes plus source-position asset identifiers
for every tested non-PDF document model, and runs without document-engine executables, an ML
runtime, or network access when OCR is explicitly `reject`.

It is **not approved for production integration**. T70 is blocked because the fixed synchronous
in-process interface cannot enforce the required hard cancellation, deadline, per-call memory, and
lost-lease/no-overlap semantics. The public serializer also cannot render an already parsed
`Document` or resolve an embedded asset id to a relative image link. Implementing that behavior in
Markweave would duplicate the upstream serializer, which T69 explicitly forbids.

The product manager must choose one of these contract changes before T70 starts:

1. Permit one disposable, separately supervised reverse-conversion process or container per active
   attempt. The anydoc call remains in-process inside that isolated worker, while its supervisor
   applies a per-attempt cgroup, kills the whole worker on cancellation/deadline/lease loss, confirms
   termination before recovery, and fences publication by attempt token. This relaxes the current
   shared-worker interpretation of “in-process” and requires a standalone restart/availability
   design.
2. Keep the existing in-process shared-worker contract and defer T70 until upstream exposes
   cooperative cancellation/deadline/resource-budget inputs plus an asset-aware renderer hook.

Silently relying on an asyncio timeout, `Future.cancel()`, a cancellation flag, lease-token
publication fencing alone, or a process-wide memory ceiling is not an acceptable third option.

`contract.json` records the validated candidate format, packaging, PDF, authorization, and safe
error contract. It remains non-normative while this decision is blocked and while
`docs/product-specification.md` is owned by T67.

## Pinned artifact and provenance

The dedicated uv project pins `firecrawl-anydoc==0.2.4`; `uv.lock` pins the source distribution and
all published wheels by SHA-256. The evaluated Linux artifact is
`firecrawl_anydoc-0.2.4-cp310-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl`, SHA-256
`0e5aed01bf6d4e5c588d3363888293f2ceeb9feb00449a32e0ba993797cf0bd3`. Its stable ABI wheel loads
under UBI CPython 3.14.5 and local CPython 3.14.6. Both installations produced native-extension
SHA-256 `5bd7463287a54040d08e26130737a660f733028a2480b880069b6eedb2311041`.

PyPI identifies the package as MIT and binds this wheel through Trusted Publishing to
`firecrawl/anydoc`, workflow `release.yml`, tag `v0.2.4`, source commit
`42bf1c5ecdde9eb0d96d6bd75a9e6698cf93b14c`, and Sigstore transparency entry `2618684387`.
The upstream tag and the copied fixture corpus use the MIT text retained as `LICENSE.anydoc`.
`supply-chain.json` carries the machine-readable inventory. Primary evidence:

- <https://pypi.org/project/firecrawl-anydoc/0.2.4/>
- <https://pypi.org/integrity/firecrawl-anydoc/0.2.4/firecrawl_anydoc-0.2.4-cp310-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl/provenance>
- <https://github.com/firecrawl/anydoc/tree/42bf1c5ecdde9eb0d96d6bd75a9e6698cf93b14c>
- <https://github.com/firecrawl/anydoc/blob/42bf1c5ecdde9eb0d96d6bd75a9e6698cf93b14c/LICENSE>

`pip-audit==2.10.0` reported no known vulnerability for the exact wheel on 2026-09-03. This is not a
complete native supply-chain clearance: the wheel has no component SBOM, and Python package
metadata cannot expose its compiled Rust dependency graph. T70 must regenerate an SBOM and scan the
exact wheel plus the upstream `Cargo.lock` before adoption.

## Reproduction

Host probe:

```bash
uv sync --project spikes/anydoc --locked
RAYON_NUM_THREADS=1 uv run --project spikes/anydoc --locked \
  python spikes/anydoc/probe.py --iterations 5
```

Exact UBI 9 probe:

```bash
podman build -f spikes/anydoc/Containerfile \
  -t localhost/markweave-anydoc-t69:0.2.4 .
podman run --rm --network none --read-only --cap-drop all \
  --security-opt no-new-privileges --pids-limit 64 --memory 512m --cpus 1 \
  localhost/markweave-anydoc-t69:0.2.4 --iterations 5
```

The retained `measurements-host.json` and `measurements-ubi9.json` are observations, not deterministic
goldens. The deterministic tests validate their schema and invariant conclusions rather than timing
values. The UBI run used base digest
`sha256:194df4e35e0e5467e1b57266f4d61f821e1b1f567135f074d23066d3604ae653`, image ID
`52283fbb112d613b98ac22d58ab4773aaba3827548e072c440a2c57419d8e0c7`, one CPU, 512 MiB,
64 PIDs, no capabilities, a read-only root, and no network.

## Results and low-compute envelope

The UBI import took 13.467 ms wall / 13.470 ms CPU with initial peak RSS 37,176 KiB. For the small
redistributable representatives, first-call conversion ranged from 0.031 ms (binary PowerPoint) to
7.489 ms (text PDF), and warm calls ranged from 0.014 ms to 5.860 ms. Every requested family
converted. Embedded retained bytes were 70 bytes for the Word, OpenDocument Text, RTF, and EPUB
fixtures and 2,354 bytes for the OpenDocument Presentation fixture. No conversion child process
was observed.

With `RAYON_NUM_THREADS=1`, non-PDF parsing stayed at one process thread and PDF initialized one
bounded Rayon thread (two process threads total). On the one-CPU UBI run, 25 text-PDF conversions
took 154.793 ms at concurrency 1, 263.759 ms at concurrency 2, and 683.327 ms at concurrency 4.
Peak RSS increased from 38,392 KiB to 44,920 KiB. More in-process concurrency reduced neither
latency nor CPU cost, so the only evidence-supported candidate is one active reverse conversion per
worker with one Rayon thread. T71 must keep both configurable and must reserve capacity separately
from forward work.

The bounded 2,800,210-byte CSV stress input completed in 501.831 ms wall / 450.322 ms CPU but raised
peak RSS from the prior 44,920 KiB to 227,516 KiB. The upstream image-bomb fixture fails locally as
`ResourceLimitError(max_entry_bytes)`. These results show that source bytes do not predict peak
native memory and that 512 MiB is only a measured spike ceiling, not an approved production budget.
No timeout, memory, upload, or result threshold can be approved until the isolation decision and a
true proposed-limit corpus are reviewed. T71 therefore retains configurable limits, but it may not
infer values from the forward workflow or treat this 512 MiB harness ceiling as a guarantee.

## CPU-only and local-only evidence

The exact runtime image contains only UBI Python and the locked anydoc environment. Chromium,
Google Chrome, Pandoc, LibreOffice, and `soffice` are absent from `PATH`. The native extension's
dynamic dependencies are only `libgcc_s`, `librt`, `libpthread`, `libm`, `libdl`, `libc`, and the
ELF loader. The upstream Rust manifest has parsing libraries only (`cfb`, `csv`, `flate2`,
`encoding_rs`, `log`, `pdf-inspector`, `quick-xml`, and `zip`), with no GPU or ML dependency. No
Torch, TensorFlow, or CUDA module was loaded, and the runs exposed no accelerator environment.

The runtime had `--network none`. A scanned two-page PDF returned `NeedsOcrError` while
`FIRECRAWL_API_KEY` and `FIRECRAWL_API_URL` were deliberately present, proving that explicit
`ocr="reject"` ignores hosted configuration. Production must always pass that literal value and
must never make user or configuration data select `ocr="hosted"`. The Python wrapper contains the
only HTTP implementation; local native parsing itself has no network client dependency.

## Format and failure contract

`contract.json` is the exact ordered matrix. Extensions are hints, never authority. The server must
detect content first, require the extension family to agree, and reject unknown content. CSV is the
only exception because it has no signature; `.csv` selects the parser only after bounded text
validation. The probe confirmed `UnsupportedError`, `MalformedError`, `EncryptedError`,
`ResourceLimitError`, and `NeedsOcrError`; T70 maps those to the content-free categories in the
contract without returning upstream messages, package parts, paths, or document data.

PDF is text extraction only. The PDF path bypasses `Document`, exposes no assets or source-position
image identifiers, and rejects the whole file when any page produces no text. Markweave must not
claim PDF layout or image preservation.

For other formats, `Document` provides ordered blocks/inlines, each embedded image inline carries
an `asset_id`, and `Document.assets` carries its bytes and declared media type. The probe records
the exact structural paths of those references. T70 can securely normalize the referenced bytes,
but 0.2.4's public Markdown renderer turns embedded images into alt text and accepts no `Document`
or asset resolver. A safe implementation therefore requires the upstream renderer hook described
in the product decision; alt-text replacement is ambiguous and a Markweave renderer would duplicate
upstream structure semantics.

If that hook and enforceable execution isolation are approved, asset-free results are plain UTF-8
Markdown. Results with assets are deterministic ZIPs containing `document.md`, normalized PNGs in
first-reference order under `assets/`, then `manifest.json`. Only referenced assets are emitted;
same ids and byte-identical ids reuse the first name; remote image sources are rejected and never
downloaded; unavailable image sources degrade to escaped alt text. T70 owns the sole canonical
manifest and archive serializer.

## Why execution is blocked

The 0.2.4 Python signatures accept only bytes/path and optional format. PyO3 calls the Rust parser
inside `py.detach`, releasing the GIL, but passes no cancellation token, deadline, allocator, or
memory budget. In the UBI cancellation probe, `Future.cancel()` returned false after the call began;
native work continued beyond the attempted cancellation and returned only after 501.831 ms. Python
task cancellation and signal handlers cannot unwind this native frame.

A separate heartbeat thread can renew a lease because the GIL is released. It can also notice
cancellation or lease loss, and an attempt token can reject late publication. None of those actions
stops the native allocation/execution. Reclaiming an expired lease can therefore overlap the old
call; refusing reclamation avoids overlap but leaves a crashed attempt permanently wedged. A
process-wide cgroup bounds the whole API/worker container, not one call, and its OOM action kills
the shared service without a deterministic job transition. Calling `os._exit()` from a watchdog
has the same shared-service problem and still supplies no per-call memory boundary.

Consequently, publication fencing is enforceable but the complete cancellation/timeout/memory/
heartbeat/no-overlap set is not enforceable in the current shared process. T70 must remain blocked
until the product manager chooses isolated disposable execution or waits for the required upstream
APIs.
