#!/usr/bin/env bash
set -euo pipefail

repository="$(pwd)"
readonly repository
readonly compose_file="$repository/compose.yaml"
readonly template_source="$repository/examples/quickstart-template.docx.base64"
readonly suffix="${GITHUB_RUN_ID:-local}-$$-$RANDOM"
readonly project="markweave-e2e-${suffix,,}"

if [[ ! -f "$compose_file" || ! -f "$template_source" ]]; then
  echo "Run this command from the repository root." >&2
  exit 2
fi

temporary_directory="$(mktemp -d)"
env_file="$temporary_directory/compose.env"
template_file="$temporary_directory/quickstart-template.docx"
state_file="$temporary_directory/checkpoint.json"
artifact_directory="$temporary_directory/artifacts"
password="$(openssl rand -hex 24)"
port="$(uv run python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
compose=(docker compose --project-name "$project" --env-file "$env_file")
succeeded=false

cleanup() {
  local exit_code=$?
  if [[ "$succeeded" != true ]]; then
    "${compose[@]}" logs --no-color >&2 2>/dev/null || true
  fi
  "${compose[@]}" down --volumes --remove-orphans --timeout 30 >/dev/null 2>&1 || true
  if [[ "$temporary_directory" == /tmp/tmp.* ]]; then
    rm -rf -- "$temporary_directory"
  else
    echo "Refusing to remove unexpected temporary directory $temporary_directory." >&2
  fi
  exit "$exit_code"
}
trap cleanup EXIT

umask 0077
printf 'MARKWEAVE_INITIAL_ADMIN_PASSWORD=%s\nMARKWEAVE_PORT=%s\n' \
  "$password" "$port" >"$env_file"
openssl base64 -d -in "$template_source" -out "$template_file"
mkdir -p -- "$artifact_directory"

wait_for_services() {
  local application_id
  local scanner_id
  for _ in $(seq 1 240); do
    application_id="$("${compose[@]}" ps -q markweave)"
    scanner_id="$("${compose[@]}" ps -q clamav)"
    if [[ -n "$application_id" && -n "$scanner_id" ]] && \
      [[ "$(docker inspect --format '{{.State.Health.Status}}' "$application_id" 2>/dev/null)" == healthy ]] && \
      [[ "$(docker inspect --format '{{.State.Health.Status}}' "$scanner_id" 2>/dev/null)" == healthy ]] && \
      curl --fail --silent --show-error "http://127.0.0.1:$port/health/ready" >/dev/null; then
      return 0
    fi
    for container in "$application_id" "$scanner_id"; do
      if [[ -n "$container" && "$(docker inspect --format '{{.State.Status}}' "$container" 2>/dev/null)" == exited ]]; then
        echo "A Compose service exited before becoming healthy." >&2
        return 1
      fi
    done
    sleep 5
  done
  echo "Timed out waiting for the Compose services." >&2
  return 1
}

verify_runtime_boundary() {
  local application_id
  local scanner_id
  application_id="$("${compose[@]}" ps -q markweave)"
  scanner_id="$("${compose[@]}" ps -q clamav)"

  test "$(docker port "$application_id" 8080/tcp)" = "127.0.0.1:$port"
  test -z "$(docker port "$scanner_id")"
  docker exec "$application_id" python -c \
    'import socket; s=socket.create_connection(("clamav", 3310), 5); s.sendall(b"zPING\0"); assert s.recv(64)==b"PONG\0"; s.close()'
  docker exec "$application_id" python -c \
    'import socket
try:
    socket.create_connection(("1.1.1.1", 443), 2)
except OSError:
    raise SystemExit(0)
raise SystemExit("application unexpectedly reached the Internet")'
}

write_checkpoint() {
  MD_CONVERTER_E2E_ADMIN_USERNAME=admin \
  MD_CONVERTER_E2E_ADMIN_PASSWORD="$password" \
    uv run python -m tests.e2e.service_workflow checkpoint \
      --base-url "http://127.0.0.1:$port" \
      --profile standalone \
      --template "$template_file" \
      --state-file "$state_file" \
      --artifact-dir "$artifact_directory" \
      --output both
}

verify_checkpoint() {
  MD_CONVERTER_E2E_ADMIN_USERNAME=admin \
  MD_CONVERTER_E2E_ADMIN_PASSWORD="$password" \
    uv run python -m tests.e2e.service_workflow verify-checkpoint \
      --base-url "http://127.0.0.1:$port" \
      --profile standalone \
      --state-file "$state_file" \
      --artifact-dir "$artifact_directory"
}

remove_work_volume() {
  local volume="${project}_markweave-work"
  test "$(docker volume inspect --format '{{ index .Labels "com.docker.compose.project" }}' "$volume")" = "$project"
  test "$(docker volume inspect --format '{{ index .Labels "com.docker.compose.volume" }}' "$volume")" = markweave-work
  docker volume rm "$volume" >/dev/null
}

"${compose[@]}" config --quiet
"${compose[@]}" up --detach
wait_for_services
verify_runtime_boundary
write_checkpoint

# Retain durable volumes, remove only labeled scratch, then recreate containers and networks.
"${compose[@]}" down --remove-orphans --timeout 30
remove_work_volume
"${compose[@]}" up --detach
wait_for_services
verify_runtime_boundary
verify_checkpoint

succeeded=true
