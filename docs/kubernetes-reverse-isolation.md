# Optional Kubernetes reverse isolation

The Kubernetes backend is optional and does not replace the rootless Podman backend. It implements
the same `IsolationRuntime` port, so the T70 authenticated Unix and mTLS worker transports,
crash-consistent inventory, reconciliation, tombstones, and proof acknowledgement remain unchanged.

## Trust boundaries

The application and external worker possess only a broker client identity. They have no Kubernetes,
OCI, CRI, node, or workload-mutating credentials. The broker alone receives namespace-scoped Pod
authority in the attempt namespace. It authors the immutable Pod specification and uses an exact
Pod UID precondition for deletion. The attempt service account has no RBAC and its
token is not mounted.

A separately deployed node attester, in a different namespace outside the broker Role and
`pods/exec` scope, is the only component that can query the bounded CRI read proxy and stable cgroup. The
broker has no Node-reading ClusterRole. The attester receives only `get` access to its Node and
attempt Pods; no mutating verb is granted. Its mTLS
endpoint accepts only the broker identity and returns bounded, content-free evidence. The attester
never accepts a command, argv, image, path, PID, cgroup, sandbox, or node chosen independently by
the caller: it derives these identities from the Pod UID and verifies them against CRI metadata.

The reference DaemonSet runs the production attester process as root without privilege, added
capabilities, host PID/IPC, or a writable root filesystem. Root is required only to read the
root-owned proxy socket, cgroup v2 hierarchy, process mount metadata, and kubelet configuration. A
separate Envoy sidecar alone mounts the raw containerd socket. Its HTTP/2 routes permit only CRI v1
`Version`, `ListPodSandbox`, `PodSandboxStatus`, `ListContainers`, and `ContainerStatus`; the
default route returns `403`. Request sizes, streams, connections, and operation times are bounded.
The shared proxy socket uses a memory-backed volume. Disabling automatic service-account mounting
and projecting the token only into the attester keeps both Kubernetes and CRI read authority out
of the proxy. All
host mounts are read-only except the bounded authenticated SQLite ledger directory. Operators must
pre-create `/var/lib/markweave-attester` as root-owned mode `0700`; the immutable authentication
key is projected root-owned mode `0400`. Certificate and inventory-key rotation require rendering
new immutable Secrets and rolling the DaemonSet while the fenced pool is unavailable to brokers.
The intended node-local mTLS transport uses TCP port `9443` without a cluster-wide Service. The broker derives the allowed endpoint as
`<attempt Pod spec.nodeName>:9443`; cluster DNS or the deployment network must resolve every
Kubernetes node name directly to that node's reachable address. The server certificate must cover
those node names, the mounted CA trusts only broker client certificates, and the broker verifies
both the server name, its configured exact leaf-certificate SHA-256, and the attested node identity.
The server likewise verifies its client CA and an exact configured broker leaf-certificate SHA-256.
The bounded protocol accepts only `bind`, `adopt_create_intent`, `recover_create_intent`,
`recover`, `confirm_exit`, `confirm_empty`, `confirm_removed`, and `acknowledge`; the server retains the bound sandbox identity and requires
the exact evidence it issued at each proof transition. Its bounded HMAC-authenticated SQLite
ledger persists `BOUND`, `EXIT`, `EMPTY`, and `REMOVED` before replying. It never expires or evicts
an unacknowledged record; capacity, corruption, deletion, or authentication failure closes the
service. A `REMOVED` record is deleted only after the broker reports its own durable worker-proof
acknowledgement, and both lost replies and repeated acknowledgements are idempotent. The protocol
never returns raw CRI output, cgroup contents, process identifiers, or Pod content. The immutable
ConfigMap enables mandatory client-certificate authentication and fixed
request/response/time ceilings, while the separate immutable Secret supplies the CA, certificate,
and private key. Deployments must render every `@REQUIRED_*@` placeholder without committing
private material. The broker deployment must mount the separate
`markweave-reverse-broker-attester-tls` Secret and use its client certificate, private key, and
attester-server CA only for this node-specific connection. `HttpsNodeAttesterClient` derives its
destination only from the scheduled Pod's API-bound `spec.nodeName`, and `AttesterHttpsServer`
provides a concurrency-limited TLS 1.3 service with bounded handshake, header, and body handling.
A production inspector must separately bound every synchronous CRI and cgroup operation.
Node-name routing, attester `hostPort`
support, firewall policy, certificate coverage, and failure behavior still require real-cluster
proof before this topology is supported.

