#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ( "$1" != standalone && "$1" != distributed ) ]]; then
  echo "Usage: scripts/e2e/run.sh {standalone|distributed}" >&2
  exit 2
fi

readonly profile="$1"
readonly repository="$(pwd)"
readonly published_image="${MARKWEAVE_E2E_IMAGE:-}"
readonly published_frontend_image="${MARKWEAVE_E2E_FRONTEND_IMAGE:-}"
readonly local_image="${MARKWEAVE_E2E_LOCAL_IMAGE:-}"
readonly local_frontend_image="${MARKWEAVE_E2E_LOCAL_FRONTEND_IMAGE:-}"
if [[ -n "$published_image" ]] &&
  [[ ! "$published_image" =~ ^ghcr\.io/guillaume-lombardo/md-converter:[0-9]+\.[0-9]+\.[0-9]+@sha256:[0-9a-f]{64}$ ]]; then
  echo "MARKWEAVE_E2E_IMAGE must be an immutable version-and-digest Markweave image." >&2
  exit 2
fi
if [[ -n "$published_frontend_image" ]] &&
  [[ ! "$published_frontend_image" =~ ^ghcr\.io/guillaume-lombardo/md-converter-web:[0-9]+\.[0-9]+\.[0-9]+@sha256:[0-9a-f]{64}$ ]]; then
  echo "MARKWEAVE_E2E_FRONTEND_IMAGE must be an immutable version-and-digest Markweave frontend image." >&2
  exit 2
fi
if { [[ -n "$published_image" ]] && [[ -z "$published_frontend_image" ]]; } ||
  { [[ -z "$published_image" ]] && [[ -n "$published_frontend_image" ]]; }; then
  echo "MARKWEAVE_E2E_IMAGE and MARKWEAVE_E2E_FRONTEND_IMAGE must be supplied together." >&2
  exit 2
fi
if [[ -n "$published_image" ]]; then
  backend_version="${published_image%@*}"
  backend_version="${backend_version##*:}"
  frontend_version="${published_frontend_image%@*}"
  frontend_version="${frontend_version##*:}"
  if [[ "$backend_version" != "$frontend_version" ]]; then
    echo "Published backend and frontend E2E image versions must match." >&2
    exit 2
  fi
fi
if { [[ -n "$local_image" ]] && [[ -z "$local_frontend_image" ]]; } ||
  { [[ -z "$local_image" ]] && [[ -n "$local_frontend_image" ]]; }; then
  echo "MARKWEAVE_E2E_LOCAL_IMAGE and MARKWEAVE_E2E_LOCAL_FRONTEND_IMAGE must be supplied together." >&2
  exit 2
fi
if [[ -n "$published_image" && -n "$local_image" ]]; then
  echo "Published and local E2E image pairs are mutually exclusive." >&2
  exit 2
fi
if [[ -n "$local_image" ]] && {
  [[ ! "$local_image" =~ ^localhost/md-converter:[a-zA-Z0-9_.-]+$ ]] ||
    [[ ! "$local_frontend_image" =~ ^localhost/md-converter-web:[a-zA-Z0-9_.-]+$ ]];
}; then
  echo "Local E2E images must use the isolated localhost Markweave package names." >&2
  exit 2
fi
if [[ -n "$local_image" ]]; then
  backend_version="${local_image##*:}"
  frontend_version="${local_frontend_image##*:}"
  if [[ "$backend_version" != "$frontend_version" ]]; then
    echo "Local backend and frontend E2E image versions must match." >&2
    exit 2
  fi
fi
readonly image="${published_image:-${local_image:-localhost/md-converter:t21-$profile}}"
readonly base_digest=sha256:194df4e35e0e5467e1b57266f4d61f821e1b1f567135f074d23066d3604ae653
readonly base_image="registry.access.redhat.com/ubi9/python-314@$base_digest"
readonly prefix="md-converter-t21-$profile"
readonly network_name="$prefix"
readonly application_name="$prefix-api"
readonly expiry_application_name="$prefix-expiry-api"
readonly insecure_application_name="$prefix-insecure-api"
readonly frontend_name="$prefix-frontend"
readonly router_name="$prefix-router"
readonly frontend_image="${published_frontend_image:-${local_frontend_image:-localhost/markweave-web:t64-$profile}}"
readonly clamav_name="$prefix-clamav"
readonly clamav_probe_name="$prefix-clamav-probe"
readonly postgres_name="$prefix-postgres"
readonly rustfs_name="$prefix-rustfs"
readonly worker_one_name="$prefix-worker-1"
readonly worker_two_name="$prefix-worker-2"
readonly runtime_uid="${T21_RUNTIME_UID:-51000}"
readonly artifact_directory="$repository/artifacts/e2e/$profile"
readonly seccomp_profile="$repository/spikes/toolchain/chrome-seccomp.json"

# shellcheck source=scripts/e2e/harness.sh
source "$repository/scripts/e2e/harness.sh"
worktree_baseline="$(e2e_get_worktree_state "$repository")"
readonly worktree_baseline
temporary_directory=""
temporary_directory_identity=""
e2e_initialize_harness_directory \
  temporary_directory temporary_directory_identity
readonly temporary_directory
readonly temporary_directory_identity
data_directory="$temporary_directory/data"
evidence_directory="$temporary_directory/evidence"
state_file="$temporary_directory/state.json"
recovery_state_file="$temporary_directory/recovery-state.json"
clamav_script="$temporary_directory/fake-clamav.py"
browser_runtime_directory="$temporary_directory/e2e"
node_runtime_directory="$temporary_directory/node_modules"
browser_session_directory="$temporary_directory/browser-session"
provisioning_file="$temporary_directory/users.csv"
provisioned_username="e2e-provisioned-$profile"
provisioned_initial_password="Provisioned-$profile-initial"
provisioned_renewed_password="Provisioned-$profile-browser-renewed"
provisioned_replacement_password="Provisioned-$profile-replacement"
created=()
succeeded=false

# shellcheck source=scripts/e2e/runtime-settings.sh
source "$repository/scripts/e2e/runtime-settings.sh"

remove_artifacts() {
  if [[ "$artifact_directory" != "$repository/artifacts/e2e/$profile" ]]; then
    echo "Refusing to remove an unexpected artifact path." >&2
    return 1
  fi
  rm -rf -- "$artifact_directory"
}

