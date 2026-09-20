# Reverse-conversion module responsibilities

T79 introduced bounded extractions; T84 continues the decomposition of the eight remaining
application modules above 1,000 lines. Public imports, SQL boundaries, broker messages and the
pinned anydoc implementation remain unchanged. Tests and scripts are not subject to this size
inventory. The T70, T71 and T74 contracts in the product specification remain authoritative.

## Broker inventory

| Module | Responsibility |
| --- | --- |
| `broker/inventory.py` | Public managed-unit lifecycle, replay reservations, reconciliation pages and proof acknowledgements. |
| `broker/inventory_storage.py` | SQLite connection/transaction primitives, authenticated row mapping, schema verification/migration and inventory manifests. |

`SQLiteBrokerInventory` inherits the storage primitives. Each lifecycle operation retains its
complete transaction body, including authentication, capacity checks and durable transitions.
The v2 migration remains one authenticated atomic rewrite.

## Runtime isolation

| Module | Responsibility |
| --- | --- |
| `broker/podman_runtime.py` | Podman process orchestration, workspace transfer, discovery, incarnation/cgroup binding and termination evidence. |
| `broker/podman_contract.py` | Runtime-unit identity, immutable labels, bounded inspection decoding, fixed create arguments and realized-specification validation. |
| `broker/podman_cgroups.py` | Rootless systemd slice removal, bounded kernel evidence and filesystem validation. |
| `broker/podman_workspace.py` | Deterministic request TAR generation and bounded single-file response TAR validation. |
| `broker/kubernetes_runtime.py` | Control-plane/attester orchestration, attempt lifecycle and proof sequencing. |
| `broker/kubernetes_contracts.py` | Identity/configuration types and control-plane/attester ports. |
| `broker/kubernetes_pod_contract.py` | Fixed Pod construction, canonical projection, approved API defaults and realized-specification digests. |
| `broker/kubernetes_recovery.py` | Closed canonical codecs for prepared and attested restart bindings. |

Runtime classes remain available through their original modules. Manifest and Podman command
construction receive their former instance configuration as explicit parameters; their operation
bodies are unchanged. These functions acquire no workload authority. Recovery codecs neither
contact the control plane nor accept incomplete termination evidence.

## Broker and node-attester transports

| Module | Responsibility |
| --- | --- |
| `broker/mtls_transport.py` | Paired-channel reservations, bounded socket I/O, dispatch, TLS material snapshots and shutdown. |
| `broker/mtls_identity.py` | Endpoint/limit/identity types, TLS context policy and certificate/SAN pin verification. |
| `broker/mtls_control.py` | Canonical control JSON/framing, closed schemas, exchange identifiers and digests. |
| `broker/unix_transport.py` | Unix peer authentication, bounded framing, lifecycle lock, socket identity and dispatch. |
| `broker/response_binding.py` | Transport-independent request/response identity and principal correlation for Unix and mTLS clients. |
| `broker/kubernetes_attester_transport.py` | Bounded HTTPS client/server, mutual TLS, request admission and readiness polling. |
| `broker/kubernetes_attester_service.py` | Node lifecycle operations, durable ledger reconciliation and proof generation. |
| `broker/kubernetes_attester_protocol.py` | Closed canonical request/response payloads and runtime identity comparison. |

Existing public imports and codec aliases remain available. Node ledger transitions remain under
one service lock. Unix socket lifecycle protections and mTLS paired-channel reservation ownership
stay in their respective transports. Extracted codecs do not authenticate or dispatch requests.

## Durable reverse jobs

| Module | Responsibility |
| --- | --- |
| `persistence/reversion_jobs/admission.py` | Submission, idempotency, admission limits, source activation and owner-bound reads. |
| `persistence/reversion_jobs/retention.py` | Terminal expiration, cleanup leasing and fenced object-cleanup acknowledgement. |
| `persistence/reversion_jobs/reconciliation.py` | Principal locking/sequence allocation, reconciliation leases/pages, tombstone retention and acknowledgements. |
| `persistence/reversion_jobs/repository.py` | Public repository composition, claims, heartbeat/cancellation, create intent, termination proof, lease recovery and publication. |
| `persistence/reversion_jobs/common.py` | Shared SQL store primitives and row/domain mapping. |

Mixins share the existing SQL store. Extraction preserves transaction scopes and the coupled
principal/attempt/job lock ordering; the same concurrency-test hooks are inherited by the public
repository. SQLite and PostgreSQL continue to run the same repository contracts.

## Private anydoc compatibility boundary

`reversions._anydoc_compat` keeps its import path and becomes a private package. It remains the
single concrete anydoc model/renderer boundary, with one native parse and no second parser.

| Module | Responsibility |
| --- | --- |
| `_anydoc_compat/__init__.py` | Public adapter entry points, pinned version/private-symbol inventory, closed model validation, single native parse, asset extraction and complete-document rendering. |
| `_anydoc_compat/render_context.py` | Render results, shared state, inline-run records and escape options. |
| `_anydoc_compat/traversal.py` | Model traversal, note numbering and source-order image occurrences. |
| `_anydoc_compat/blocks.py` | Headings, lists, tables, block quotes, notes and anchor resolution. |
| `_anydoc_compat/inlines.py` | Inline normalization, styles, links and injected image positions. |
| `_anydoc_compat/escaping.py` | Markdown escaping, URL encoding, code fences and math spans. |
| `reversions/hyperlinks.py` | Engine-independent HTTP(S) destination validation. |

The renderer modules retain the upstream-derived behavior, and their docstrings identify the
unchanged `reversions/ANYDOC_COMPAT_LICENSE.txt`. The exact engine version, upstream commit and
`UPSTREAM_RENDERER_SURFACES` inventory are unchanged. Internal imports flow from document/block
rendering to inline rendering, escaping and shared context; traversal has no renderer dependency.
Upgrades still require serializer-parity and compatibility suites. Only a verified upstream
asset-aware hook permits removal of the adapter.

## Verification

Existing owner-isolation, idempotency/concurrency, lease/reconciliation/proof, authenticated
inventory, hostile TAR, canonical mTLS, peer-authentication, anydoc parity and deterministic asset
package tests cover the moves. Test doubles follow the extracted cgroup boundary; the anydoc
license assertion follows the package layout while retaining the original license file.

T84 also compares the original function ASTs with the extracted implementations: transaction and
security bodies stay intact, with only explicit configuration parameters substituted in the three
extracted Pod/Podman construction and validation functions. Ticket progress records actual local
checks and any unavailable final-image or document-engine validation; unit results do not replace
those boundaries.
