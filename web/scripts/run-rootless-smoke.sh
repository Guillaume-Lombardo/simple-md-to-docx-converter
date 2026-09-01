#!/usr/bin/env bash
set -euo pipefail

image="localhost/markweave-web:t60-smoke"
name="markweave-web-t60-smoke-$$"
router_pid=""
router_log="$(mktemp)"
cleanup() {
  if [[ -n "$router_pid" ]]; then kill "$router_pid" >/dev/null 2>&1 || true; fi
  podman rm --force "$name" >/dev/null 2>&1 || true
  rm --force -- "$router_log"
}
trap cleanup EXIT

podman build --format docker --tag "$image" --file web/Containerfile web
podman run --detach --name "$name" --user 10073:0 --read-only --cap-drop all \
  --security-opt no-new-privileges \
  --pids-limit 64 --memory 256m --cpus 0.5 --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m \
  --publish 127.0.0.1::3000 --publish 127.0.0.1::3001 "$image" >/dev/null

page_port="$(podman port "$name" 3000/tcp | awk -F: 'NR == 1 {print $NF}')"
probe_port="$(podman port "$name" 3001/tcp | awk -F: 'NR == 1 {print $NF}')"
router_port="$((38000 + ($$ % 20000)))"
PUBLIC_HOST="127.0.0.1:${router_port}" \
  FRONTEND_ORIGIN="http://127.0.0.1:${page_port}" \
  ROUTER_PORT="$router_port" node web/scripts/routing-fixture.mjs \
  >"$router_log" 2>&1 &
router_pid="$!"
for _ in $(seq 1 50); do
  if grep --quiet '^routing fixture listening' "$router_log"; then break; fi
  if ! kill -0 "$router_pid" >/dev/null 2>&1; then
    cat "$router_log" >&2
    exit 1
  fi
  sleep 0.1
done
grep --quiet '^routing fixture listening' "$router_log"
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
podman exec "$name" test -r /opt/markweave-web/next.config.ts