The broker's `KubernetesApiControlPlane` uses the official Kubernetes 1.35 Python client under the
optional `markweave[kubernetes]` extra. It loads only the broker Pod's in-cluster identity and fixes
the namespace, attempt container, workspace paths, and exec commands in code. Pod creation waits a
configured bounded interval for API-bound node assignment. Every later workspace or lifecycle
operation rereads and matches the complete Pod identity; deletion carries the exact Pod UID
precondition. Kubernetes exec has no UID precondition, so its fixed helper first blocks, the broker
rechecks the API identity, and the helper checks a kubelet-projected Pod UID before accepting data.
Workspace exec transfers canonical attempt-channel files with bounded base64 framing,
and the pinned websocket client receives the exact broker timeout during connection establishment
as well as stream polling. This bounded connection path intentionally mirrors private `WSClient`
initialization from the pinned `kubernetes==35.0.0` dependency; upgrades must pass the compatibility
test before changing that pin. The kill exec acknowledgement is never considered termination evidence.

The committed `NodeAttestationEngine` is the fail-closed policy core. The production process uses
the official read-only Kubernetes client, fixed bounded `crictl` commands, and bounded reads of the
local cgroup v2 and `/proc` filesystems. Raw CRI and kernel output remains inside the attester
process. Unknown fields, truncated CRI enumeration, lookup errors, identity
changes, or an unavailable attester reject readiness or proof. Only an explicit trusted-inspector
"sandbox not observable yet" result is retried, within a configured deadline; policy mismatches
fail immediately. The node fence and complete runnable sandbox are re-attested before staging, and
the node fence is revalidated at every proof transition.

## Dedicated pool contract

Every isolation node must have both exact labels and the matching taint shown in
`deploy/reverse-kubernetes.yaml.example`. The attester refuses a node unless all of these facts are
positive:

- the Node UID is unchanged and Ready;
- cgroup v2 is active;
- kubelet `podPidsLimit` equals the broker policy PID ceiling;
- kubelet's CPU CFS quota period equals the broker policy period;
- the node carries the expected pool/fence labels and dedicated taint; and
- the configured CNI file and executable have the exact rendered SHA-256 digests; and
- the sandbox kernel network namespace contains only `lo` and dummy `eth0`, only
  `127.0.0.1` and `192.0.2.1`, no main-table route, and no neighbor; and
- the observed, API-server-defaulted Pod projects to the exact canonical broker-owned contract.

The canonical Pod contract contains every field authored by the broker. Observation accepts only
the documented API fields `status`, server-owned identity/version metadata, assigned `nodeName`,
the service-account alias, default scheduler/priority values, and default container termination
message settings. Their types and fixed values are validated. Equivalent CPU and byte quantities
are normalized, and only the two exact standard automatically injected `NoExecute` tolerations are
dropped. Every other extra field or toleration—including security-context and mount options—fails
attestation, as do extra containers, init containers, ephemeral containers, volumes, mounts, or
changes to any broker-owned field. The observed Pod UID and node name must match the identities
bound by the API and node attester. The contract digest is carried in evidence; the full
policy-specification digest is an annotation, because a 64-character digest is not a valid
Kubernetes label value.

The committed isolated CNI is mandatory. Kubernetes `NetworkPolicy` alone is not accepted as proof
because implementations can exempt node traffic. The CNI creates one unpeered dummy `eth0`, assigns
the documentation-only `192.0.2.1/32` address needed by standard containerd, disables IPv6 on that
interface and IPv4/IPv6 forwarding, flushes main-table routes and neighbors, then verifies those
facts before returning success. The attempt has no network-administration capability. At every
attestation transition, the inspector independently reads `/proc/<sandbox-pid>/net` and rejects any
extra interface, address, non-loopback IPv6 route, IPv4 route, or ARP neighbor. It also binds the
exact CNI configuration and executable digests into the node fence and evidence. Linux exposes the
target network's tables through `/proc/<pid>/net`, while a host reader of
`/proc/<pid>/root/proc/sys/net` observes the reader's network namespace; the attester therefore does
not claim a false direct sysctl observation. Forwarding is instead disabled and checked by the
digest-attested CNI at namespace creation, and the kernel-attested absence of a peer, route, and
neighbor leaves no forwarding or egress path. The dedicated nodes must run no general workload.
Changing kubelet, CRI, cgroup, runtime-handler, CNI, image-cache, kernel, or attester configuration
requires a new fence revision, draining the old pool, real-cluster validation, and then relabeling.
The broker fails closed while no positively attested node matches.