collect_failure_artifacts() {
  local resource container_state container_exit_code container_oom_killed
  mkdir -p -- "$artifact_directory"
  container_state=""
  container_exit_code=""
  container_oom_killed=""
  if ! mkdir -p -- "$temporary_directory/browser-artifacts"; then
    echo "Could not create browser artifact directory." >&2
  elif podman container exists "$application_name"; then
    if ! read -r container_state container_exit_code container_oom_killed < <(
      podman inspect "$application_name" \
        --format '{{.State.Status}} {{.State.ExitCode}} {{.State.OOMKilled}}' 2>/dev/null || true
    ); then
      container_state=""
      container_exit_code=""
      container_oom_killed=""
    fi
    [[ "$container_state" =~ ^[a-z-]+$ ]] || container_state=""
    [[ "$container_exit_code" =~ ^[0-9]+$ ]] || container_exit_code=""
    [[ "$container_oom_killed" == true || "$container_oom_killed" == false ]] \
      || container_oom_killed=""
    if [[ "$container_state" == running ]] && ! node "$browser_runtime_directory/resource-diagnostics.mjs" \
      --validate "$temporary_directory/browser-artifacts/resource-diagnostics.json" \
      >/dev/null 2>&1; then
      podman exec "$application_name" node /e2e/resource-diagnostics.mjs \
        >/dev/null 2>&1 || true
    fi
  fi
  if [[ -d "$temporary_directory/browser-artifacts" ]] && ! node "$browser_runtime_directory/resource-diagnostics.mjs" \
    --validate "$temporary_directory/browser-artifacts/resource-diagnostics.json" \
    >/dev/null 2>&1; then
    if ! node "$browser_runtime_directory/resource-diagnostics.mjs" \
      --output "$temporary_directory/browser-artifacts/resource-diagnostics.json" \
      --host-fallback \
      --container-state "$container_state" \
      --container-exit-code "$container_exit_code" \
      --container-oom-killed "$container_oom_killed"; then
      echo "Could not write fallback resource diagnostics." >&2
      return 1
    fi
    if ! node "$browser_runtime_directory/resource-diagnostics.mjs" \
      --validate "$temporary_directory/browser-artifacts/resource-diagnostics.json" \
      >/dev/null 2>&1; then
      echo "Fallback resource diagnostics failed schema validation." >&2
      return 1
    fi
  fi
  for resource in "${created[@]}"; do
    if [[ "$resource" == network:* || "$resource" == volume:* ]]; then
      continue
    fi
    if podman container exists "$resource"; then
      podman logs "$resource" >"$artifact_directory/$resource.log" 2>&1 || true
      podman inspect "$resource" \
        --format 'name={{.Name}} state={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}}' \
        >"$artifact_directory/$resource.state" 2>&1 || true
    fi
  done
  if [[ -d "$temporary_directory/browser-artifacts" ]]; then
    podman unshare chown -R 0:0 -- "$temporary_directory/browser-artifacts" \
      || true
    cp -a -- "$temporary_directory/browser-artifacts/." "$artifact_directory/" \
      || true
  fi
  printf 'profile=%s\nresult=failed\n' "$profile" >"$artifact_directory/summary.txt"
}

cleanup() {
  local exit_code=$?
  local resource
  if [[ "$succeeded" != true ]]; then
    if ! collect_failure_artifacts; then
      exit_code=1
    fi
  fi
  # The router joins the backend container's network namespace. Podman does
  # not guarantee dependency order within a multi-container removal request,
  # so detach that child before iterating over its possible parent entries.
  podman rm --force "$router_name" >/dev/null 2>&1 || true
  for resource in "${created[@]}"; do
    if [[ "$resource" == network:* ]]; then
      podman network rm "${resource#network:}" >/dev/null 2>&1 || true
    elif [[ "$resource" == volume:* ]]; then
      podman volume rm "${resource#volume:}" >/dev/null 2>&1 || true
    else
      podman rm --force "$resource" >/dev/null 2>&1 || true
    fi
  done
  if [[ "$succeeded" == true ]]; then
    if ! remove_artifacts; then
      exit_code=1
    fi
  fi
  if ! e2e_remove_harness_directory \
    "$temporary_directory" "$temporary_directory_identity"; then
    exit_code=1
  fi
  if ! e2e_require_worktree_state_unchanged \
    "$repository" "$worktree_baseline"; then
    exit_code=1
  fi
  exit "$exit_code"
}
trap cleanup EXIT

refuse_existing_resources() {
  local name
  for name in "$application_name" "$clamav_name" "$clamav_probe_name" \
    "$postgres_name" "$rustfs_name" \
    "$expiry_application_name" "$insecure_application_name" "$worker_one_name" \
    "$worker_two_name" "$frontend_name" "$router_name"; do
    if podman container exists "$name"; then
      echo "Refusing to replace pre-existing container $name." >&2
      exit 1
    fi
  done
  if podman network exists "$network_name"; then
    echo "Refusing to replace pre-existing network $network_name." >&2
    exit 1
  fi
}

wait_for_url() {
  local url="$1"
  local container="$2"
  local expected="$3"
  local attempt
  for attempt in $(seq 1 120); do
    if [[ -z "$expected" ]]; then
      if curl --fail --silent --show-error "$url" >/dev/null 2>&1; then
        return 0
      fi
    elif curl --fail --silent --show-error "$url" 2>/dev/null | grep -Fq "$expected"; then
      return 0
    fi
    if [[ "$(podman inspect "$container" --format '{{.State.Running}}' 2>/dev/null)" != true ]]; then
      podman logs "$container" >&2 || true
      return 1
    fi
    sleep 0.25
  done
  echo "Timed out waiting for $url." >&2
  return 1
}

wait_for_embedded_worker_idle() {
  local container="$1"
  podman exec "$container" /opt/md-converter/venv/bin/python -c '
from pathlib import Path
from time import monotonic, sleep

expected_name = "md-converter-embedded-worker"
deadline = monotonic() + 15
stable_task = None
stable_samples = 0
while monotonic() < deadline:
    sleeping_task = None
    for task in Path("/proc").glob("[0-9]*/task/[0-9]*"):
        try:
            name = (task / "comm").read_text(encoding="utf-8").strip()
            status = (task / "status").read_text(encoding="utf-8")
            wait_channel = (task / "wchan").read_text(encoding="utf-8").strip()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        state = next(
            (line.removeprefix("State:").strip() for line in status.splitlines()
             if line.startswith("State:")),
            "",
        )
        if (
            name.startswith("md-converter-")
            and expected_name.startswith(name)
            and state.startswith("S")
            and "futex" in wait_channel
        ):
            sleeping_task = str(task)
            break
    if sleeping_task == stable_task and sleeping_task is not None:
        stable_samples += 1
    else:
        stable_task = sleeping_task
        stable_samples = 1 if sleeping_task is not None else 0
    if stable_samples >= 5:
        raise SystemExit(0)
    sleep(0.1)
raise SystemExit(
    "embedded worker did not enter an observable stable idle wait within 15 seconds"
)
'
}

