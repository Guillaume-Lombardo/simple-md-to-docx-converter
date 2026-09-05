# Rootless Podman isolation broker service

The reverse-conversion isolation broker is a host-native process. Run it as a dedicated unprivileged
user through the reviewed [systemd user unit](../packaging/systemd/user/markweave-broker.service).
Do not run the broker in a container and do not give the application, worker, or attempt container
the Podman socket.

## Host prerequisites

The host must provide all of the following before the broker is enabled:

- cgroup v2 and a running systemd user manager for the broker account;
- rootless Podman using the systemd cgroup manager and local runtime authority;
- subordinate UID and GID ranges assigned to the broker account;
- lingering enabled for that account when the broker must start without an interactive login;
- the reviewed reverse-attempt image already present under the configured repository and exact
  immutable digest; and
- the `markweave-broker` executable installed exactly at `/usr/local/bin/markweave-broker`.

An administrator can enable lingering with `loginctl enable-linger <broker-account>`. Verify the
effective rootless runtime as the broker account with `podman info`; do not configure a remote
Podman endpoint, mount a Podman socket, or supply registry credentials to the service.

## Owner-only files

The broker configuration is fixed at
`~/.config/markweave-broker/broker.json`. Create its parent, the inventory state directory, the
Unix socket parent when selected, the empty hooks directory, and the mTLS material directory with
mode `0700`. They must be canonical, owned by the broker EUID, and distinct where the configuration
contract requires distinct identities. The configuration, inventory key, private CA, certificate
chain, and private key must be pre-existing regular files owned by that same EUID, have one hard
link, and use exactly mode `0400` or `0600`. The inventory key is exactly 32 random bytes encoded as
64 lowercase hexadecimal characters followed by one newline.

Render either the [schema-v1 Unix template](../packaging/broker/broker-unix-v1.json.in) or the
[schema-v2 mTLS template](../packaging/broker/broker-mtls-v2.json.in). Every `@REQUIRED_*@` token is
mandatory deployment input, including all T71-owned channel and runtime ceilings. The rendered file
must remain ASCII canonical JSON with its existing key ordering and final newline. The repository
templates intentionally provide no production budget, capacity, timeout, endpoint, identity, or
image value.

For mTLS, use a dedicated private CA and the exact client and broker SPIFFE URI identities. Configure
one current client leaf-certificate SHA-256 pin, or the reviewed current and next pins during a
bounded rotation. A secret store projection that produces root-owned or group-readable files does
not satisfy the process contract. Copy each secret into a temporary file on the destination
filesystem, set the broker EUID ownership and mode `0400` or `0600`, verify it is regular and
single-link, then atomically rename it into the owner-only material directory. Do not use `fsGroup`
or another group-readable relaxation.

Configuration, labels, inventory, and service diagnostics contain only content-free identities and
policy evidence. Never place document bytes, document names, content-derived hashes, registry
credentials, storage credentials, or publication credentials in these files or the journal.

## Service lifecycle

Install the unit as the broker account under `~/.config/systemd/user/markweave-broker.service`, or
link the reviewed file from another owner-controlled location, then run:

```text
systemctl --user daemon-reload
systemctl --user enable --now markweave-broker.service
```

The process validates all configuration and TLS material, acquires the per-EUID authority lock,
opens its protected inventory, and completes orphan reconciliation before it listens. An active
systemd unit alone is not broker readiness; the authenticated worker must receive a positive `READY`
response before creating an attempt.

The unit restarts unexpected runtime failures and uses `KillMode=control-group`. Configuration exit
status `2` is excluded from restart, so an invalid owner-only configuration cannot create a restart
loop. `TimeoutStopSec=infinity` prevents an ambient systemd manager default from replacing the
broker's explicit hard-shutdown watchdog. The unit adds no service-level runtime deadline or
workload budget: the hard broker shutdown deadline and every attempt ceiling remain required
deployment configuration. A normal stop drains admission and preserves the broker's
terminate-and-prove cleanup contract:

```text
systemctl --user stop markweave-broker.service
```

After a broker crash, systemd starts the same process and the broker sweeps any live inventoried
attempt before reopening its listener. If reconciliation or termination proof cannot be completed,
the broker remains unavailable and fails closed. Never delete the inventory or managed Podman
objects to force readiness.