For k3s, render `deploy/k3s/reverse-node-config.yaml.example` into the dedicated agent's
`/etc/rancher/k3s/config.yaml.d/` directory. Render
`deploy/k3s/10-markweave-reverse-kubelet.conf.example` as
`/var/lib/rancher/k3s/agent/etc/kubelet.conf.d/10-markweave-reverse.conf`; the attester reads this
effective kubelet drop-in instead of trusting launch arguments. Install
`deploy/k3s/20-markweave-reverse-runtime.toml` as
`/var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.d/20-markweave-reverse.toml`.
Install the executable `deploy/k3s/markweave-runc-wrapper` as
`/var/lib/rancher/k3s/data/markweave/markweave-runc-wrapper`, mode `0755`. The dedicated node must
provide `/usr/bin/runc` and `/usr/bin/jq`; the wrapper uses fixed absolute paths and fails closed if
either executable is unavailable. It delegates non-create operations unchanged. For CRI create it
allows an unchanged pause sandbox with no `/work`, or exactly one workload bind mount sourced from
the kubelet Pod UID `emptyDir` path with the containerd-generated `rbind,rprivate,rw` options. It
atomically adds only `nodev`, `noexec`, and `nosuid` before invoking runc and rejects every other
mount shape.
Install `deploy/k3s/00-markweave-isolated.conflist` as
`/var/lib/rancher/k3s/agent/etc/cni/net.d/00-markweave-isolated.conflist` and install the executable
`deploy/k3s/markweave-isolated` as `/var/lib/rancher/k3s/data/cni/markweave-isolated`, mode `0755`.
The `00-` prefix is security-significant: containerd selects the lexicographically first CNI
configuration, and k3s regenerates its later `10-flannel.conflist`. Render the CNI configuration,
CNI executable, runtime handler, and runtime-wrapper digests into the attester configuration.
Restart the dedicated agent only while its fence is unavailable, then verify the generated kubelet
configuration, containerd handler, labels, taint, and actual sandbox network before making the new
fence revision schedulable. RuntimeClass selection alone does not select a CNI, so the dedicated
node, first CNI configuration, immutable asset digests, and the attester's positive kernel network
observation are all mandatory. A disposable k3s probe confirmed that standard containerd rejects a
loopback-only result and accepts this isolated dummy-interface result as Pod IP `192.0.2.1`.

## Attempt policy

Each attempt is one UID-bound Pod with a digest-only image, fixed Python module entrypoint, empty
arguments, arbitrary non-root UID/GID, read-only root, all capabilities dropped, runtime-default
seccomp, no privilege escalation, no host namespaces, no service links, no service-account token,
and no restart. CPU and memory requests equal limits. `/work` is a memory-backed `emptyDir` with the
exact workspace size limit; Pod-level `fsGroup` ownership makes it writable by the arbitrary
non-root attempt identity, and the same value is the ephemeral-storage request and limit. The Pod
active deadline is the rounded-up broker deadline. CPU quota/period values that cannot be
represented exactly in Kubernetes millicores are rejected.

Because Kubernetes charges memory-backed `emptyDir` pages to the container memory limit, the
Kubernetes runtime configuration includes a positive `interpreter_memory_margin_bytes`. It rejects
any policy where `memory_bytes - workspace_bytes` is smaller than that configured margin. Operators
must size the margin from exact-image measurements and validate OOM and workspace exhaustion
separately on the dedicated pool; the reference tests use 64 MiB and do not make that value a
portable production default.

Readiness is based on observations from the sandbox and kernel, not the requested Pod resources.
The attester reads and binds the sandbox cgroup's actual `cpu.max` quota and period, `memory.max`,
and `pids.max`, plus the `/work` mount filesystem, path, size and exact `rw,nodev,noexec,nosuid`
flags. Any mismatch with policy rejects the sandbox before request staging.

Workspace transfer uses the exact attempt Pod identity and bounded T70 request/response channel.
Document bytes never enter labels, inventory, evidence, diagnostics, or attester messages.

## Termination proof and recovery

