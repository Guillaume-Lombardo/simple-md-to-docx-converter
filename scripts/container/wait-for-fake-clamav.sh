#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 4 ]]; then
  echo "Usage: wait-for-fake-clamav.sh SCANNER LABEL [PROBE_CONTAINER PROBE_HOST]" >&2
  exit 2
fi

readonly container_name="${1:?scanner container name is required}"
readonly evidence_label="${2:?scanner evidence label is required}"
readonly probe_container="${3:-$container_name}"
readonly probe_host="${4:-127.0.0.1}"

for _ in $(seq 1 60); do
  if podman exec "$probe_container" /opt/md-converter/venv/bin/python -c '
import socket
import sys

with socket.create_connection((sys.argv[1], 3310), timeout=1) as scanner:
    scanner.settimeout(1)
    scanner.sendall(b"zINSTREAM\0\0\0\0\0")
    expected = b"stream: OK\0"
    response = b""
    while len(response) < len(expected):
        chunk = scanner.recv(len(expected) - len(response))
        if not chunk:
            break
        response += chunk
    assert response == expected
' "$probe_host" >/dev/null 2>&1; then
    if [[ -n "${MARKWEAVE_CONTAINER_EVIDENCE_DIRECTORY:-}" ]]; then
      mkdir -p "$MARKWEAVE_CONTAINER_EVIDENCE_DIRECTORY"
      printf '%s scanner accepted a framed empty INSTREAM request.\n' \
        "$evidence_label" >> \
        "$MARKWEAVE_CONTAINER_EVIDENCE_DIRECTORY/scanner-readiness.txt"
    fi
    exit 0
  fi
  if ! podman container exists "$container_name" || \
     [[ "$(podman inspect "$container_name" --format '{{.State.Running}}')" != true ]] || \
     ! podman container exists "$probe_container" || \
     [[ "$(podman inspect "$probe_container" --format '{{.State.Running}}')" != true ]]; then
    break
  fi
  sleep 0.25
done

echo "$evidence_label scanner did not become ready within 15 seconds." >&2
if podman container exists "$container_name"; then
  podman inspect "$container_name" \
    --format 'scanner running={{.State.Running}} exit_code={{.State.ExitCode}}' >&2
  podman logs --tail 50 "$container_name" >&2 || true
else
  echo "Scanner container $container_name no longer exists." >&2
fi
if [[ "$probe_container" != "$container_name" ]]; then
  if podman container exists "$probe_container"; then
    podman inspect "$probe_container" \
      --format 'probe running={{.State.Running}} exit_code={{.State.ExitCode}}' >&2
    podman logs --tail 50 "$probe_container" >&2 || true
  else
    echo "Probe container $probe_container no longer exists." >&2
  fi
fi
exit 1
