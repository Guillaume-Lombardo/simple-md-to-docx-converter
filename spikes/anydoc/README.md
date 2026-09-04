# T69 anydoc feasibility evidence

## Decision

`firecrawl-anydoc==0.2.4` is compatible with CPython 3.14 and the pinned x86-64 UBI 9 base, parses
the requested local format families, exposes embedded bytes plus source-position asset identifiers
for every tested non-PDF document model, and runs without document-engine executables, an ML
runtime, or network access when OCR is explicitly `reject`.

The product manager approved both decisions required for T70 implementation.

For execution, a trusted external isolation broker is the sole holder of Podman/Kubernetes workload
authority. The worker-side supervisor reaches it only through a narrow authenticated owner-
restricted Unix socket or mutually authenticated TLS protocol; neither the application nor the
child receives a raw runtime socket or workload-mutating service account. For each attempt the
broker launches only the reviewed image pinned by immutable digest and a fixed reverse-attempt argv
inside one disposable stable kernel isolation unit. The anydoc call remains in-process only inside
that unit. T71 supplies reviewed configurable CPU, memory, PID/descendant, and bounded workspace or
ephemeral-storage budgets enforced by the broker at the runtime/kernel boundary; the T69 harness
does not select their numeric values. The child receives and returns only bounded local data and has
no network, service-account token, Secret, ConfigMap, PVC, persistence or broker credential, runtime
socket, or publication capability. On cancellation, deadline, lease loss, broker disconnect, or a
hard resource limit, the worker stops accepting output and the broker hard-kills the complete stable
unit, proves it empty, removes it, and only then permits recovery or another attempt. The worker
retains lease heartbeat, attempt-token validation, bounded-output validation, and sole publication
authority.

For asset serialization, T70 may implement one narrowly bounded Markweave-maintained internal
adapter around the exact pinned anydoc `Document` model and renderer behavior. It must consume the
single parsed document and must not add a second parser. All private symbols or mirrored renderer
logic stay behind one compatibility boundary that fails closed for unknown anydoc versions or model
variants. Adoption requires security review, serializer-parity and asset-position tests,
upstream-version compatibility tests, complete dependency/SBOM/license inventory, and explicit T70
maintenance ownership. The adapter must be removed when upstream provides a supported asset-aware
renderer hook; a broader fork remains prohibited.

Silently relying on an asyncio timeout, `Future.cancel()`, a cancellation flag, lease-token
publication fencing alone, or a process-wide memory ceiling is not an acceptable third option.

`contract.json` records the approved execution, serializer, format, packaging, PDF, authorization,
and safe-error contract. `docs/product-specification.md` is the normative product source.

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
The copied upstream fixtures use the MIT text retained as `LICENSE.anydoc`. The seven extension-
alias fixtures are either byte-identical MIT derivatives of the bounded PowerPoint fixture or
deterministic minimal OOXML files generated under this repository's Apache-2.0 license. Their exact
provenance and hashes are recorded in `corpus/manifest.json`; the complete 24-file corpus is
475,234 bytes. `supply-chain.json` carries the machine-readable package inventory. Primary evidence:

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
uv run --project spikes/anydoc python spikes/anydoc/generate_extension_fixtures.py
uv run --project spikes/anydoc --group dev ruff check \
  spikes/anydoc/probe.py spikes/anydoc/generate_extension_fixtures.py
uv run --project spikes/anydoc --group dev ty check \
  spikes/anydoc/probe.py spikes/anydoc/generate_extension_fixtures.py
RAYON_NUM_THREADS=1 uv run --project spikes/anydoc --locked \
  python spikes/anydoc/probe.py --iterations 5 \
  --output spikes/anydoc/measurements-host.json
```

Exact UBI 9 probe:

```bash
podman build --network slirp4netns -f spikes/anydoc/Containerfile \
  -t localhost/markweave-anydoc-t69:0.2.4 .
podman run --rm --network none --read-only --cap-drop all \
  --security-opt no-new-privileges --pids-limit 64 --memory 512m --cpus 1 \
  localhost/markweave-anydoc-t69:0.2.4 --iterations 5 \
  > spikes/anydoc/measurements-ubi9.json