require_http_status() {
  local url="$1"
  local expected="$2"
  local actual
  actual="$(curl --silent --output /dev/null --write-out '%{http_code}' "$url")"
  if [[ "$actual" != "$expected" ]]; then
    echo "HTTP $actual from $url, expected $expected." >&2
    return 1
  fi
}

e2e_podman() {
  e2e_run_in_harness_directory \
    "$temporary_directory" "$temporary_directory_identity" podman "$@"
}

hardened_runtime=(
  --user "$runtime_uid:0"
  --read-only
  --cap-drop=all
  --security-opt=no-new-privileges
  --security-opt="seccomp=$seccomp_profile"
  --memory=768m
  --cpus=2
  --pids-limit=256
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777
  --tmpfs /work:rw,nosuid,nodev,size=256m,mode=0770
  --shm-size=128m
)

start_production_router() {
  local backend_container="$1"
  local backend_origin="${2:-http://127.0.0.1:8080}"
  local frontend_origin="${3:-http://frontend:3000}"
  local expected_api_status="${4:-401}"
  local probe_page="${5:-true}"
  e2e_podman rm --force "$router_name" >/dev/null 2>&1 || true
  e2e_run_in_harness_directory \
    "$temporary_directory" "$temporary_directory_identity" \
    podman run --detach --name "$router_name" \
    --network "container:$backend_container" --user "$runtime_uid:0" \
    --read-only --cap-drop=all --security-opt=no-new-privileges \
    --pids-limit=64 --memory=128m --cpus=0.5 \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m \
    --env ROUTER_HOST=127.0.0.1 --env ROUTER_PORT=3100 \
    --env "BACKEND_ORIGIN=$backend_origin" \
    --env "FRONTEND_ORIGIN=$frontend_origin" \
    --env PUBLIC_HOSTS=localhost:3100 \
    --env ROUTER_REQUEST_MAX_BYTES=1100000 \
    --env ROUTER_UPSTREAM_TIMEOUT_MS=30000 \
    "$frontend_image" node router.mjs >/dev/null
  for _ in $(seq 1 120); do
    if e2e_podman exec --env "EXPECTED_API_STATUS=$expected_api_status" \
      --env "PROBE_PAGE=$probe_page" \
      "$backend_container" node -e \
      'const o={signal:AbortSignal.timeout(1000)}; const page=process.env.PROBE_PAGE === "true" ? fetch("http://localhost:3100/login",o) : Promise.resolve({status:200}); Promise.all([page,fetch("http://localhost:3100/api/v1/session",o)]).then(([p,a]) => process.exit(p.status === 200 && a.status === Number(process.env.EXPECTED_API_STATUS) ? 0 : 1)).catch(() => process.exit(1))' \
      >/dev/null 2>&1; then
      return 0
    fi
    if [[ "$(podman inspect "$router_name" --format '{{.State.Running}}' 2>/dev/null)" != true ]]; then
      e2e_podman logs "$router_name" >&2 || true
      return 1
    fi
    sleep 0.25
  done
  echo "Timed out waiting for the production router." >&2
  e2e_podman logs "$router_name" >&2 || true
  e2e_podman logs "$frontend_name" >&2 || true
  return 1
}

start_frontend() {
  e2e_podman rm --force "$frontend_name" >/dev/null 2>&1 || true
  e2e_run_in_harness_directory \
    "$temporary_directory" "$temporary_directory_identity" \
    podman run --detach --name "$frontend_name" --network "$network_name" \
    --network-alias frontend --user "$runtime_uid:0" --read-only --cap-drop=all \
    --security-opt=no-new-privileges --pids-limit=64 --memory=256m --cpus=0.5 \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m --env HOSTNAME=0.0.0.0 \
    "$frontend_image" >/dev/null
  for _ in $(seq 1 120); do
    if e2e_podman exec "$frontend_name" node -e \
      'fetch("http://127.0.0.1:3001/_frontend/health/ready",{signal:AbortSignal.timeout(1000)}).then(r => process.exit(r.status === 200 ? 0 : 1)).catch(() => process.exit(1))' \
      >/dev/null 2>&1; then
      return 0
    fi
    if [[ "$(podman inspect "$frontend_name" --format '{{.State.Running}}' 2>/dev/null)" != true ]]; then
      e2e_podman logs "$frontend_name" >&2 || true
      return 1
    fi
    sleep 0.25
  done
  echo "Timed out waiting for the frontend readiness probe." >&2
  e2e_podman logs "$frontend_name" >&2 || true
  return 1
}

restart_backend_and_router() {
  local backend_container="$1"
  local backend_port
  # The router is a child of the backend network namespace. Remove it before
  # restarting that namespace owner, then recreate and probe it afterwards.
  e2e_podman rm --force "$router_name" >/dev/null
  e2e_podman restart --time 15 "$backend_container" >/dev/null
  backend_port="$(e2e_podman port "$backend_container" 8080/tcp | sed 's/.*://')"
  wait_for_url "http://127.0.0.1:$backend_port/health/ready" \
    "$backend_container" '"status":"ready"'
  start_production_router "$backend_container"
}

kill_backend_and_reconnect_router() {
  local backend_container="$1"
  local backend_port
  # A forced backend restart must fence the router that shares its network
  # namespace. Recreate the router only after the backend is ready again.
  e2e_podman rm --force "$router_name" >/dev/null
  e2e_podman kill --signal KILL "$backend_container" >/dev/null
  test "$(e2e_podman inspect "$backend_container" --format '{{.State.ExitCode}}')" = 137
  e2e_podman start "$backend_container" >/dev/null
  backend_port="$(e2e_podman port "$backend_container" 8080/tcp | sed 's/.*://')"
  wait_for_url "http://127.0.0.1:$backend_port/health/ready" \
    "$backend_container" '"status":"ready"'
  start_production_router "$backend_container"
}

remove_artifacts
mkdir -p -- "$data_directory" "$evidence_directory" \
  "$temporary_directory/browser-artifacts" "$browser_session_directory"
chmod 0770 "$data_directory"
chmod 0777 "$evidence_directory" "$temporary_directory/browser-artifacts" \
  "$browser_session_directory"
printf '%s\n%s,%s,user,true,true\n' \
  'username,password,role,active,password_change_required' \
  "$provisioned_username" "$provisioned_initial_password" >"$provisioning_file"
