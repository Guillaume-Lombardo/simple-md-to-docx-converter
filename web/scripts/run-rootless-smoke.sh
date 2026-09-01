#!/usr/bin/env bash
set -euo pipefail

image="localhost/markweave-web:t60-smoke"
name="markweave-web-t60-smoke-$$"
cleanup() { podman rm --force "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT

podman build --format docker --tag "$image" --file web/Containerfile web
podman run --detach --name "$name" --user 10073:0 --read-only --cap-drop all \
  --security-opt no-new-privileges \
  --pids-limit 64 --memory 256m --cpus 0.5 --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m \
  --publish 127.0.0.1::3000 --publish 127.0.0.1::3001 "$image" >/dev/null

page_port="$(podman port "$name" 3000/tcp | awk -F: 'NR == 1 {print $NF}')"
probe_port="$(podman port "$name" 3001/tcp | awk -F: 'NR == 1 {print $NF}')"
for _ in $(seq 1 60); do
  if curl --fail --silent --output /dev/null "http://127.0.0.1:${probe_port}/_frontend/health/ready"; then break; fi
  sleep 1
done
curl --fail --silent --output /dev/null "http://127.0.0.1:${probe_port}/_frontend/health/live"
curl --fail --silent --output /dev/null "http://127.0.0.1:${probe_port}/_frontend/health/ready"
curl --fail --silent --output /dev/null "http://127.0.0.1:${page_port}/convert"
test "$(podman inspect --format '{{.Config.User}}' "$name")" = "10073:0"
test "$(podman inspect --format '{{.HostConfig.ReadonlyRootfs}}' "$name")" = "true"
