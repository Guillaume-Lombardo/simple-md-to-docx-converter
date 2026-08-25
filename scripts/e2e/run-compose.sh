#!/usr/bin/env bash
set -euo pipefail
umask 0077

repository="$(pwd)"
readonly repository
readonly compose_file="$repository/compose.yaml"
readonly quickstart_script="$repository/scripts/quickstart.sh"
readonly suffix="${GITHUB_RUN_ID:-local}-$$-$RANDOM"
readonly project="markweave-e2e-${suffix,,}"
readonly work_volume="${project}_markweave-work"
readonly data_volume="${project}_markweave-data"
readonly signatures_volume="${project}_clamav-signatures"

if [[ ! -f "$compose_file" || ! -x "$quickstart_script" ]]; then
  echo "Run this command from the repository root." >&2
  exit 2
fi

temporary_directory="$(mktemp -d)"
state_home="$temporary_directory/state"
state_directory="$state_home/markweave-quickstart"
template_file="$state_directory/quickstart-template.docx"
work_image="$state_directory/work.ext4"
state_file="$temporary_directory/checkpoint.json"
artifact_directory="$temporary_directory/artifacts"
fault_env="$temporary_directory/fault-compose.env"
unrelated_image="$temporary_directory/unrelated-one.img"
unrelated_down_image="$temporary_directory/unrelated-two.img"
unrelated_device=""
unrelated_down_device=""
password=""
port="$(uv run python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
succeeded=false

quickstart=(
  env
  "XDG_STATE_HOME=$state_home"
  "MARKWEAVE_QUICKSTART_PROJECT=$project"
  "MARKWEAVE_QUICKSTART_PORT=$port"
  "$quickstart_script"
)

quickstart() {
  "${quickstart[@]}" "$@"
}

work_device() {
  sudo /usr/sbin/losetup --noheadings --output NAME -j "$work_image" | xargs
}

backing_file() {
  local device="$1"
  sudo /usr/sbin/losetup --noheadings --raw --output BACK-FILE "$device"
}

write_fault_env() {
  local device="$1"
  printf 'MARKWEAVE_INITIAL_ADMIN_PASSWORD=%s\nMARKWEAVE_PORT=%s\nMARKWEAVE_WORK_DEVICE=%s\n' \
    "$password" "$port" "$device" >"$fault_env"
}

compose() {
  docker compose --project-name "$project" --project-directory "$repository" \
    --file "$compose_file" --env-file "$fault_env" "$@"
}

cleanup() {
  local exit_code=$?
  local device=""
  if [[ "$succeeded" != true && -f "$fault_env" ]]; then
    compose logs --no-color >&2 2>/dev/null || true
  fi
  quickstart down >/dev/null 2>&1 || true
  password="$(quickstart password 2>/dev/null)" || true
  write_fault_env /dev/null
  compose down --volumes --remove-orphans --timeout 30 >/dev/null 2>&1 || true
  if [[ -n "$unrelated_down_device" ]] && sudo /usr/sbin/losetup "$unrelated_down_device" >/dev/null 2>&1; then
    if [[ "$(backing_file "$unrelated_down_device")" == "$unrelated_down_image" ]]; then
      sudo /usr/sbin/losetup --detach "$unrelated_down_device" || exit_code=1
    else
      echo "Refusing to detach a reused loop device during E2E cleanup." >&2
      exit_code=1
    fi
  fi
  if [[ -n "$unrelated_device" ]] && sudo /usr/sbin/losetup "$unrelated_device" >/dev/null 2>&1; then
    if [[ "$(backing_file "$unrelated_device")" == "$unrelated_image" ]]; then
      sudo /usr/sbin/losetup --detach "$unrelated_device" || exit_code=1
    else
      echo "Refusing to detach a reused loop device during E2E cleanup." >&2
      exit_code=1
    fi
  fi
  for volume in "$work_volume" "$data_volume" "$signatures_volume"; do
    docker volume rm "$volume" >/dev/null 2>&1 || true
  done
  if [[ "$temporary_directory" == /tmp/tmp.* ]] && \
    [[ -z "$(sudo /usr/sbin/losetup --noheadings --output NAME -j "$unrelated_image" 2>/dev/null)" ]] && \
    [[ -z "$(sudo /usr/sbin/losetup --noheadings --output NAME -j "$unrelated_down_image" 2>/dev/null)" ]]; then
    rm -rf -- "$temporary_directory"
  else
    echo "Preserving an unexpected directory or attached E2E backing file: $temporary_directory" >&2
    exit_code=1
  fi
  exit "$exit_code"
}
trap cleanup EXIT