chmod 0444 "$provisioning_file"
install -m 0444 "$repository/scripts/container/fake-clamav.py" "$clamav_script"
cp -a "$repository/tests/e2e" "$browser_runtime_directory"
COREPACK_ENABLE_NETWORK=0 pnpm install --frozen-lockfile --ignore-scripts --filter md-converter-web-tests
cp -a "$repository/node_modules" "$node_runtime_directory"
chmod -R a+rX "$browser_runtime_directory" "$node_runtime_directory"
refuse_existing_resources

test "$(podman info --format '{{.Host.Security.Rootless}}')" = true
bash scripts/e2e/rollback-rehearsal.sh "$profile"
if [[ -n "$published_image" ]]; then
  podman pull --quiet "$image"
  test "$(podman image inspect "$image" --format '{{.Digest}}')" = "${image##*@}"
  podman pull --quiet "$frontend_image"
  test "$(podman image inspect "$frontend_image" --format '{{.Digest}}')" = \
    "${frontend_image##*@}"
elif [[ -n "$local_image" ]]; then
  podman image exists "$image"
  podman image exists "$frontend_image"
else
  podman pull --quiet "$base_image"
  test "$(podman image inspect "$base_image" --format '{{.Digest}}')" = "$base_digest"
  bash scripts/container/build.sh "$image"
  podman build --format oci --tag "$frontend_image" --file web/Containerfile .
fi

podman network create "$network_name" >/dev/null
created+=("network:$network_name")

created=("$clamav_name" "${created[@]}")
e2e_run_in_harness_directory \
  "$temporary_directory" "$temporary_directory_identity" \
  podman run --detach --name "$clamav_name" --network "$network_name" \
  --network-alias e2e-clamav --read-only --cap-drop=all \
  --security-opt=no-new-privileges --pids-limit=64 --memory=128m \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=8m \
  --volume "$clamav_script:/fake-clamav.py:ro,Z" \
  --entrypoint /opt/md-converter/venv/bin/python \
  "$image" /fake-clamav.py >/dev/null

created=("$clamav_probe_name" "${created[@]}")
e2e_run_in_harness_directory \
  "$temporary_directory" "$temporary_directory_identity" \
  podman run --detach --name "$clamav_probe_name" --network "$network_name" \
  --read-only --cap-drop=all --security-opt=no-new-privileges \
  --pids-limit=16 --memory=64m --tmpfs /tmp:rw,nosuid,nodev,noexec,size=4m \
  --entrypoint /opt/md-converter/venv/bin/python \
  "$image" -c 'import time; time.sleep(30)' >/dev/null
bash "$repository/scripts/container/wait-for-fake-clamav.sh" \
  "$clamav_name" "$profile-alias" "$clamav_probe_name" e2e-clamav
podman kill --signal KILL "$clamav_probe_name" >/dev/null
podman rm "$clamav_probe_name" >/dev/null

clamav_address="$(podman inspect "$clamav_name" \
  --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')"
if [[ -z "$clamav_address" ]]; then
  echo "Fake ClamAV has no address on the E2E network." >&2
  exit 1
fi
# The unmapped peer above proves the network alias. Application and worker
# containers use this mapping to avoid later transient Netavark DNS failures.
scanner_host_mapping=(--add-host "e2e-clamav:$clamav_address")

e2e_runtime_settings
if [[ "$profile" == standalone ]]; then
  E2E_SETTINGS+=(
    --env MARKWEAVE_STORAGE_PROFILE=standalone
    --env MARKWEAVE_STANDALONE_DATA_DIRECTORY=/data
    --env MARKWEAVE_PUBLIC_ORIGIN=http://127.0.0.1:8080
  )
else
  created=("$postgres_name" "${created[@]}")
  e2e_run_in_harness_directory \
    "$temporary_directory" "$temporary_directory_identity" \
    podman run --detach --name "$postgres_name" --network "$network_name" \
    --network-alias postgres --env POSTGRES_DB=md_converter_e2e \
    --env POSTGRES_PASSWORD=e2e-postgres-password \
    docker.io/library/postgres:18-alpine@sha256:63bdc97d67b5133bf0e5ebd500bec6d046fa851dc81340d838f0347e616107e8 \
    >/dev/null
  created=("$rustfs_name" "${created[@]}")
  e2e_run_in_harness_directory \
    "$temporary_directory" "$temporary_directory_identity" \
    podman run --detach --name "$rustfs_name" --network "$network_name" \
    --network-alias rustfs --publish 127.0.0.1::9000 \
    --env RUSTFS_ACCESS_KEY=e2eaccess --env RUSTFS_SECRET_KEY=e2esecret \
    --env RUSTFS_ADDRESS=0.0.0.0:9000 --env RUSTFS_CONSOLE_ENABLE=false \
    ghcr.io/rustfs/rustfs:1.0.0-beta.12-glibc@sha256:6d693c8d0c09a1c5770f1780303a5d58b9e864c313fd2644ecd561e92b79ae04 \
    >/dev/null
  for _ in $(seq 1 120); do
    podman exec "$postgres_name" pg_isready -U postgres -d md_converter_e2e \
      >/dev/null 2>&1 && break
    sleep 0.25
  done
  rustfs_port="$(podman port "$rustfs_name" 9000/tcp | sed 's/.*://')"
  wait_for_url "http://127.0.0.1:$rustfs_port/health" "$rustfs_name" ""
  MARKWEAVE_TEST_S3_ACCESS_KEY_ID=e2eaccess \
  MARKWEAVE_TEST_S3_BUCKET=md-converter-t21 \
  MARKWEAVE_TEST_S3_ENDPOINT_URL="http://127.0.0.1:$rustfs_port" \
  MARKWEAVE_TEST_S3_REGION=us-east-1 \
  MARKWEAVE_TEST_S3_SECRET_ACCESS_KEY=e2esecret \
    uv run python -m scripts.ci.prepare_s3_test_bucket
  E2E_SETTINGS+=(
    --env MARKWEAVE_STORAGE_PROFILE=distributed
    --env MARKWEAVE_DISTRIBUTED_DATABASE_URL=postgresql+psycopg://postgres:e2e-postgres-password@postgres:5432/md_converter_e2e
    --env MARKWEAVE_S3_BUCKET=md-converter-t21
    --env MARKWEAVE_S3_ENDPOINT_URL=http://rustfs:9000
    --env MARKWEAVE_S3_REGION=us-east-1
    --env MARKWEAVE_S3_ACCESS_KEY_ID=e2eaccess
    --env MARKWEAVE_S3_SECRET_ACCESS_KEY=e2esecret
  )
fi

application_volumes=(
  --volume "$browser_runtime_directory:/e2e:ro,Z"
  --volume "$node_runtime_directory:/node_modules:ro,Z"
  --volume "$evidence_directory:/evidence:rw,Z"
  --volume "$temporary_directory/browser-artifacts:/browser-artifacts:rw,Z"
  --volume "$browser_session_directory:/browser-session:rw,Z"
  --volume "$provisioning_file:/run/secrets/users.csv:ro,Z"
)
if [[ "$profile" == standalone ]]; then
  application_volumes+=(--volume "$data_directory:/data:rw,Z")
