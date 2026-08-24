# Isolated PDF conversion

T11 provides the internal synchronous DOCX-to-PDF boundary used by the future T13 worker. It does
not create jobs, persist cancellation state, publish results, or expose an HTTP endpoint.

## Conversion contract

`LibreOfficePdfConverter` accepts validated DOCX bytes, explicit caller-owned limits, immutable
traceability context, and an optional cancellation probe. Every invocation creates a disposable
workspace and a unique LibreOffice user profile. It invokes the configured executable without a
shell, with a fixed `pdf:writer_pdf_Export` argument vector, a minimal environment allowlist, no
standard-input channel, and a new process session.

The caller must configure DOCX bytes, ZIP entries, member and total uncompressed bytes,
compression ratio, PDF bytes, decoded PDF stream bytes, page count, PDF object count, and object
depth. T18 owns the eventual production values. T11 deliberately defines no production defaults.

Timeout, cancellation, non-zero exit, missing engine, unsafe input, invalid output, and limit
violations have distinct stable error codes. Timeout and cancellation terminate the complete
process group with `SIGTERM`, wait for the configured grace period, then use `SIGKILL` if any
descendant remains. A cancellation observed after LibreOffice exits still prevents publication.

## Output validation and traceability

The adapter opens the output without following symbolic links, verifies that its size does not
change while read, and parses it in strict mode. It rejects empty, malformed, encrypted, active,
embedded, over-deep, over-object, over-page, and non-finite-page outputs.

The returned external manifest is canonical JSON without timestamps, paths, user identifiers,
filenames, or document content. It records source and output digests, output size and page geometry,
application and conversion-contract versions, immutable template identity/version/digest, the
Pandoc reader and engine versions, the font-manifest digest, and the fixed LibreOffice export
filter. T13 will persist and publish this result atomically with durable job state.

## Validation boundary

Unit tests cover validation, failure normalization, resource limits, cleanup, and process-group
logic. Real tests run Pandoc, LibreOffice 26.2.5.2, and PDFium 5.13.0 under an arbitrary UID with a
read-only root filesystem, no capabilities, `no-new-privileges`, no network, bounded memory/CPU/
PIDs, and bounded writable mounts. They cover success, concurrent isolated profiles, output
failures, timeout, cancellation, descendant cleanup, and exact raster golden comparison.

Build the current toolchain and reproduce that focused rootless suite with:

```bash
podman build --pull=false --file spikes/toolchain/Containerfile \
  --tag localhost/simple-md-toolchain:t11 spikes/toolchain
spikes/toolchain/run-t11-tests.sh
```

The harness stages a read-only source copy, installs only the locked dependency graph, disables
network access for test execution, and removes its exact temporary volumes on every exit.

T11 cannot exercise the final application image or asynchronous API because T13 and T20 do not yet
exist. The approved sequencing exception keeps that final-image success, authorization,
cancellation, recovery, concurrency, and failure E2E debt in T20/T21; it is not a waiver of that
coverage.
