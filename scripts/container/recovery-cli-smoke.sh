#!/usr/bin/env bash
set -euo pipefail

ensure_postgres_database() {
  local container="$1"
  local database="$2"
  local _attempt
  local failure=""
  local ready_failures=0

  if [[ ! "$database" =~ ^[a-z][a-z0-9_]*$ ]]; then
    printf 'Invalid PostgreSQL database name: %s\n' "$database" >&2
    return 2
  fi
  for _attempt in {1..60}; do
    if failure="$(podman exec "$container" createdb \
      --username postgres "$database" 2>&1)"; then
      return 0
    fi
    if podman exec "$container" psql --username postgres --dbname postgres \
      --tuples-only --no-align \
      --command "SELECT 1 FROM pg_database WHERE datname = '$database'" \
      2>/dev/null | grep -qx 1; then
      return 0
    fi
    if podman exec "$container" pg_isready --username postgres \
      --dbname postgres >/dev/null 2>&1; then
      ready_failures=$((ready_failures + 1))
      if ((ready_failures >= 2)); then
        printf '%s\n' "$failure" >&2
        return 1
      fi
    else
      ready_failures=0
    fi
    sleep 1
  done
  printf '%s\n' "$failure" >&2
  return 1
}

if [[ "${BASH_SOURCE[0]-}" != "$0" ]]; then
  return 0
fi

readonly image="${1:?usage: recovery-cli-smoke.sh IMAGE}"
readonly postgres_image="docker.io/library/postgres:18-alpine@sha256:63bdc97d67b5133bf0e5ebd500bec6d046fa851dc81340d838f0347e616107e8"
readonly rustfs_image="ghcr.io/rustfs/rustfs:1.0.0-beta.12-glibc@sha256:6d693c8d0c09a1c5770f1780303a5d58b9e864c313fd2644ecd561e92b79ae04"
readonly run_id="$$"
readonly network="markweave-t37-${run_id}"
readonly postgres="markweave-t37-postgres-${run_id}"
readonly rustfs="markweave-t37-rustfs-${run_id}"
readonly runtime_uid="${T38_RECOVERY_RUNTIME_UID:-54000}"
readonly repository="${MARKWEAVE_REPOSITORY_ROOT:-$PWD}"
readonly seccomp_profile="$repository/spikes/toolchain/chrome-seccomp.json"
workspace="$(mktemp -d -t markweave-t37-e2e.XXXXXXXX)"
readonly workspace
readonly setup_script="$repository/tests/e2e/recovery_cli_setup.py"

cleanup() {
  podman rm --force "$postgres" "$rustfs" >/dev/null 2>&1 || true
  podman network rm "$network" >/dev/null 2>&1 || true
  if [[ "$workspace" == /tmp/markweave-t37-e2e.* ]]; then
    podman unshare rm -rf -- "$workspace"
  fi
}
trap cleanup EXIT

podman network create "$network" >/dev/null
podman run --detach --name "$postgres" --network "$network" \
  --network-alias postgres \
  --env POSTGRES_DB=source --env POSTGRES_PASSWORD=recovery-test-only \
  "$postgres_image" >/dev/null
podman run --detach --name "$rustfs" --network "$network" \
  --network-alias rustfs \
  --env RUSTFS_ACCESS_KEY=recovery-test \
  --env RUSTFS_SECRET_KEY=recovery-test-secret \
  --env RUSTFS_ADDRESS=0.0.0.0:9000 \
  --env RUSTFS_CONSOLE_ENABLE=false \
  "$rustfs_image" >/dev/null

ready=false
for _attempt in {1..60}; do
  if podman exec "$postgres" pg_isready -U postgres -d source >/dev/null 2>&1 \
    && podman exec "$rustfs" curl -fsS http://127.0.0.1:9000/health >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
[[ "$ready" == true ]]
ensure_postgres_database "$postgres" target

run_setup() {
  local -a container_arguments=()
  while [[ "$1" != -- ]]; do
    container_arguments+=("$1")
    shift
  done
  shift
  podman run --rm --network "$network" --entrypoint python \
    --volume "$workspace:/e2e:U,Z" \
    --volume "$setup_script:/tmp/recovery-cli-setup.py:ro,Z" \
    "${container_arguments[@]}" "$image" /tmp/recovery-cli-setup.py "$@"
}
run_cli() {
  local -a container_arguments=()
  while [[ "$1" != -- ]]; do
    container_arguments+=("$1")
    shift
  done
  shift
  podman run --rm --network "$network" \
    --user "$runtime_uid:0" --read-only --cap-drop=all \
    --security-opt=no-new-privileges --security-opt="seccomp=$seccomp_profile" \
    --memory=768m --cpus=2 --pids-limit=256 \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777 \
    --tmpfs /work:rw,nosuid,nodev,size=256m,mode=0770 \
    --shm-size=128m \
    --volume "$workspace:/e2e:U,Z" "${container_arguments[@]}" "$image" "$@"
}