fi

application_mode=serve
application_settings=(
  "${E2E_SETTINGS[@]}"
  --env MARKWEAVE_USER_PROVISIONING_FILE=/run/secrets/users.csv
)
created=("$application_name" "${created[@]}")
e2e_run_in_harness_directory \
  "$temporary_directory" "$temporary_directory_identity" \
  podman run --detach --name "$application_name" --network "$network_name" \
  --network-alias application --publish 127.0.0.1::8080 \
  "${scanner_host_mapping[@]}" \
  "${hardened_runtime[@]}" "${application_volumes[@]}" "${application_settings[@]}" \
  "$image" "$application_mode" >/dev/null

if [[ "$profile" == distributed ]]; then
  for worker in "$worker_one_name" "$worker_two_name"; do
    created=("$worker" "${created[@]}")
    e2e_run_in_harness_directory \
      "$temporary_directory" "$temporary_directory_identity" \
      podman run --detach --name "$worker" --network "$network_name" \
      "${scanner_host_mapping[@]}" \
      --publish 127.0.0.1::9464 "${hardened_runtime[@]}" "${E2E_SETTINGS[@]}" \
      "$image" worker >/dev/null
  done
fi

application_port="$(podman port "$application_name" 8080/tcp | sed 's/.*://')"
base_url="http://127.0.0.1:$application_port"
wait_for_url "$base_url/health/ready" "$application_name" '"status":"ready"'
bash "$repository/scripts/container/wait-for-fake-clamav.sh" \
  "$clamav_name" "$profile-mapped" "$application_name" e2e-clamav
podman exec "$application_name" python -c '
from pathlib import Path

arguments = Path("/proc/1/cmdline").read_bytes().rstrip(b"\0").split(b"\0")
assert any(value.endswith(b"/markweave") for value in arguments), arguments
assert arguments[-1] == b"serve", arguments
'
if [[ "$profile" == distributed ]]; then
  for worker in "$worker_one_name" "$worker_two_name"; do
    podman exec "$worker" python -c '
from pathlib import Path

arguments = Path("/proc/1/cmdline").read_bytes().rstrip(b"\0").split(b"\0")
assert any(value.endswith(b"/markweave") for value in arguments), arguments
assert arguments[-1] == b"worker", arguments
'
  done
fi
e2e_run_in_harness_directory \
  "$temporary_directory" "$temporary_directory_identity" \
  podman run --rm --network "container:$application_name" \
  "${hardened_runtime[@]}" \
  "$image" --json health live --url http://127.0.0.1:8080 \
  | grep -Fq '"status":"ok"'
e2e_run_in_harness_directory \
  "$temporary_directory" "$temporary_directory_identity" \
  podman run --rm --network "container:$application_name" \
  "${hardened_runtime[@]}" \
  "$image" --json health ready --url http://127.0.0.1:8080 \
  | grep -Fq '"status":"ready"'

if [[ "$profile" == standalone ]]; then
  podman exec "$application_name" /opt/md-converter/venv/bin/python -c '
import http.client
import json

payload = json.dumps({"username": "e2e-admin", "password": "e2e-admin-password"})

def login(origin):
    connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=10)
    connection.request(
        "POST",
        "/api/v1/login",
        body=payload,
        headers={
            "Content-Type": "application/json",
            "Origin": origin,
            "Forwarded": "host=attacker.example;proto=https",
            "X-Forwarded-Host": "attacker.example",
            "X-Forwarded-Proto": "https",
        },
    )
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response.status, body

accepted_status, _ = login("http://127.0.0.1:8080")
hostile_status, hostile_body = login("https://attacker.example")
assert accepted_status == 200
assert hostile_status == 403
assert json.loads(hostile_body)["error"]["code"] == "LOGIN_ORIGIN_INVALID"
'
fi

podman exec "$application_name" /opt/md-converter/venv/bin/python -c \
  'from pathlib import Path; Path("/tmp/e2e-template.md").write_text("# Template\n", encoding="utf-8")'
podman exec "$application_name" pandoc /tmp/e2e-template.md --output=/tmp/e2e-template.docx
podman cp "$application_name:/tmp/e2e-template.docx" "$evidence_directory/template.docx"
cp -- "$evidence_directory/template.docx" "$evidence_directory/browser-template.docx"
uv run python -c \
  'import sys; from pathlib import Path; from scripts.container.api_workflow_smoke import candidate_reference; path = Path(sys.argv[1]); path.write_bytes(candidate_reference(path.read_bytes()))' \
  "$evidence_directory/browser-template.docx"
printf '# Final image E2E\n\nReal **conversion** workflow.\n' >"$evidence_directory/source.md"
chmod 0444 "$evidence_directory/template.docx" \
  "$evidence_directory/browser-template.docx" "$evidence_directory/source.md"

uv run python -m tests.e2e.service_workflow exercise-security-boundaries \
  --base-url "$base_url" --profile "$profile" \
  --template "$evidence_directory/template.docx" \
  --artifact-dir "$temporary_directory/browser-artifacts"

worker_metrics=()
if [[ "$profile" == distributed ]]; then
  worker_metrics+=(--worker-metrics-url "http://127.0.0.1:$(podman port "$worker_one_name" 9464/tcp | sed 's/.*://')/metrics")
  worker_metrics+=(--worker-metrics-url "http://127.0.0.1:$(podman port "$worker_two_name" 9464/tcp | sed 's/.*://')/metrics")
fi

uv run python -m tests.e2e.service_workflow exercise \
  --base-url "$base_url" --profile "$profile" \
  --template "$evidence_directory/template.docx" --state-file "$state_file" \
  --artifact-dir "$temporary_directory/browser-artifacts" \
  --api-metrics-url "$base_url/metrics" "${worker_metrics[@]}"

uv run python -m tests.e2e.cli_workflow --container "$application_name" --profile "$profile"

uv run python -m tests.e2e.conversion_cli_workflow \
  --container "$application_name" --profile "$profile"

uv run python -m tests.e2e.administration_cli_workflow \
  --container "$application_name"

uv run python -m tests.e2e.template_cli_workflow \
  --container "$application_name" --profile "$profile"

chmod 0644 "$provisioning_file"
printf '%s\n%s,%s,user,true,true\n' \
  'username,password,role,active,password_change_required' \
  "$provisioned_username" "$provisioned_replacement_password" >"$provisioning_file"
