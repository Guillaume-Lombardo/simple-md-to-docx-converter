#!/usr/bin/env bash
set -euo pipefail
umask 0077

repository="$(pwd)"
readonly repository
readonly compose_file="$repository/compose.yaml"
readonly overlay_file="$repository/compose.simple.yaml"
readonly podman_overlay_file="$repository/compose.podman.yaml"
readonly quickstart_script="$repository/scripts/quickstart-simple.sh"
readonly suffix="${GITHUB_RUN_ID:-local}-$$-$RANDOM"
readonly project="markweave-simple-e2e-${suffix,,}"
readonly work_volume="${project}_markweave-work"
readonly data_volume="${project}_markweave-data"
readonly signatures_volume="${project}_clamav-signatures"
readonly runtime="${MARKWEAVE_SIMPLE_E2E_RUNTIME:-docker}"
runtime_service_pid=""
runtime_socket=""

case "$runtime" in
  docker)
    runtime_command=(docker)
    compose_command=(docker compose)
    expected_volume_options=null
    ;;
  podman)
    runtime_command=(podman)
    compose_command=()
    expected_volume_options='{}'
    ;;
  *)
    echo "MARKWEAVE_SIMPLE_E2E_RUNTIME must be docker or podman." >&2
    exit 2
    ;;
esac

if [[ ! -f "$compose_file" || ! -f "$overlay_file" || \
  ! -f "$podman_overlay_file" || ! -x "$quickstart_script" ]]; then
  echo "Run this command from the repository root." >&2
  exit 2
fi

temporary_directory="$(mktemp -d)"
if [[ "$runtime" == podman ]]; then
  runtime_socket="$temporary_directory/podman-compose.sock"
  compose_command=(
    env "DOCKER_HOST=unix://$runtime_socket"
    podman --url "unix://$runtime_socket" compose
  )
fi
state_home="$temporary_directory/state"
state_directory="$state_home/markweave-quickstart-simple"
template_file="$state_directory/quickstart-template.docx"
state_file="$temporary_directory/checkpoint.json"
artifact_directory="$temporary_directory/artifacts"
fault_env="$temporary_directory/fault-compose.env"
port_ready="$temporary_directory/port-blocker.ready"
port_blocker_pid=""
password=""
port="$(uv run python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
readonly public_endpoint="http://127.0.0.1:$port"
readonly public_host="localhost:$port"
readonly public_base_url="http://$public_host"
succeeded=false

quickstart_command=(
  env
  "XDG_STATE_HOME=$state_home"
  "MARKWEAVE_SIMPLE_PROJECT=$project"
  "MARKWEAVE_SIMPLE_PORT=$port"
  "MARKWEAVE_SIMPLE_RUNTIME=$runtime"
  "$quickstart_script"
)

quickstart() {
  "${quickstart_command[@]}" "$@"
}

write_fault_env() {
  printf 'MARKWEAVE_INITIAL_ADMIN_PASSWORD=%s\nMARKWEAVE_PORT=%s\nMARKWEAVE_PUBLIC_ORIGIN=http://localhost:%s\nMARKWEAVE_WORK_DEVICE=/dev/null\n' \
    "$password" "$port" "$port" >"$fault_env"
}

compose() {
  local files=(--file "$compose_file" --file "$overlay_file")
  if [[ "$runtime" == podman ]]; then
    files+=(--file "$podman_overlay_file")
  fi
  "${compose_command[@]}" --project-name "$project" --project-directory "$repository" \
    "${files[@]}" --env-file "$fault_env" "$@"
}

file_sha256() {
  sha256sum -- "$1" | awk '{print $1}'
}