wait_for_services() {
  local application_id
  local scanner_id
  for _ in $(seq 1 240); do
    application_id="$(compose ps -q markweave)"
    scanner_id="$(compose ps -q clamav)"
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
  application_id="$(compose ps -q markweave)"
  scanner_id="$(compose ps -q clamav)"

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
  application_id="$(compose ps -q markweave)"
  test "$(stat -c %s -- "$work_image")" = 268435456
  docker exec "$application_id" python -c '
import errno
import os
from pathlib import Path

root = Path("/work")
stats = os.statvfs(root)
capacity = stats.f_blocks * stats.f_frsize
assert 240_000_000 <= capacity <= 268_435_456, capacity
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
assert written <= 268_435_456, written
'
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

filesystem_uuid() {
  /usr/sbin/blkid -p -s UUID -o value -- "$work_image"
}

mkdir -p -- "$artifact_directory"

# Exercise migration from the former unbounded local volume before the first start.
docker volume create \
  --label "com.docker.compose.project=$project" \
  --label "com.docker.compose.volume=markweave-work" \
  "$work_volume" >/dev/null
test -z "$(docker volume inspect --format '{{ index .Options "device" }}' "$work_volume")"

quickstart up
password="$(quickstart password)"
write_fault_env "$(work_device)"
compose config --quiet
wait_for_services
verify_runtime_boundary
verify_work_capacity
first_uuid="$(filesystem_uuid)"

# An up command against the healthy stack is idempotent and does not reformat scratch.
quickstart up
test "$(filesystem_uuid)" = "$first_uuid"
wait_for_services
write_checkpoint

# Simulate an abnormal host stop: Compose metadata survives, its loop association
# vanishes, and an unrelated file reuses the stale /dev/loopN allocation.
stale_device="$(work_device)"
write_fault_env "$stale_device"
compose down --remove-orphans --timeout 30
sudo /usr/sbin/losetup --detach "$stale_device"
truncate -s 1048576 "$unrelated_image"
unrelated_device="$(sudo /usr/sbin/losetup --find --show "$unrelated_image")"
test "$unrelated_device" = "$stale_device"

quickstart up
test "$(backing_file "$unrelated_device")" = "$unrelated_image"
recovered_device="$(work_device)"
test "$recovered_device" != "$unrelated_device"
test "$(filesystem_uuid)" != "$first_uuid"
password="$(quickstart password)"
write_fault_env "$recovered_device"
wait_for_services
verify_runtime_boundary
verify_work_capacity
verify_checkpoint

# Repeat the stale-device condition for the supported down command. It must
# remove its exact labeled volume and private image without touching the reused device.
compose down --remove-orphans --timeout 30
sudo /usr/sbin/losetup --detach "$recovered_device"
truncate -s 1048576 "$unrelated_down_image"
unrelated_down_device="$(sudo /usr/sbin/losetup --find --show "$unrelated_down_image")"
test "$unrelated_down_device" = "$recovered_device"
quickstart down
if docker volume inspect "$work_volume" >/dev/null 2>&1; then
  echo "The stale quickstart work volume survived down." >&2
  exit 1
fi
test ! -e "$work_image"
test "$(backing_file "$unrelated_down_device")" = "$unrelated_down_image"

succeeded=true
