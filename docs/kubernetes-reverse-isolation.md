# Optional Kubernetes reverse isolation

The Kubernetes backend is optional and does not replace the rootless Podman backend. It implements
the same `IsolationRuntime` port, so the T70 authenticated Unix and mTLS worker transports,
crash-consistent inventory, reconciliation, tombstones, and proof acknowledgement remain unchanged.

## Trust boundaries

The application and external worker possess only a broker client identity. They have no Kubernetes,
OCI, CRI, node, or workload-mutating credentials. The broker alone receives namespace-scoped Pod
authority in the attempt namespace. It authors the immutable Pod specification and uses an exact
Pod UID precondition for termination and deletion. The attempt service account has no RBAC and its
token is not mounted.

A separately deployed node attester, in a different namespace outside the broker Role and
`pods/exec` scope, is the only component that can query the CRI socket and stable cgroup. The
broker has no Node-reading ClusterRole. The attester service account token is disabled. Its mTLS
endpoint accepts only the broker identity and returns bounded, content-free evidence. The attester
never accepts a command, argv, image, path, PID, cgroup, sandbox, or node chosen independently by
the caller: it derives these identities from the Pod UID and verifies them against CRI metadata.

The reference DaemonSet exposes that mTLS endpoint through node-local TCP port `9443` using
`hostPort`, not a cluster-wide Service. The broker derives the only allowed endpoint as
`<attempt Pod spec.nodeName>:9443`; cluster DNS or the deployment network must resolve every
Kubernetes node name directly to that node's reachable address. The server certificate must cover
those node names, the mounted CA trusts only broker client certificates, and the broker verifies
both the server name and the attested node identity. The immutable ConfigMap enables mandatory
client-certificate authentication, while the separate immutable Secret supplies the CA,
certificate, and private key. Deployments must render every `@REQUIRED_*@` placeholder without
committing private material. The broker deployment must mount the separate
`markweave-reverse-broker-attester-tls` Secret and use its client certificate, private key, and
attester-server CA only for this node-specific connection.

The committed `NodeAttestationEngine` is the fail-closed policy core. A production adapter must
collect the corresponding facts from the local CRI and cgroup v2 filesystem and keep raw output
inside the attester process. Unknown fields, truncated CRI enumeration, lookup errors, identity
changes, or an unavailable attester reject readiness or proof.

## Dedicated pool contract

Every isolation node must have both exact labels and the matching taint shown in
`deploy/reverse-kubernetes.yaml.example`. The attester refuses a node unless all of these facts are
positive:

- the Node UID is unchanged and Ready;
- cgroup v2 is active;
- kubelet `podPidsLimit` equals the broker policy PID ceiling;
- kubelet's CPU CFS quota period equals the broker policy period;
- the node carries the expected pool/fence labels and dedicated taint; and
- the sandbox network namespace contains only `lo`, with forwarding disabled; and
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

The loopback-only CNI is mandatory. Kubernetes `NetworkPolicy` alone is not accepted as proof
because implementations can exempt node traffic. The dedicated nodes must run no general workload.
Changing kubelet, CRI, cgroup, runtime-handler, CNI, image-cache, kernel, or attester configuration
requires a new fence revision, draining the old pool, real-cluster validation, and then relabeling.
The broker fails closed while no positively attested node matches.

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
The node attester must first enumerate the exact CRI sandbox and prove every container exited. It
then proves the stable cgroup is unpopulated and contains zero descendants. Only after this positive
evidence may the broker delete the exact Pod UID. Removal requires complete CRI enumeration proving
the sandbox is gone, cgroup lookup proving the previously emptied cgroup is gone, and bounded Pod
API absence. The final evidence binds all identities and the prior emptiness digest.

The existing T70 inventory persists that proof as a tombstone. Startup and reconnect discovery
rebind every labelled Pod through the attester before reconciliation; labels alone never establish
identity. Failure at any stage keeps the broker unavailable and leaves the tombstone unacknowledged.

## Required deployment proof

Do not claim this backend supported after rendering the example YAML. Acceptance requires a real
dedicated cluster run against the exact broker, attester, and reverse-attempt image digests. The run
must cover successful conversion, restart reconciliation, lost create/delete replies, deadline,
cancellation, OOM, PID exhaustion, workspace exhaustion, attempted credential access, every egress
class including node-local destinations, Pod substitution, node relabeling, attester outage,
incomplete CRI enumeration, descendant escape attempts, and proof retention/acknowledgement.

No usable Kubernetes context was available in the development environment on 2026-09-08. The
cluster and exact-image gates therefore remain required but unexecuted; unit or fake-control-plane
results are not substitutes for that evidence.