Pod phase, deletion acknowledgement, force deletion, and API absence are supplementary facts only.
The node attester must first completely enumerate the exact CRI sandbox and prove every bound
container exited. It then either observes the exact previously bound cgroup as unpopulated with
zero descendants or completes a negative lookup of that exact cgroup on the unchanged fenced node. The
latter is accepted as kernel evidence that the cgroup became empty before containerd's automatic
removal; an incomplete lookup, an unbound path, or absence without prior CRI exit evidence fails
closed. Only after this positive evidence may the broker delete the exact Pod UID. Removal requires
complete CRI enumeration proving the sandbox is gone, a complete cgroup lookup proving the
previously emptied cgroup is gone, and bounded Pod API absence. The final evidence binds all
identities and the prior emptiness digest.

Before the first Pod API mutation, the broker inventory persists a bounded, versioned,
content-free prepared binding on `CREATE_INTENT`. It fixes the unit identity, creation-time policy,
runtime configuration, image repository, and Pod-contract digest. After attestation, the same
authenticated row is atomically replaced with the exact Pod, sandbox, node fence, and complete
container ID set. The authenticated v2-to-v3 migration verifies every row and manifest before an
atomic schema rewrite and re-MAC. Recovery validates those facts against the durable unit, so a
lost API/bind reply or current-policy rollover cannot reinterpret an existing unit.
It performs exact Pod lookup rather than guessing a node or trusting labels, and it separately
recovers a present `EXITED` unit without trying to recreate a running binding. If a create reply
was lost before the broker persisted `CREATED`, `recover_create_intent` returns only the original
authenticated ledger contract and sandbox for the exact API-bound Pod; it does not reinterpret the
Pod under the current policy. A failed kill against an already exited container is accepted only
when the attester independently returns positive exit evidence.

Broker and attester restart reconciliation replays the exact durable lifecycle. A worker proof ACK
first becomes a durable broker marker, then releases the exact attester `REMOVED` record, then
discards the broker tombstone. A crash between any of those steps resumes the same cleanup on
startup. There is no pre-ACK TTL. Failure at any stage keeps the broker unavailable and retains the
authenticated state needed for retry.

## Required deployment proof

Do not claim this backend supported after rendering the example YAML. Acceptance requires a real
dedicated cluster run against the exact broker, attester, and reverse-attempt image digests. The run
must cover successful conversion, restart reconciliation, lost create/delete replies, deadline,
cancellation, OOM, PID exhaustion, workspace exhaustion, attempted credential access, every egress
class including node-local destinations, Pod substitution, node relabeling, attester outage,
incomplete CRI enumeration, descendant escape attempts, and proof retention/acknowledgement.

Authorized disposable probes on `codex-dev` k3s `v1.35.5+k3s1`, containerd `2.2.3-k3s1`, and
cgroup v2 established the actual automatic cgroup-removal ordering used by the bounded
absent-cgroup rule, validated the exact-method Envoy CRI proxy against live containerd (read RPCs
succeeded and a mutating `StopPodSandbox` was denied), and ran a Pod through the committed CNI with
the accepted Pod IP `192.0.2.1`. Kernel inspection of that live sandbox observed only `lo` and
`eth0`, only `127.0.0.1` and `192.0.2.1`, no IPv4 route, no non-loopback IPv6 route, and no ARP
neighbor. Every probe namespace and host asset was removed and k3s was restored to its initial
inactive/disabled state. This single general-purpose node is not the required dedicated fenced
pool. The attester image now builds and passes minimal-import and closed-failure smoke probes. The
exact broker/attester/attempt image deployment and complete acceptance matrix are still pending.
These gates remain required but unexecuted; unit, loopback-mTLS, fake-control-plane, or general-node
results are not substitutes for that evidence.

The same concrete-inspector probe exposed a containment gap on Kubernetes 1.35:
`emptyDir.medium: Memory` produced a `/work` tmpfs mounted as `rw,relatime` with no `nodev`, `noexec`,
or `nosuid`. The inspector reports the observed flags and the policy engine correctly rejects that
sandbox. The Pod API has no `emptyDir` mount-options field in this version; StorageClass mount
options apply to dynamically provisioned persistent volumes, not `emptyDir`. The selected dedicated
OCI runtime wrapper closes the configuration gap at runc create time and is itself part of the node
fence. A native OCI prestart/createContainer hook was rejected during disposable testing because
the kubelet bind mount was not present at the hook's execution point. A subsequent real-k3s probe
ran the workload with exact `nodev,noexec,nosuid,rw` flags, rejected a workload missing `/work`
before runc, and proved that modified wrapper bytes invalidate the fence. The full dedicated-pool,
published-digest, broker/attester/attempt exact-image acceptance matrix remains required; the
backend remains unsupported until that proof passes.