chmod 0444 "$provisioning_file"
podman restart --time 15 "$application_name" >/dev/null
wait_for_url "$base_url/health/ready" "$application_name" '"status":"ready"'
uv run python -m tests.e2e.service_workflow submit-recovery \
  --base-url "$base_url" --profile "$profile" --output both \
  --template "$evidence_directory/template.docx" \
  --state-file "$recovery_state_file" \
  --artifact-dir "$temporary_directory/browser-artifacts"
if [[ "$profile" == standalone ]]; then
  podman kill --signal KILL "$application_name" >/dev/null
  test "$(podman inspect "$application_name" --format '{{.State.ExitCode}}')" = 137
  podman start "$application_name" >/dev/null
else
  podman kill --signal KILL "$application_name" "$worker_one_name" \
    "$worker_two_name" >/dev/null
  test "$(podman inspect "$application_name" --format '{{.State.ExitCode}}')" = 137
  test "$(podman inspect "$worker_one_name" --format '{{.State.ExitCode}}')" = 137
  test "$(podman inspect "$worker_two_name" --format '{{.State.ExitCode}}')" = 137
  podman start "$application_name" "$worker_one_name" "$worker_two_name" >/dev/null
fi
wait_for_url "$base_url/health/ready" "$application_name" '"status":"ready"'
uv run python -m tests.e2e.service_workflow verify-recovery \
  --base-url "$base_url" --profile "$profile" \
  --state-file "$recovery_state_file" \
  --artifact-dir "$temporary_directory/browser-artifacts"

require_http_status "$base_url/health/live" 200
if [[ "$profile" == standalone ]]; then
  chmod 000 "$data_directory"
else
  podman stop --time 10 "$rustfs_name" >/dev/null
fi
require_http_status "$base_url/health/ready" 503
require_http_status "$base_url/health/live" 200
uv run python -m tests.e2e.administration_cli_workflow \
  --container "$application_name" expect-readiness-failure
if [[ "$profile" == standalone ]]; then
  chmod 0770 "$data_directory"
else
  podman start "$rustfs_name" >/dev/null
  wait_for_url "http://127.0.0.1:$rustfs_port/health" "$rustfs_name" ""
  wait_for_url "$base_url/health/ready" "$application_name" '"status":"ready"'
  podman stop --time 10 "$postgres_name" >/dev/null
  require_http_status "$base_url/health/ready" 503
  require_http_status "$base_url/health/live" 200
  podman start "$postgres_name" >/dev/null
  for _ in $(seq 1 120); do
    podman exec "$postgres_name" pg_isready -U postgres -d md_converter_e2e \
      >/dev/null 2>&1 && break
    sleep 0.25
  done
fi
wait_for_url "$base_url/health/ready" "$application_name" '"status":"ready"'

uv run python -m tests.e2e.service_workflow checkpoint \
  --base-url "$base_url" --profile "$profile" \
  --template "$evidence_directory/template.docx" --state-file "$state_file" \
  --policy-evidence \
  --artifact-dir "$temporary_directory/browser-artifacts"

podman restart --time 15 "$application_name" >/dev/null
wait_for_url "$base_url/health/ready" "$application_name" '"status":"ready"'
uv run python -m tests.e2e.service_workflow verify-checkpoint \
  --base-url "$base_url" --profile "$profile" \
  --template "$evidence_directory/template.docx" --state-file "$state_file" \
  --artifact-dir "$temporary_directory/browser-artifacts"

# Prove that an isolated snapshot restores the durable identities, jobs,
# results, templates, and merged audit history. All snapshot bytes remain in
# the private temporary directory and are never retained as failure artifacts.
podman stop --time 15 "$application_name" >/dev/null
if [[ "$profile" == distributed ]]; then
  podman stop --time 15 "$worker_one_name" "$worker_two_name" >/dev/null
  podman exec "$postgres_name" pg_dump --username postgres --dbname md_converter_e2e \
    --format=custom --file=/tmp/md-converter-e2e.dump
  podman cp "$postgres_name:/tmp/md-converter-e2e.dump" \
    "$temporary_directory/postgres.dump"
  uv run python -m scripts.e2e.s3_backup backup \
    --endpoint-url "http://127.0.0.1:$rustfs_port" --region us-east-1 \
    --access-key-id e2eaccess --secret-access-key e2esecret \
    --bucket md-converter-t21 --directory "$temporary_directory/s3-backup"
  podman exec "$postgres_name" dropdb --username postgres md_converter_e2e
  podman exec "$postgres_name" createdb --username postgres md_converter_e2e
  podman cp "$temporary_directory/postgres.dump" \
    "$postgres_name:/tmp/md-converter-e2e.dump"
  podman exec "$postgres_name" pg_restore --username postgres \
    --dbname md_converter_e2e --exit-on-error /tmp/md-converter-e2e.dump
  uv run python -m scripts.e2e.s3_backup restore \
    --endpoint-url "http://127.0.0.1:$rustfs_port" --region us-east-1 \
    --access-key-id e2eaccess --secret-access-key e2esecret \
    --bucket md-converter-t21 --directory "$temporary_directory/s3-backup"
  podman start "$worker_one_name" "$worker_two_name" >/dev/null
else
  mkdir -m 0700 "$temporary_directory/standalone-backup"
  podman unshare cp -a -- "$data_directory/." \
    "$temporary_directory/standalone-backup/"
  podman unshare find "$data_directory" -mindepth 1 -delete
  podman unshare cp -a -- "$temporary_directory/standalone-backup/." \
    "$data_directory/"
fi
podman start "$application_name" >/dev/null
wait_for_url "$base_url/health/ready" "$application_name" '"status":"ready"'
uv run python -m tests.e2e.service_workflow verify-checkpoint \
  --base-url "$base_url" --profile "$profile" \
  --template "$evidence_directory/template.docx" --state-file "$state_file" \
  --artifact-dir "$temporary_directory/browser-artifacts"

checkpoint_policy_values="$(
  uv run python -c '
import json
import sys

with open(sys.argv[1], encoding="utf-8") as state_file:
    state = json.load(state_file)
keys = (
    "policy_user_idle_minutes",
    "policy_admin_idle_minutes",
    "policy_revision",
)
values = [state.get(key) for key in keys]
if not all(
    isinstance(value, str) and value.isascii() and value.isdecimal()
    for value in values
):
    raise SystemExit("checkpoint policy evidence is invalid")
print(*values, sep="\t")
' "$state_file"
)"
IFS=$'\t' read -r checkpoint_user_idle_minutes \
  checkpoint_admin_idle_minutes checkpoint_policy_revision \
  <<<"$checkpoint_policy_values"
