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
work_image="$temporary_directory/work.ext4"
work_device=""
initialization_mount=""
password="$(openssl rand -hex 24)"
port="$(uv run python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
compose=(docker compose --project-name "$project" --env-file "$env_file")
succeeded=false

cleanup() {
  local exit_code=$?
  if [[ -n "$initialization_mount" && -d "$initialization_mount" ]]; then
    if mountpoint -q "$initialization_mount"; then
      sudo umount "$initialization_mount" || exit_code=1
    fi
    rmdir -- "$initialization_mount" 2>/dev/null || true
  fi
  if [[ "$succeeded" != true ]]; then
    "${compose[@]}" logs --no-color >&2 2>/dev/null || true
  fi
  "${compose[@]}" down --volumes --remove-orphans --timeout 30 >/dev/null 2>&1 || true
  if [[ -n "$work_device" ]]; then
    if [[ "$(sudo /usr/sbin/losetup --noheadings --raw --output BACK-FILE "$work_device" 2>/dev/null)" == "$work_image" ]]; then
      sudo /usr/sbin/losetup --detach "$work_device" || exit_code=1
    else
      echo "Refusing to detach an unexpected E2E loop device." >&2
      exit_code=1
    fi
  fi
  if [[ "$temporary_directory" == /tmp/tmp.* ]]; then
    if [[ -z "$work_device" ]] || ! sudo /usr/sbin/losetup "$work_device" >/dev/null 2>&1; then
      rm -rf -- "$temporary_directory"
    else
      echo "Preserving the temporary directory for an attached loop device." >&2
      exit_code=1
    fi
  else
    echo "Refusing to remove unexpected temporary directory $temporary_directory." >&2
  fi
  exit "$exit_code"
}
trap cleanup EXIT

umask 0077
truncate -s 335544320 "$work_image"
/usr/sbin/mkfs.ext4 -q -F -m 0 -O '^has_journal' "$work_image"
work_device="$(sudo /usr/sbin/losetup --find --show "$work_image")"
initialization_mount="$(mktemp -d "$temporary_directory/mount.XXXXXX")"
sudo mount -t ext4 -o rw,nosuid,nodev "$work_device" "$initialization_mount"
sudo chown 1001:0 "$initialization_mount"
sudo chmod 0770 "$initialization_mount"
sudo umount "$initialization_mount"
rmdir -- "$initialization_mount"
initialization_mount=""
printf 'MARKWEAVE_INITIAL_ADMIN_PASSWORD=%s\nMARKWEAVE_PORT=%s\nMARKWEAVE_WORK_DEVICE=%s\n' \
  "$password" "$port" "$work_device" >"$env_file"
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

verify_work_capacity() {
  local application_id
  application_id="$("${compose[@]}" ps -q markweave)"
  docker exec "$application_id" python -c '
import errno
import os
from pathlib import Path

root = Path("/work")
stats = os.statvfs(root)
capacity = stats.f_blocks * stats.f_frsize
assert 268_435_456 <= capacity <= 335_544_320, capacity
path = root / "compose-e2e-enospc"
block = b"x" * 1_048_576
written = 0
try:
    with path.open("wb", buffering=0) as stream:
        while True:
            stream.write(block)
            written += len(block)
except OSError as error:
    assert error.errno == errno.ENOSPC, error
finally:
    path.unlink(missing_ok=True)
    os.sync()
assert written <= 335_544_320, written
'
}

initialize_work_device() {
  initialization_mount="$(mktemp -d "$temporary_directory/mount.XXXXXX")"
  sudo mount -t ext4 -o rw,nosuid,nodev "$work_device" "$initialization_mount"
  sudo chown 1001:0 "$initialization_mount"
  sudo chmod 0770 "$initialization_mount"
  sudo umount "$initialization_mount"
  rmdir -- "$initialization_mount"
  initialization_mount=""
}

reset_work_device() {
  sudo /usr/sbin/mkfs.ext4 -q -F -m 0 -O '^has_journal' "$work_device"
  initialize_work_device
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
  test "$(docker volume inspect --format '{{ index .Options "type" }}' "$volume")" = ext4
  test "$(docker volume inspect --format '{{ index .Options "o" }}' "$volume")" = rw,nosuid,nodev
  test "$(docker volume inspect --format '{{ index .Options "device" }}' "$volume")" = "$work_device"
  docker volume rm "$volume" >/dev/null
}

"${compose[@]}" config --quiet
"${compose[@]}" up --detach
wait_for_services
verify_runtime_boundary
verify_work_capacity
write_checkpoint

# Retain durable volumes, remove only labeled scratch, then recreate containers and networks.
"${compose[@]}" down --remove-orphans --timeout 30
remove_work_volume
reset_work_device
"${compose[@]}" up --detach
wait_for_services
verify_runtime_boundary
verify_checkpoint

succeeded=true