cleanup() {
  local exit_code=$?
  if [[ -n "$port_blocker_pid" ]] && kill -0 "$port_blocker_pid" 2>/dev/null; then
    kill "$port_blocker_pid" || exit_code=1
    wait "$port_blocker_pid" 2>/dev/null || true
  fi
  if [[ "$succeeded" != true && -f "$fault_env" ]]; then
    compose logs --no-color >&2 2>/dev/null || true
  fi
  if [[ "$succeeded" != true ]]; then
    quickstart down >/dev/null 2>&1 || true
    password="$(quickstart password 2>/dev/null)" || true
    write_fault_env
    compose down --volumes --remove-orphans --timeout 30 >/dev/null 2>&1 || true
  fi
  for volume in "$work_volume" "$data_volume" "$signatures_volume"; do
    "${runtime_command[@]}" volume rm "$volume" >/dev/null 2>&1 || true
  done
  if [[ -n "$runtime_service_pid" ]] && kill -0 "$runtime_service_pid" 2>/dev/null; then
    kill "$runtime_service_pid" || exit_code=1
    wait "$runtime_service_pid" 2>/dev/null || true
  fi
  if [[ "$temporary_directory" == /tmp/tmp.* ]]; then
    rm -rf -- "$temporary_directory"
  else
    echo "Preserving unexpected simple-E2E directory: $temporary_directory" >&2
    exit_code=1
  fi
  exit "$exit_code"
}
trap cleanup EXIT

if [[ "$runtime" == podman ]]; then
  podman system service --time=0 "unix://$runtime_socket" >/dev/null 2>&1 &
  runtime_service_pid=$!
  for _ in $(seq 1 100); do
    [[ -S "$runtime_socket" ]] && break
    kill -0 "$runtime_service_pid"
    sleep 0.1
  done
  [[ -S "$runtime_socket" ]] || exit 2
fi

wait_for_services() {
  local application_id
  local canonical_status
  local rejected_status
  local scanner_id
  for _ in $(seq 1 240); do
    canonical_status=""
    application_id="$(compose ps -q markweave)"
    scanner_id="$(compose ps -q clamav)"
    if [[ -n "$application_id" && -n "$scanner_id" ]]; then
      if [[ "$runtime" == podman ]] && \
        "${runtime_command[@]}" exec "$scanner_id" /usr/local/bin/clamdcheck.sh \
          >/dev/null 2>&1; then
        canonical_status="$(curl --silent --output /dev/null --write-out '%{http_code}' \
          --header "Host: $public_host" "$public_endpoint/health/ready" || true)"
      elif [[ "$runtime" == docker ]] && \
        [[ "$("${runtime_command[@]}" inspect --format '{{.State.Health.Status}}' "$application_id" 2>/dev/null)" == healthy ]] && \
        [[ "$("${runtime_command[@]}" inspect --format '{{.State.Health.Status}}' "$scanner_id" 2>/dev/null)" == healthy ]]; then
        canonical_status="$(curl --silent --output /dev/null --write-out '%{http_code}' \
          --header "Host: $public_host" "$public_endpoint/health/ready" || true)"
      fi
      if [[ "$canonical_status" == 200 ]]; then
        rejected_status="$(curl --silent --output /dev/null --write-out '%{http_code}' \
          "$public_endpoint/health/ready" || true)"
        [[ "$rejected_status" == 421 ]] || {
          echo "The production router did not reject the non-canonical Host with 421." >&2
          return 1
        }
        return 0
      fi
    fi
    for container in "$application_id" "$scanner_id"; do
      if [[ -n "$container" && "$("${runtime_command[@]}" inspect --format '{{.State.Status}}' "$container" 2>/dev/null)" == exited ]]; then
        echo "A simple-Compose service exited before becoming healthy." >&2
        return 1
      fi
    done
    sleep 5
  done
  echo "Timed out waiting for the simple-Compose services." >&2
  return 1
}