readonly checkpoint_user_idle_minutes checkpoint_admin_idle_minutes \
  checkpoint_policy_revision

# Exercise the final frontend and backend through the production same-origin router.
podman rm --force "$application_name" >/dev/null
created=("$router_name" "$frontend_name" "${created[@]}")
start_frontend
e2e_run_in_harness_directory \
  "$temporary_directory" "$temporary_directory_identity" \
  podman run --detach --name "$application_name" --network "$network_name" \
  --network-alias application --publish 127.0.0.1::8080 \
  "${scanner_host_mapping[@]}" \
  "${hardened_runtime[@]}" "${application_volumes[@]}" "${application_settings[@]}" \
  --env MARKWEAVE_PUBLIC_ORIGIN=http://localhost:3100 \
  "$image" "$application_mode" >/dev/null
wait_for_url "http://127.0.0.1:$(podman port "$application_name" 8080/tcp | sed 's/.*://')/health/ready" \
  "$application_name" '"status":"ready"'
start_production_router "$application_name"
podman exec \
  --env MARKWEAVE_E2E_BASE_URL=http://localhost:3100 \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_PROVISIONED_USERNAME="$provisioned_username" \
  --env MARKWEAVE_E2E_PROVISIONED_OLD_PASSWORD="$provisioned_renewed_password" \
  --env MARKWEAVE_E2E_PROVISIONED_PASSWORD="$provisioned_replacement_password" \
  "$application_name" node --test /e2e/browser-provisioning-restart.test.mjs
podman exec \
  --env MARKWEAVE_E2E_BASE_URL=http://localhost:3100 \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_RECOVERY_STATE=/browser-session/admin.json \
  --env MARKWEAVE_E2E_ARTIFACT_DIR=/browser-artifacts \
  --env MARKWEAVE_E2E_ADMIN_USERNAME=e2e-admin \
  --env MARKWEAVE_E2E_ADMIN_PASSWORD=e2e-admin-password \
  "$application_name" node --test /e2e/browser-recovery-checkpoint.test.mjs
kill_backend_and_reconnect_router "$application_name"
podman exec \
  --env MARKWEAVE_E2E_BASE_URL=http://localhost:3100 \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_RECOVERY_STATE=/browser-session/admin.json \
  --env MARKWEAVE_E2E_ARTIFACT_DIR=/browser-artifacts \
  "$application_name" node --test /e2e/browser-recovery.test.mjs
podman exec \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_ARTIFACT_DIR=/browser-artifacts \
  "$application_name" node --test /e2e/browser-next-auth.test.mjs
podman exec \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_ARTIFACT_DIR=/browser-artifacts \
  --env MARKWEAVE_E2E_CONVERSION_STATE=/browser-session/next-conversion.json \
  "$application_name" node --test /e2e/browser-next-conversion.test.mjs
podman exec \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  "$application_name" node --test /e2e/browser-next-conversion-failure.test.mjs

# Hold job execution while exercising exact admission boundaries through the
# real final-image API and Next.js UI. Distributed workers can be stopped
# independently. Standalone is recreated with a long idle poll only for this
# isolated phase; the named worker thread must observably remain asleep in its
# interruptible futex wait after the initial empty claim before submissions begin.
podman rm --force "$router_name" >/dev/null
podman rm --force "$application_name" >/dev/null
if [[ "$profile" == distributed ]]; then
  podman stop --time 15 "$worker_one_name" "$worker_two_name" >/dev/null
fi
created=("$application_name" "${created[@]}")
e2e_run_in_harness_directory \
  "$temporary_directory" "$temporary_directory_identity" \
  podman run --detach --name "$application_name" --network "$network_name" \
  --network-alias application --publish 127.0.0.1::8080 \
  "${scanner_host_mapping[@]}" \
  "${hardened_runtime[@]}" "${application_volumes[@]}" "${application_settings[@]}" \
  --env MARKWEAVE_PUBLIC_ORIGIN=http://localhost:3100 \
  --env MARKWEAVE_JOB_ACTIVE_LIMIT_PER_USER=2 \
  --env MARKWEAVE_JOB_GLOBAL_QUEUE_CAPACITY=3 \
  --env MARKWEAVE_WORKER_IDLE_POLL_SECONDS=600 \
  "$image" "$application_mode" >/dev/null
wait_for_url "http://127.0.0.1:$(podman port "$application_name" 8080/tcp | sed 's/.*://')/health/ready" \
  "$application_name" '"status":"ready"'
if [[ "$profile" == standalone ]]; then
  wait_for_embedded_worker_idle "$application_name"
fi
start_production_router "$application_name"
podman exec \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  "$application_name" node --test /e2e/browser-next-conversion-admission.test.mjs

# Restore the ordinary profile runtime before restart and expiry recovery.
podman rm --force "$router_name" >/dev/null
podman rm --force "$application_name" >/dev/null
created=("$application_name" "${created[@]}")
e2e_run_in_harness_directory \
  "$temporary_directory" "$temporary_directory_identity" \
  podman run --detach --name "$application_name" --network "$network_name" \
  --network-alias application --publish 127.0.0.1::8080 \
  "${scanner_host_mapping[@]}" \
  "${hardened_runtime[@]}" "${application_volumes[@]}" "${application_settings[@]}" \
  --env MARKWEAVE_PUBLIC_ORIGIN=http://localhost:3100 \
  "$image" "$application_mode" >/dev/null
if [[ "$profile" == distributed ]]; then
  podman start "$worker_one_name" "$worker_two_name" >/dev/null
fi
wait_for_url "http://127.0.0.1:$(podman port "$application_name" 8080/tcp | sed 's/.*://')/health/ready" \
  "$application_name" '"status":"ready"'
start_production_router "$application_name"
podman exec \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_CONVERSION_STATE=/browser-session/next-conversion.json \
  "$application_name" node --test \
  /e2e/browser-next-conversion-restart-prepare.test.mjs
restart_backend_and_router "$application_name"
podman exec "$application_name" node -e \
  'fetch("http://localhost:3100/api/v1/session").then(r => process.exit(r.status === 401 ? 0 : 1))'
podman exec \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_CONVERSION_STATE=/browser-session/next-conversion.json \
  "$application_name" node --test /e2e/browser-next-conversion-restart.test.mjs

# Keep the T62 durable-result checkpoint inside its deliberate 60-second
# retention window. The longer administration journey runs only after restart
# recovery has proved the original result remains authoritative.
podman exec \
  "$application_name" node --test /e2e/browser-next-admin-cookie.test.mjs