run_setup -- standalone-initialize --path /e2e/standalone-source
standalone_json="$(run_cli -- --json --non-interactive --timeout 60 backup \
  --profile standalone --data-directory /e2e/standalone-source \
  --destination /e2e/standalone-sets)"
standalone_id="$(jq -er '.backup_id' <<<"$standalone_json")"
run_cli -- --non-interactive --timeout 60 restore --profile standalone \
  --source "/e2e/standalone-sets/$standalone_id" \
  --data-directory /e2e/standalone-restored \
  --offline-proof final-image-e2e --yes >/dev/null
run_setup -- standalone-verify --path /e2e/standalone-restored
run_setup -- tamper --path "/e2e/standalone-sets/$standalone_id"
if run_cli -- --non-interactive --timeout 60 restore --profile standalone \
  --source "/e2e/standalone-sets/$standalone_id" \
  --data-directory /e2e/tampered-target \
  --offline-proof final-image-e2e --yes >/dev/null 2>&1; then
  echo "tampered standalone recovery set was accepted" >&2
  exit 1
fi

readonly source_url='postgresql+psycopg://postgres:recovery-test-only@postgres:5432/source'
readonly target_url='postgresql+psycopg://postgres:recovery-test-only@postgres:5432/target'
readonly common_s3=(
  --env RECOVERY_S3_ENDPOINT=http://rustfs:9000
  --env RECOVERY_S3_ACCESS=recovery-test
  --env RECOVERY_S3_SECRET=recovery-test-secret
  --env RECOVERY_SOURCE_BUCKET=source-bucket
  --env RECOVERY_TARGET_BUCKET=target-bucket
  --env RECOVERY_FAILED_BUCKET=failed-bucket
)
run_setup "${common_s3[@]}" --env RECOVERY_DATABASE="$source_url" -- \
  distributed-initialize
distributed_json="$(run_cli "${common_s3[@]}" \
  --env RECOVERY_DATABASE="$source_url" -- \
  --json --non-interactive --timeout 60 backup --profile distributed \
  --destination /e2e/distributed-sets \
  --database-url-environment RECOVERY_DATABASE \
  --s3-bucket source-bucket --s3-endpoint-url http://rustfs:9000 \
  --s3-region us-east-1 --s3-access-key-environment RECOVERY_S3_ACCESS \
  --s3-secret-key-environment RECOVERY_S3_SECRET \
  --consistency-proof final-image-workers-drained)"
distributed_id="$(jq -er '.backup_id' <<<"$distributed_json")"
run_cli "${common_s3[@]}" --env RECOVERY_DATABASE="$target_url" -- \
  --non-interactive --timeout 60 restore --profile distributed \
  --source "/e2e/distributed-sets/$distributed_id" \
  --database-url-environment RECOVERY_DATABASE \
  --s3-bucket target-bucket --s3-endpoint-url http://rustfs:9000 \
  --s3-region us-east-1 --s3-access-key-environment RECOVERY_S3_ACCESS \
  --s3-secret-key-environment RECOVERY_S3_SECRET \
  --offline-proof final-image-isolated --yes >/dev/null
failed_restore_json=""
if failed_restore_json="$(run_cli "${common_s3[@]}" \
  --env RECOVERY_DATABASE="$source_url" -- \
  --json --non-interactive --timeout 60 restore --profile distributed \
  --source "/e2e/distributed-sets/$distributed_id" \
  --database-url-environment RECOVERY_DATABASE \
  --s3-bucket failed-bucket --s3-endpoint-url http://rustfs:9000 \
  --s3-region us-east-1 --s3-access-key-environment RECOVERY_S3_ACCESS \
  --s3-secret-key-environment RECOVERY_S3_SECRET \
  --offline-proof final-image-isolated --yes 2>&1)"; then
  echo "non-isolated distributed database target was accepted" >&2
  exit 1
fi
jq -e '
  .error.code == "recovery_failed"
  and .error.message == "Distributed restore target is not isolated"
' <<<"$failed_restore_json" >/dev/null
run_setup "${common_s3[@]}" -- distributed-cleanup-verify
run_setup "${common_s3[@]}" --env RECOVERY_DATABASE="$target_url" -- \
  distributed-verify

echo "Final-image standalone and distributed recovery CLI smoke passed."