verify_runtime_boundary() {
  local application_id
  local router_id
  local scanner_id
  local security_options
  application_id="$(compose ps -q markweave)"
  router_id="$(compose ps -q router)"
  scanner_id="$(compose ps -q clamav)"

  [[ -n "$application_id" && -n "$router_id" && -n "$scanner_id" ]]
  test -z "$("${runtime_command[@]}" port "$application_id")"
  test "$("${runtime_command[@]}" port "$router_id" 8080/tcp)" = "127.0.0.1:$port"
  test -z "$("${runtime_command[@]}" port "$scanner_id")"
  test "$("${runtime_command[@]}" inspect --format '{{.Config.User}}' "$application_id")" = "1001:0"
  test "$("${runtime_command[@]}" inspect --format '{{.HostConfig.ReadonlyRootfs}}' "$application_id")" = true
  if [[ "$runtime" == docker ]]; then
    test "$("${runtime_command[@]}" inspect --format '{{json .HostConfig.CapDrop}}' "$application_id")" = '["ALL"]'
  else
    "${runtime_command[@]}" exec "$application_id" python -c \
      'from pathlib import Path
values = {line.split(":", 1)[0]: line.split()[1] for line in Path("/proc/1/status").read_text().splitlines() if line.startswith("Cap")}
assert all(int(values[name], 16) == 0 for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"))'
  fi
  security_options="$("${runtime_command[@]}" inspect --format '{{json .HostConfig.SecurityOpt}}' "$application_id")"
  [[ "$security_options" == *no-new-privileges* ]]
  [[ "$security_options" != *unconfined* ]]
  "${runtime_command[@]}" exec "$application_id" sh -c \
    "grep -Eq '^Seccomp:[[:space:]]+2$' /proc/1/status"
  "${runtime_command[@]}" exec "$application_id" python -c \
    'import socket; s=socket.create_connection(("clamav", 3310), 5); s.sendall(b"zPING\0"); assert s.recv(64)==b"PONG\0"; s.close()'
  "${runtime_command[@]}" exec "$application_id" python -c \
    'import socket
try:
    socket.create_connection(("1.1.1.1", 443), 2)
except OSError:
    raise SystemExit(0)
raise SystemExit("application unexpectedly reached the Internet")'
}

verify_simple_work_volume() {
  test "$("${runtime_command[@]}" volume inspect --format '{{ index .Labels "com.docker.compose.project" }}' "$work_volume")" = "$project"
  test "$("${runtime_command[@]}" volume inspect --format '{{ index .Labels "com.docker.compose.volume" }}' "$work_volume")" = markweave-work
  test "$("${runtime_command[@]}" volume inspect --format '{{.Driver}}' "$work_volume")" = local
  test "$("${runtime_command[@]}" volume inspect --format '{{json .Options}}' "$work_volume")" = "$expected_volume_options"
}

verify_helper_service_stopped() {
  [[ "$runtime" == podman ]] || return 0
  test ! -e "$state_directory/podman-compose.sock"
  if pgrep -f \
    "[p]odman system service --time=0 unix://$state_directory/podman-compose.sock" \
    >/dev/null; then
    echo "The public helper retained its private Podman API service." >&2
    return 1
  fi
}

write_checkpoint() {
  MARKWEAVE_E2E_ADMIN_USERNAME=admin \
  MARKWEAVE_E2E_ADMIN_PASSWORD="$password" \
    uv run python -m tests.e2e.service_workflow checkpoint \
      --base-url "$public_base_url" \
      --profile standalone \
      --template "$template_file" \
      --state-file "$state_file" \
      --artifact-dir "$artifact_directory" \
      --output both
}

verify_podman_mermaid() {
  [[ "$runtime" == podman ]] || return 0
  MARKWEAVE_E2E_ADMIN_USERNAME=admin \
  MARKWEAVE_E2E_ADMIN_PASSWORD="$password" \
    uv run python -m tests.e2e.service_workflow exercise-mermaid \
      --base-url "$public_base_url" \
      --profile standalone \
      --template "$template_file" \
      --artifact-dir "$artifact_directory"
}

verify_login_origin() {
  uv run python -m tests.e2e.service_workflow verify-login-origin \
    --base-url "$public_base_url" \
    --login-origin "http://localhost:$port" \
    --profile standalone \
    --artifact-dir "$artifact_directory"
}

verify_checkpoint() {
  MARKWEAVE_E2E_ADMIN_USERNAME=admin \
  MARKWEAVE_E2E_ADMIN_PASSWORD="$password" \
    uv run python -m tests.e2e.service_workflow verify-checkpoint \
      --base-url "$public_base_url" \
      --profile standalone \
      --state-file "$state_file" \
      --artifact-dir "$artifact_directory"
}

mkdir -p -- "$artifact_directory"

quickstart up
verify_helper_service_stopped
verify_login_origin
password="$(quickstart password)"
write_fault_env
compose config --quiet
wait_for_services
verify_runtime_boundary
verify_simple_work_volume
application_id="$(compose ps -q markweave)"
"${runtime_command[@]}" exec "$application_id" sh -c 'printf preserved > /work/simple-rerun-marker'

# A repeated up against the healthy stack retains the active disposable workspace.
quickstart up
verify_helper_service_stopped
wait_for_services
"${runtime_command[@]}" exec "$application_id" test -f /work/simple-rerun-marker
write_checkpoint
verify_podman_mermaid

# A stopped restart recreates only scratch and recovers durable state from /data.
compose stop --timeout 30
quickstart up
verify_helper_service_stopped
password="$(quickstart password)"
write_fault_env
wait_for_services
verify_runtime_boundary
verify_simple_work_volume
application_id="$(compose ps -q markweave)"
if "${runtime_command[@]}" exec "$application_id" test -e /work/simple-rerun-marker; then
  echo "The simple quickstart retained disposable work across a stopped restart." >&2
  exit 1
fi
verify_checkpoint

# A real port collision must roll back partial scratch while preserving durable state.
password_sha256="$(file_sha256 "$state_directory/password.env")"
template_sha256="$(file_sha256 "$template_file")"
quickstart down
verify_helper_service_stopped
"${runtime_command[@]}" volume inspect "$data_volume" >/dev/null
"${runtime_command[@]}" volume inspect "$signatures_volume" >/dev/null
uv run python -c '
import socket
import sys
from pathlib import Path

port = int(sys.argv[1])
ready = Path(sys.argv[2])
with socket.socket() as listener:
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen()
    ready.write_text("ready", encoding="utf-8")
    listener.accept()
' "$port" "$port_ready" &
port_blocker_pid=$!
for _ in $(seq 1 100); do
  [[ -f "$port_ready" ]] && break
  kill -0 "$port_blocker_pid"
  sleep 0.1
done
test -f "$port_ready"
if quickstart up >"$temporary_directory/expected-up-failure.log" 2>&1; then
  echo "Simple quickstart unexpectedly started on an occupied port." >&2
  exit 1
fi
verify_helper_service_stopped
kill -0 "$port_blocker_pid"
test -z "$("${runtime_command[@]}" container ls --all --quiet --filter "label=com.docker.compose.project=$project")"
if "${runtime_command[@]}" volume inspect "$work_volume" >/dev/null 2>&1; then
  echo "Failed simple startup retained its disposable work volume." >&2
  exit 1
fi
test "$(file_sha256 "$state_directory/password.env")" = "$password_sha256"
test "$(file_sha256 "$template_file")" = "$template_sha256"
"${runtime_command[@]}" volume inspect "$data_volume" >/dev/null
"${runtime_command[@]}" volume inspect "$signatures_volume" >/dev/null
kill "$port_blocker_pid"
wait "$port_blocker_pid" 2>/dev/null || true
port_blocker_pid=""

quickstart up
verify_helper_service_stopped
password="$(quickstart password)"
write_fault_env
wait_for_services
verify_checkpoint
quickstart down
verify_helper_service_stopped
if "${runtime_command[@]}" volume inspect "$work_volume" >/dev/null 2>&1; then
  echo "The simple work volume survived its supported down command." >&2
  exit 1
fi
"${runtime_command[@]}" volume inspect "$data_volume" >/dev/null
"${runtime_command[@]}" volume inspect "$signatures_volume" >/dev/null

succeeded=true
