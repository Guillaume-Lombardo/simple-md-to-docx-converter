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
readonly seccomp_profile="$repository/toolchain/document-engines/chrome-seccomp.json"
workspace="$(mktemp -d -t markweave-t37-e2e.XXXXXXXX)"
readonly workspace
readonly setup_script="$repository/tests/e2e/recovery_cli_setup.py"
readonly ready_service="markweave-t37-ready-${run_id}"

# shellcheck source=scripts/e2e/runtime-settings.sh
source "$repository/scripts/e2e/runtime-settings.sh"
e2e_runtime_settings
readonly readiness_settings=(
  "${E2E_SETTINGS[@]}"
  --env MARKWEAVE_MALWARE_SCANNING_MODE=trusted-upstream
  --env MARKWEAVE_PUBLIC_ORIGIN=http://127.0.0.1:8080
)

cleanup() {
  podman rm --force "$ready_service" "$postgres" "$rustfs" >/dev/null 2>&1 || true
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
ensure_postgres_database "$postgres" missing
for database in older_source older_target older_bad_source older_bad_target \
  older_newer_source; do
  ensure_postgres_database "$postgres" "$database"
done

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

# Prove a restored instance is already at head and serves readiness.
verify_restored_readiness() {
  local migration_json
  migration_json="$(run_cli "$@" "${readiness_settings[@]}" -- --json migrate)"
  jq -e '.changed == false' <<<"$migration_json" >/dev/null
  run_cli --detach --name "$ready_service" "$@" "${readiness_settings[@]}" \
    -- serve >/dev/null
  local ready=false
  for _attempt in $(seq 1 240); do
    if podman exec "$ready_service" /opt/md-converter/venv/bin/python -c \
      'import urllib.request; assert b"\"status\":\"ready\"" in urllib.request.urlopen("http://127.0.0.1:8080/health/ready", timeout=2).read()' \
      >/dev/null 2>&1; then
      ready=true
      break
    fi
    if [[ "$(podman inspect "$ready_service" --format '{{.State.Running}}' \
      2>/dev/null)" != true ]]; then
      break
    fi
    sleep 0.25
  done
  if [[ "$ready" != true ]]; then
    podman logs "$ready_service" >&2 || true
    echo "restored earlier-schema instance did not become ready" >&2
    exit 1
  fi
  podman rm --force "$ready_service" >/dev/null 2>&1 || true
}

expect_recovery_failure() {
  local message="$1"
  local output="$2"
  jq -e --arg message "$message" '
    .error.code == "recovery_failed" and .error.message == $message
  ' <<<"$output" >/dev/null
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
for case in active incomplete; do
  run_setup -- "standalone-missing-$case" --path /e2e/standalone-source
  missing_json="$(run_cli -- --json --non-interactive --timeout 60 backup \
    --profile standalone --data-directory /e2e/standalone-source \
    --destination "/e2e/standalone-missing-$case-sets")"
  missing_id="$(jq -er '.backup_id' <<<"$missing_json")"
  rejected_target="/e2e/standalone-$case-rejected"
  if rejected_json="$(run_cli -- --json --non-interactive --timeout 60 restore \
    --profile standalone \
    --source "/e2e/standalone-missing-$case-sets/$missing_id" \
    --data-directory "$rejected_target" \
    --offline-proof final-image-e2e --yes 2>&1)"; then
    echo "standalone restore accepted a missing $case upload" >&2
    exit 1
  fi
  jq -e '
    .error.code == "recovery_failed"
    and .error.message == "Restored stable object references are incomplete"
  ' <<<"$rejected_json" >/dev/null
  run_setup -- verify-empty-target --path "$rejected_target"
done
run_setup -- tamper --path "/e2e/standalone-sets/$standalone_id"
if run_cli -- --non-interactive --timeout 60 restore --profile standalone \
  --source "/e2e/standalone-sets/$standalone_id" \
  --data-directory /e2e/tampered-target \
  --offline-proof final-image-e2e --yes >/dev/null 2>&1; then
  echo "tampered standalone recovery set was accepted" >&2
  exit 1
fi

# A populated pre-T91 set (schema 20260923_24) restores to head with its references.
run_setup -- standalone-older-initialize --path /e2e/older-source
older_json="$(run_cli -- --json --non-interactive --timeout 60 backup \
  --profile standalone --data-directory /e2e/older-source \
  --destination /e2e/older-sets)"
older_id="$(jq -er '.backup_id' <<<"$older_json")"
run_cli -- --non-interactive --timeout 120 restore --profile standalone \
  --source "/e2e/older-sets/$older_id" --data-directory /e2e/older-restored \
  --offline-proof final-image-older-schema --yes >/dev/null
run_setup -- standalone-older-verify --path /e2e/older-restored \
  --records /e2e/older-source.records.json
verify_restored_readiness --env MARKWEAVE_STORAGE_PROFILE=standalone \
  --env MARKWEAVE_STANDALONE_DATA_DIRECTORY=/e2e/older-restored
for alteration in drop-table newer-revision; do
  altered_source="/e2e/older-$alteration-source"
  run_setup -- standalone-older-initialize --path "$altered_source"
  run_setup -- standalone-older-alter --path "$altered_source" \
    --alteration "$alteration"
  altered_json="$(run_cli -- --json --non-interactive --timeout 60 backup \
    --profile standalone --data-directory "$altered_source" \
    --destination "/e2e/older-$alteration-sets")"
  altered_id="$(jq -er '.backup_id' <<<"$altered_json")"
  rejected_target="/e2e/older-$alteration-rejected"
  if rejected_json="$(run_cli -- --json --non-interactive --timeout 120 restore \
    --profile standalone --source "/e2e/older-$alteration-sets/$altered_id" \
    --data-directory "$rejected_target" \
    --offline-proof final-image-older-schema --yes 2>&1)"; then
    echo "standalone restore accepted an altered earlier schema: $alteration" >&2
    exit 1
  fi
  if [[ "$alteration" == drop-table ]]; then
    expect_recovery_failure "Restored database schema is incomplete" \
      "$rejected_json"
  else
    expect_recovery_failure \
      "Recovery set schema revision is unknown to this release" "$rejected_json"
  fi
  run_setup -- verify-empty-target --path "$rejected_target"
done

readonly source_url='postgresql+psycopg://postgres:recovery-test-only@postgres:5432/source'
readonly target_url='postgresql+psycopg://postgres:recovery-test-only@postgres:5432/target'
readonly missing_url='postgresql+psycopg://postgres:recovery-test-only@postgres:5432/missing'
readonly common_s3=(
  --env RECOVERY_S3_ENDPOINT=http://rustfs:9000
  --env RECOVERY_S3_ACCESS=recovery-test
  --env RECOVERY_S3_SECRET=recovery-test-secret
  --env RECOVERY_SOURCE_BUCKET=source-bucket
  --env RECOVERY_TARGET_BUCKET=target-bucket
  --env RECOVERY_FAILED_BUCKET=failed-bucket
  --env RECOVERY_MISSING_BUCKET=missing-bucket
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
for case in active incomplete; do
  run_setup "${common_s3[@]}" --env RECOVERY_DATABASE="$source_url" -- \
    "distributed-missing-$case"
  missing_json="$(run_cli "${common_s3[@]}" \
    --env RECOVERY_DATABASE="$source_url" -- \
    --json --non-interactive --timeout 60 backup --profile distributed \
    --destination "/e2e/distributed-missing-$case-sets" \
    --database-url-environment RECOVERY_DATABASE \
    --s3-bucket source-bucket --s3-endpoint-url http://rustfs:9000 \
    --s3-region us-east-1 --s3-access-key-environment RECOVERY_S3_ACCESS \
    --s3-secret-key-environment RECOVERY_S3_SECRET \
    --consistency-proof final-image-workers-drained)"
  missing_id="$(jq -er '.backup_id' <<<"$missing_json")"
  if rejected_json="$(run_cli "${common_s3[@]}" \
    --env RECOVERY_DATABASE="$missing_url" -- \
    --json --non-interactive --timeout 60 restore --profile distributed \
    --source "/e2e/distributed-missing-$case-sets/$missing_id" \
    --database-url-environment RECOVERY_DATABASE \
    --s3-bucket missing-bucket --s3-endpoint-url http://rustfs:9000 \
    --s3-region us-east-1 --s3-access-key-environment RECOVERY_S3_ACCESS \
    --s3-secret-key-environment RECOVERY_S3_SECRET \
    --offline-proof final-image-isolated --yes 2>&1)"; then
    echo "distributed restore accepted a missing $case upload" >&2
    exit 1
  fi
  jq -e '
    .error.code == "recovery_failed"
    and .error.message == "Restored stable object references are incomplete"
  ' <<<"$rejected_json" >/dev/null
  run_setup "${common_s3[@]}" --env RECOVERY_DATABASE="$missing_url" -- \
    verify-empty-target
done
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

readonly postgres_root='postgresql+psycopg://postgres:recovery-test-only@postgres:5432'
readonly s3_cli=(
  --s3-endpoint-url http://rustfs:9000 --s3-region us-east-1
  --s3-access-key-environment RECOVERY_S3_ACCESS
  --s3-secret-key-environment RECOVERY_S3_SECRET
)
distributed_backup() {
  local database="$1"
  local bucket="$2"
  local destination="$3"
  run_cli "${common_s3[@]}" --env RECOVERY_DATABASE="$postgres_root/$database" -- \
    --json --non-interactive --timeout 120 backup --profile distributed \
    --destination "$destination" --database-url-environment RECOVERY_DATABASE \
    --s3-bucket "$bucket" "${s3_cli[@]}" \
    --consistency-proof final-image-older-schema-drained
}
distributed_restore() {
  local database="$1"
  local bucket="$2"
  local source="$3"
  run_cli "${common_s3[@]}" --env RECOVERY_DATABASE="$postgres_root/$database" -- \
    --json --non-interactive --timeout 120 restore --profile distributed \
    --source "$source" --database-url-environment RECOVERY_DATABASE \
    --s3-bucket "$bucket" "${s3_cli[@]}" \
    --offline-proof final-image-older-schema-isolated --yes
}

# A populated pre-T91 distributed set restores to head with its references.
run_setup "${common_s3[@]}" \
  --env RECOVERY_DATABASE="$postgres_root/older_source" \
  --env RECOVERY_SOURCE_BUCKET=older-source-bucket \
  --env RECOVERY_CREATE_BUCKETS=older-source-bucket,older-target-bucket,older-bad-bucket,older-rejected-bucket,older-newer-bucket \
  -- distributed-older-initialize --records /e2e/distributed-older.records.json
older_json="$(distributed_backup older_source older-source-bucket \
  /e2e/distributed-older-sets)"
older_id="$(jq -er '.backup_id' <<<"$older_json")"
distributed_restore older_target older-target-bucket \
  "/e2e/distributed-older-sets/$older_id" >/dev/null
run_setup "${common_s3[@]}" \
  --env RECOVERY_DATABASE="$postgres_root/older_target" \
  --env RECOVERY_TARGET_BUCKET=older-target-bucket \
  -- distributed-older-verify --records /e2e/distributed-older.records.json
verify_restored_readiness --env MARKWEAVE_STORAGE_PROFILE=distributed \
  --env MARKWEAVE_DISTRIBUTED_DATABASE_URL="$postgres_root/older_target" \
  --env MARKWEAVE_S3_BUCKET=older-target-bucket \
  --env MARKWEAVE_S3_ENDPOINT_URL=http://rustfs:9000 \
  --env MARKWEAVE_S3_REGION=us-east-1 \
  --env MARKWEAVE_S3_ACCESS_KEY_ID=recovery-test \
  --env MARKWEAVE_S3_SECRET_ACCESS_KEY=recovery-test-secret

# An incomplete earlier schema is refused, rolled back, and its objects removed.
run_setup "${common_s3[@]}" \
  --env RECOVERY_DATABASE="$postgres_root/older_bad_source" \
  --env RECOVERY_SOURCE_BUCKET=older-bad-bucket --env RECOVERY_CREATE_BUCKETS= \
  -- distributed-older-initialize --records /e2e/distributed-older-bad.records.json
run_setup "${common_s3[@]}" \
  --env RECOVERY_DATABASE="$postgres_root/older_bad_source" \
  -- distributed-older-alter --alteration drop-table
bad_json="$(distributed_backup older_bad_source older-bad-bucket \
  /e2e/distributed-older-bad-sets)"
bad_id="$(jq -er '.backup_id' <<<"$bad_json")"
if rejected_json="$(distributed_restore older_bad_target older-rejected-bucket \
  "/e2e/distributed-older-bad-sets/$bad_id" 2>&1)"; then
  echo "distributed restore accepted an incomplete earlier schema" >&2
  exit 1
fi
expect_recovery_failure "PostgreSQL backup schema is incompatible" \
  "$rejected_json"
run_setup "${common_s3[@]}" \
  --env RECOVERY_DATABASE="$postgres_root/older_bad_target" \
  --env RECOVERY_MISSING_BUCKET=older-rejected-bucket -- verify-empty-target

# A database at a revision unknown to this release is refused before any set exists.
run_setup "${common_s3[@]}" \
  --env RECOVERY_DATABASE="$postgres_root/older_newer_source" \
  --env RECOVERY_SOURCE_BUCKET=older-newer-bucket --env RECOVERY_CREATE_BUCKETS= \
  -- distributed-older-initialize --records /e2e/distributed-older-newer.records.json
run_setup "${common_s3[@]}" \
  --env RECOVERY_DATABASE="$postgres_root/older_newer_source" \
  -- distributed-older-alter --alteration newer-revision
if newer_json="$(distributed_backup older_newer_source older-newer-bucket \
  /e2e/distributed-older-newer-sets 2>&1)"; then
  echo "distributed backup accepted an unknown schema revision" >&2
  exit 1
fi
expect_recovery_failure "Recovery set schema revision is unknown to this release" \
  "$newer_json"

echo "Final-image standalone and distributed recovery CLI smoke passed, including earlier-schema sets."
