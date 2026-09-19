# Reverse-conversion module responsibilities

T79 extracts independent responsibilities without changing public imports, SQL boundaries, the
broker protocol, or the pinned anydoc implementation. The T70 and T71 contracts in the product
specification remain authoritative.

| Boundary | Responsibility | Extraction constraint |
| --- | --- | --- |
| `persistence/reversion_jobs/admission.py` | Submission, idempotency, shared admission limits, source activation, owner-bound reads, internal attempt reads | Each existing session and transaction stays intact; owner predicates and concurrency-test seams remain unchanged. |
| `persistence/reversion_jobs/retention.py` | Terminal expiration, cleanup leasing, object identities, fenced cleanup acknowledgement | Keep selection, locking, metadata clearing, and lease assignment in one transaction. |
| `persistence/reversion_jobs/repository.py` | Public repository composition, claims, broker reconciliation, heartbeat/cancellation, termination proof, recovery, publication | Keep the coupled principal/attempt/job lock ordering and proof fences together. Both mixins share the existing SQL store. |
| `broker/podman_workspace.py` | Deterministic request TAR generation and bounded single-file response TAR validation | No filesystem extraction, runtime authority, or policy decisions. Preserve exact headers, padding, filenames, and ceilings. |
| `broker/podman_runtime.py` | Podman arguments, realized-specification checks, cgroup binding, lifecycle, termination evidence, workspace transfer | Retain runtime authority and the public runtime classes; existing codec names remain aliases. |
| `broker/mtls_control.py` | Canonical control JSON/framing, closed schemas, exchange identifiers, digests | No sockets, TLS material, authentication, clocks, or dispatch. |
| `broker/mtls_transport.py` | TLS identities, peer authentication, deadlines, paired-channel reservations, dispatch, shutdown | Retain public transport imports and existing codec aliases. |
| `broker/unix_transport.py` | Unix peer authentication, bounded framing, lifecycle lock, dispatch | No extraction in this step: its socket identity and lifecycle protections stay together. |
| `broker/kubernetes_runtime.py` and attester transports | Kubernetes realization, node attestation, and evidence validation | No extraction in this step: no change to the optional backend or its trust boundary. |
| `reversions/hyperlinks.py` | Engine-independent HTTP(S) destination validation | No anydoc dependency, model access, document parsing, or mirrored upstream rendering. |
| `reversions/_anydoc_compat.py` | Pinned version/model checks, the single native parse, model validation/traversal, asset positions, upstream-compatible rendering | Remains the sole concrete anydoc model/renderer boundary. |

The bounded steps above can be reviewed independently as persistence, runtime codec, transport
codec, and destination-validation moves. Function bodies and transaction scopes are preserved.
Further decomposition of proof reconciliation or anydoc rendering requires a separate review of
the relevant coupled invariants; module length alone does not justify a split.

The adapter's `UPSTREAM_RENDERER_SURFACES` inventory, exact upstream commit/version, and adjacent
`ANYDOC_COMPAT_LICENSE.txt` remain unchanged. The extracted hyperlink validator is Markweave policy,
not mirrored upstream code. Upgrades still require the existing serializer-parity and compatibility
suites; only a verified upstream asset-aware hook permits removal of the adapter.

Validation uses existing owner-isolation, idempotency/concurrency, lease/reconciliation/proof,
retention, hostile TAR, canonical mTLS, peer-authentication, anydoc parity, and deterministic asset
package tests. SQLite and PostgreSQL run the shared repository contract. Final-image rootless
acceptance must run both storage profiles; a passing unit suite is not a replacement for it.