podman exec \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_ARTIFACT_DIR=/browser-artifacts \
  --env MARKWEAVE_E2E_CHECKPOINT_USER_IDLE_MINUTES="$checkpoint_user_idle_minutes" \
  --env MARKWEAVE_E2E_CHECKPOINT_ADMIN_IDLE_MINUTES="$checkpoint_admin_idle_minutes" \
  --env MARKWEAVE_E2E_CHECKPOINT_POLICY_REVISION="$checkpoint_policy_revision" \
  "$application_name" node --test /e2e/browser-next-admin.test.mjs

# Prove asymmetric runtime failures and the custom-server admission boundary
# through the production router against the exact final images.
e2e_podman stop --time 15 "$frontend_name" >/dev/null
e2e_podman exec \
  --env MARKWEAVE_E2E_RUNTIME_FAILURE=frontend-outage \
  "$application_name" node --test /e2e/browser-next-runtime-failures.test.mjs
start_frontend
start_production_router "$application_name" http://127.0.0.1:1 \
  http://frontend:3000 502
e2e_podman exec \
  --env MARKWEAVE_E2E_RUNTIME_FAILURE=backend-outage \
  "$application_name" node --test /e2e/browser-next-runtime-failures.test.mjs

e2e_podman rm --force "$router_name" >/dev/null
e2e_podman rm --force "$frontend_name" >/dev/null
rm -f -- "$evidence_directory"/frontend-*
e2e_run_in_harness_directory \
  "$temporary_directory" "$temporary_directory_identity" \
  podman run --detach --name "$frontend_name" --network "$network_name" \
  --network-alias frontend --user "$runtime_uid:0" --read-only --cap-drop=all \
  --security-opt=no-new-privileges --pids-limit=64 --memory=256m --cpus=0.5 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m \
  --volume "$browser_runtime_directory:/e2e:ro,Z" \
  --volume "$evidence_directory:/evidence:rw,Z" \
  "$frontend_image" node /e2e/frontend-admission-fixture.mjs >/dev/null
for _ in $(seq 1 120); do
  [[ -f "$evidence_directory/frontend-admission-ready" ]] && break
  if [[ "$(podman inspect "$frontend_name" --format '{{.State.Running}}' 2>/dev/null)" != true ]]; then
    e2e_podman logs "$frontend_name" >&2 || true
    break
  fi
  sleep 0.25
done
if [[ ! -f "$evidence_directory/frontend-admission-ready" ]]; then
  echo "Timed out waiting for the admission frontend." >&2
  e2e_podman logs "$frontend_name" >&2 || true
  exit 1
fi
start_production_router "$application_name" http://127.0.0.1:8080 \
  http://frontend:3000 401 false
e2e_podman exec \
  --env MARKWEAVE_E2E_RUNTIME_FAILURE=admission \
  "$application_name" node --test /e2e/browser-next-runtime-failures.test.mjs &
admission_test_pid=$!
# The browser test owns a 25-second pre-admission deadline. Give it five more
# seconds to publish the drain request or exit through its cleanup path.
for _ in $(seq 1 1200); do
  [[ -f "$evidence_directory/frontend-request-drain" ]] && break
  kill -0 "$admission_test_pid" 2>/dev/null || break
  sleep 0.025
done
test -f "$evidence_directory/frontend-request-drain"
e2e_podman kill --signal TERM "$frontend_name" >/dev/null
wait "$admission_test_pid"
test "$(e2e_podman wait "$frontend_name")" = 0
e2e_podman rm "$frontend_name" >/dev/null
start_frontend
start_production_router "$application_name"

# Prove absolute session expiry against the real final image without waiting for
# the administrator policy's approved five-minute minimum. This isolated runtime
# uses the operator-owned two-second absolute ceiling and performs no policy update.
podman rm --force "$router_name" >/dev/null
podman rm --force "$application_name" >/dev/null
created=("$expiry_application_name" "${created[@]}")
e2e_run_in_harness_directory \
  "$temporary_directory" "$temporary_directory_identity" \
  podman run --detach --name "$expiry_application_name" --network "$network_name" \
  --network-alias application --publish 127.0.0.1::8080 \
  "${scanner_host_mapping[@]}" \
  "${hardened_runtime[@]}" "${application_volumes[@]}" "${application_settings[@]}" \
  --env MARKWEAVE_SESSION_ABSOLUTE_SECONDS=2 \
  --env MARKWEAVE_PUBLIC_ORIGIN=http://localhost:3100 \
  "$image" "$application_mode" >/dev/null
expiry_application_port="$(podman port "$expiry_application_name" 8080/tcp | sed 's/.*://')"
expiry_base_url="http://127.0.0.1:$expiry_application_port"
wait_for_url "$expiry_base_url/health/ready" "$expiry_application_name" \
  '"status":"ready"'
uv run python -m tests.e2e.service_workflow verify-session-expiration \
  --base-url "$expiry_base_url" --profile "$profile" \
  --artifact-dir "$temporary_directory/browser-artifacts"
start_production_router "$expiry_application_name"
podman exec \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  "$expiry_application_name" node --test /e2e/browser-next-auth-expiry.test.mjs
podman exec \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  "$expiry_application_name" node --test /e2e/browser-next-conversion-expiry.test.mjs

# Prove the final image's explicit insecure exception without a scanner. The
# published port remains loopback-only even though login origins are ignored.
podman rm --force "$router_name" >/dev/null
podman rm --force "$expiry_application_name" "$clamav_name" >/dev/null
created=("$insecure_application_name" "${created[@]}")
e2e_run_in_harness_directory \
  "$temporary_directory" "$temporary_directory_identity" \
  podman run --detach --name "$insecure_application_name" --network "$network_name" \
  --network-alias application --publish 127.0.0.1::8080 \
  --env MARKWEAVE_INSECURE_EVALUATION_MODE=true \
  "${hardened_runtime[@]}" "${application_volumes[@]}" "${application_settings[@]}" \
  "$image" "$application_mode" >/dev/null
insecure_application_port="$(podman port "$insecure_application_name" 8080/tcp | sed 's/.*://')"
insecure_base_url="http://127.0.0.1:$insecure_application_port"
test "$(podman port "$insecure_application_name" 8080/tcp)" = \
  "127.0.0.1:$insecure_application_port"
wait_for_url "$insecure_base_url/health/ready" "$insecure_application_name" \
  '"status":"ready"'
uv run python -m tests.e2e.service_workflow verify-disabled-login-origin \
  --base-url "$insecure_base_url" --profile "$profile" \
  --artifact-dir "$temporary_directory/browser-artifacts"
podman logs "$insecure_application_name" 2>&1 | \
  grep '"event":"insecure_evaluation_mode_enabled"' >/dev/null

succeeded=true
echo "Final-image $profile E2E workflow passed for $image."
