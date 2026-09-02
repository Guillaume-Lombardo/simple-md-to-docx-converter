#!/usr/bin/env bash
set -euo pipefail

image="${1:-localhost/markweave-web:t60-smoke}"
mode="${2:-build}"
if [[ "$mode" != build && "$mode" != --existing ]]; then
  echo "Usage: web/scripts/run-rootless-smoke.sh [IMAGE [--existing]]" >&2
  exit 2
fi
name="markweave-web-t60-smoke-$$"
router_name="$name-router"
network_name="$name-network"
cleanup() {
  podman rm --force "$router_name" "$name" >/dev/null 2>&1 || true
  podman network rm "$network_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [[ "$mode" == build ]]; then
  podman build --format docker --tag "$image" --file web/Containerfile web
else
  podman image exists "$image"
fi
podman network create "$network_name" >/dev/null
podman run --detach --name "$name" --network "$network_name" \
  --network-alias frontend --user 10073:0 --read-only --cap-drop all \
  --security-opt no-new-privileges \
  --pids-limit 64 --memory 256m --cpus 0.5 --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m \
  --publish 127.0.0.1::3001 "$image" >/dev/null

probe_port="$(podman port "$name" 3001/tcp | awk -F: 'NR == 1 {print $NF}')"
router_port="$((38000 + ($$ % 20000)))"
podman run --detach --name "$router_name" --network "$network_name" \
  --user 10173:0 --read-only --cap-drop all \
  --security-opt no-new-privileges --pids-limit 64 --memory 128m --cpus 0.5 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m \
  --publish "127.0.0.1:${router_port}:8080" \
  --env BACKEND_ORIGIN=http://frontend:3000 \
  --env FRONTEND_ORIGIN=http://frontend:3000 \
  --env PUBLIC_HOSTS="127.0.0.1:${router_port}" \
  --env ROUTER_REQUEST_MAX_BYTES=1100000 \
  "$image" node router.mjs >/dev/null
for _ in $(seq 1 50); do
  if curl --fail --silent --output /dev/null \
    "http://127.0.0.1:${router_port}/convert"; then break; fi
  if [[ "$(podman inspect "$router_name" --format '{{.State.Running}}')" != true ]]; then
    podman logs "$router_name" >&2
    exit 1
  fi
  sleep 0.1
done
for _ in $(seq 1 60); do
  if curl --fail --silent --output /dev/null "http://127.0.0.1:${probe_port}/_frontend/health/ready"; then break; fi
  sleep 1
done
curl --fail --silent --output /dev/null "http://127.0.0.1:${probe_port}/_frontend/health/live"
curl --fail --silent --output /dev/null "http://127.0.0.1:${probe_port}/_frontend/health/ready"
for _ in $(seq 1 30); do
  if curl --fail --silent --output /dev/null "http://127.0.0.1:${router_port}/convert"; then break; fi
  sleep 0.1
done
curl --fail --silent --output /dev/null "http://127.0.0.1:${router_port}/convert"
curl --fail --silent --output /dev/null "http://127.0.0.1:${router_port}/login"
curl --fail --silent --output /dev/null "http://127.0.0.1:${router_port}/change-password"
test -z "$(curl --silent --head "http://127.0.0.1:${router_port}/convert" | \
  grep --ignore-case '^x-powered-by:' || true)"
for path in \
  '/_frontend/health' \
  '/_frontend/health/live' \
  '/_FRONTEND/HEALTH/live' \
  '/%5ffrontend/health/live' \
  '//_frontend//health//ready' \
  '/x/../_frontend/health/ready'; do
  test "$(curl --silent --path-as-is --output - --write-out '%{http_code}' \
    "http://127.0.0.1:${router_port}${path}")" = "404"
done
test "$(podman inspect --format '{{.Config.User}}' "$name")" = "10073:0"
test "$(podman inspect --format '{{.HostConfig.ReadonlyRootfs}}' "$name")" = "true"
test "$(podman inspect --format '{{.Config.User}}' "$router_name")" = "10173:0"
test "$(podman inspect --format '{{.HostConfig.ReadonlyRootfs}}' "$router_name")" = "true"
podman exec "$name" test -r /opt/markweave-web/next.config.ts
podman exec "$router_name" test -r /opt/markweave-web/router.mjs