```

The retained `measurements-host.json` and `measurements-ubi9.json` are observations, not deterministic
goldens. The deterministic tests validate their schema and invariant conclusions rather than timing
values. The UBI run used base digest
`sha256:194df4e35e0e5467e1b57266f4d61f821e1b1f567135f074d23066d3604ae653`, image ID
`270ebd2749f0126a20199ef5bcc3084ecd9f14a4a12a37013435bf0ae3dc8b77`, one CPU, 512 MiB,
64 PIDs, no capabilities, a read-only root, and no network. Its inventory records the invoked
`/opt/anydoc-spike/.venv/bin/python` runtime rather than its `/usr/bin/python3.14` symlink target;
the host report preserves the invoked virtual-environment path with its home prefix replaced by
`<home>`.

## Results and low-compute envelope

The UBI import took 19.853 ms wall / 15.628 ms CPU with initial peak RSS 37,032 KiB. Across one
fixture for every admitted extension, first-call conversion ranged from 0.015 ms (generated
PowerPoint template alias) to 6.892 ms (text PDF), and warm calls ranged from 0.012 ms to 5.861 ms. All
21 extensions across the eight requested families reached the expected content-detected parser.
Embedded retained bytes were 70 bytes for the Word, OpenDocument
Text, RTF, and EPUB representatives and 2,354 bytes for OpenDocument Presentation. No conversion
child process was observed.

The minimal `pptm/generated.pptm`, `ppsx/generated.ppsx`, and `ppsm/generated.ppsm` fixtures each
produce `output_units: 0`. They prove extension admission and `pptx` content detection only; they do
not prove slide-content extraction for those three variants. The ordinary `.pptx` representative
produces eight output units and remains the extraction evidence for the shared OOXML parser.

With `RAYON_NUM_THREADS=1`, non-PDF parsing stayed at one process thread and PDF initialized one
bounded Rayon thread (two process threads total). On the one-CPU UBI run, 25 text-PDF conversions
took 155.819, 289.461, and 693.064 ms wall at concurrency 1, 2, and 4; whole-process CPU for those
complete batches was 155.413, 332.901, and 698.707 ms. Sampled peak live process threads, including
the Rayon thread, were 3, 4, and 6, and peak RSS was 38,244, 40,292, and 44,900 KiB. The refreshed
host run recorded wall/whole-process CPU pairs of 164.008/169.094, 171.545/329.987, and
245.623/687.769 ms, with peak live thread counts 3, 4, and 6. More in-process concurrency did not
reduce CPU cost, so the only evidence-supported candidate is one active reverse conversion per
worker with one Rayon thread. T71 must keep both configurable and must reserve capacity separately
from forward work.

The bounded 2,800,210-byte CSV stress input completed in 524.280 ms wall / 479.325 ms CPU but raised
peak RSS from the prior 44,900 KiB to 227,452 KiB. The upstream image-bomb fixture fails locally as
`ResourceLimitError(max_entry_bytes)`. These results show that source bytes do not predict peak
native memory and that 512 MiB is only a measured spike ceiling, not an approved production budget.
No timeout, memory, upload, or result threshold is approved by this harness; T71 must review a true
proposed-limit corpus and retain configurable limits. It may not infer values from the forward
workflow or treat this 512 MiB harness ceiling as a guarantee.

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
or asset resolver. The approved bounded internal adapter therefore traverses the single parsed
`Document` and retains only the minimum pinned renderer behavior required to inject safe relative
asset links. Alt-text replacement remains prohibited because it is ambiguous, and the adapter must
not grow into a second parser or an unconstrained serializer fork.

Under the approved decisions, a result is plain UTF-8 Markdown only when the document model contains
no embedded-asset or unavailable-image source position. A result with an exportable asset is a
deterministic ZIP containing `document.md`, normalized PNGs in first-reference order under
`assets/`, then `manifest.json`. If every reported image is unavailable, the result is still a ZIP
containing `document.md` then `manifest.json`; this preserves explicit traceability instead of
making partial output indistinguishable from an asset-free document. Its mode is
`markdown_with_unavailable_assets`, emitted `asset_count` and `asset_bytes` are zero, and
`unavailable_asset_count` counts image-inline occurrences in source-position traversal order.
Mixed results use `markdown_with_assets` and report the same unavailable occurrence count.
Only referenced assets are emitted; same ids and byte-identical ids reuse the first name; remote
image sources are rejected and never downloaded; unavailable image sources degrade to escaped alt
text. T70 owns the sole canonical manifest and archive serializer.

## Why disposable isolation is required

The 0.2.4 Python signatures accept only bytes/path and optional format. PyO3 calls the Rust parser
inside `py.detach`, releasing the GIL, but passes no cancellation token, deadline, allocator, or
memory budget. In the UBI cancellation probe, `Future.cancel()` returned false after the call began;
native work continued beyond the attempted cancellation and returned only after 524.280 ms. Python
task cancellation and signal handlers cannot unwind this native frame.

A separate heartbeat thread can renew a lease because the GIL is released. It can also notice
cancellation or lease loss, and an attempt token can reject late publication. None of those actions
stops the native allocation/execution. Reclaiming an expired lease can therefore overlap the old
call; refusing reclamation avoids overlap but leaves a crashed attempt permanently wedged. A
process-wide cgroup bounds the whole API/worker container, not one call, and its OOM action kills
the shared service without a deterministic job transition. Calling `os._exit()` from a watchdog
has the same shared-service problem and still supplies no per-call memory boundary.

Consequently, publication fencing alone is insufficient and the complete cancellation/timeout/
memory/heartbeat/no-overlap set is not enforceable in a shared process. The approved design sends
the bounded attempt through a trusted external isolation broker, the sole holder of Podman/
Kubernetes workload authority. The broker creates one immutable-image, fixed-argument disposable
kernel-isolated unit, hard-terminates and proves it empty and removed, and returns only bounded
output plus content-free lifecycle evidence. The application and child receive no raw runtime
authority. T70 owns the authenticated broker protocol/service/backends and terminate-and-verify
runner; T71 configures reviewed budgets and durably binds stable unit identity and termination proof
to leases, recovery, and publication fencing. Recovery remains blocked whenever the prior unit
cannot be proved empty, terminated, and removed; PID exit or delete acknowledgement alone is never
sufficient.
