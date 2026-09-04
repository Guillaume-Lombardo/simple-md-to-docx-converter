# Reverse-attempt image

Reverse conversion uses a dedicated image built from
`containers/reverse-attempt/Containerfile`. It is deliberately separate from the ordinary
Markweave application image: the `all` installation extra excludes `firecrawl-anydoc`, while the
attempt image installs only the `reverse-attempt` extra.

The image has one fixed entrypoint:

```text
python -m markweave.reversions.attempt_main
```

It accepts no command arguments. The external isolation broker supplies a fresh bounded `/work`
ephemeral mount containing `request.json` and `source.bin`, plus the required
`MARKWEAVE_REVERSE_MAX_INPUT_BYTES` and `MARKWEAVE_REVERSE_MAX_OUTPUT_BYTES` transport ceilings.
The image defines no default for either T71-owned value and fails closed when either is absent or
noncanonical. The attempt writes `response.json` and, on success, `result.bin` to that same mount.
Document bytes never cross stdout or stderr. The
broker, not the image, enforces no network, a read-only root, no capabilities, no-new-privileges,
an arbitrary UID, the reviewed CPU, memory, PID, workspace and autonomous wall-time ceilings, and
whole-unit termination. The image contains no runtime socket, service-account token, application
credentials, Pandoc, LibreOffice, browser, Mermaid CLI, HTTP server, or publication client.

The rootless smoke test runs a real asset-bearing DOCX through the fixed workspace entrypoint and
verifies its closed Markdown/PNG/manifest ZIP. It also rasterizes a safe SVG through the installed
CairoSVG/Cairo path under the same arbitrary-UID, read-only-root, networkless policy.

`RAYON_NUM_THREADS=1` preserves the low-compute policy measured by T69. The image does not expose a
port and `/work` is its sole runtime-writable location. Input names, output names, the image,
entrypoint, arguments and containment policy are fixed broker-side and cannot be selected from a
document or request.

Build and inspect the image locally with:

```bash
bash scripts/container/build-reverse-attempt.sh
bash scripts/container/smoke-reverse-attempt.sh
bash scripts/container/supply-chain.sh \
  localhost/markweave-reverse-attempt:t70 artifacts/reverse-attempt ci reverse-attempt
```

The supply-chain command generates CycloneDX and SPDX SBOMs from the built OCI archive. Its
reverse-attempt profile also extracts the Cargo CycloneDX SBOM embedded in the pinned anydoc wheel,
verifies its exact digest, component graph and license coverage, and scans both CycloneDX graphs
with Grype. CI retains the exact OCI archive, both vulnerability reports, all SBOMs, metadata, and
their checksum manifest as one self-verifiable evidence bundle. The reviewed anydoc artifact,
provenance, license and native-component inventory are recorded in
`spikes/anydoc/supply-chain.json`; its MIT license and the compatibility adapter's full MIT notice
are copied into the attempt image. The machine-readable T70 inventory in
`docs/evidence/t70-reverse-attempt-supply-chain.json` identifies the exact public Python surface and
every mirrored upstream renderer behavior. Every anydoc upgrade must refresh the lock, provenance,
embedded Cargo inventory, vulnerability scans, compatibility/parity suite, both image SBOM formats
and this image validation before adoption.
