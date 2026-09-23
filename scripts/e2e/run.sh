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
readonly published_reverse_attempt_image="${MARKWEAVE_E2E_REVERSE_ATTEMPT_IMAGE:-}"
readonly local_reverse_attempt_image="${MARKWEAVE_E2E_LOCAL_REVERSE_ATTEMPT_IMAGE:-}"
readonly local_image="${MARKWEAVE_E2E_LOCAL_IMAGE:-}"
readonly local_frontend_image="${MARKWEAVE_E2E_LOCAL_FRONTEND_IMAGE:-}"
readonly browser_runner_smoke_only="${MARKWEAVE_E2E_BROWSER_RUNNER_SMOKE_ONLY:-0}"
if [[ "$browser_runner_smoke_only" != 0 && "$browser_runner_smoke_only" != 1 ]]; then
  echo "MARKWEAVE_E2E_BROWSER_RUNNER_SMOKE_ONLY must be 0 or 1." >&2
  exit 2
fi
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
if [[ -n "$published_reverse_attempt_image" ]] &&
  [[ ! "$published_reverse_attempt_image" =~ ^ghcr\.io/guillaume-lombardo/md-converter-reverse-attempt:[0-9]+\.[0-9]+\.[0-9]+@sha256:[0-9a-f]{64}$ ]]; then
  echo "MARKWEAVE_E2E_REVERSE_ATTEMPT_IMAGE must be an immutable version-and-digest image." >&2
  exit 2
fi
if { [[ -n "$published_image" ]] && [[ -z "$published_frontend_image" ]]; } ||
  { [[ -z "$published_image" ]] && [[ -n "$published_frontend_image" ]]; } ||
  { [[ -n "$published_image" ]] && [[ -z "$published_reverse_attempt_image" ]]; } ||
  { [[ -z "$published_image" ]] && [[ -n "$published_reverse_attempt_image" ]]; }; then
  echo "MARKWEAVE_E2E_IMAGE and MARKWEAVE_E2E_FRONTEND_IMAGE must be supplied together with MARKWEAVE_E2E_REVERSE_ATTEMPT_IMAGE." >&2
  exit 2
fi
if [[ -n "$published_image" ]]; then
  backend_version="${published_image%@*}"
  backend_version="${backend_version##*:}"
  frontend_version="${published_frontend_image%@*}"
  frontend_version="${frontend_version##*:}"
  reverse_version="${published_reverse_attempt_image%@*}"
  reverse_version="${reverse_version##*:}"
  if [[ "$backend_version" != "$frontend_version" || "$backend_version" != "$reverse_version" ]]; then
    echo "Published backend, frontend and reverse-attempt E2E image versions must match." >&2
    exit 2
  fi
fi
if { [[ -n "$local_image" ]] && [[ -z "$local_frontend_image" ]]; } ||
  { [[ -z "$local_image" ]] && [[ -n "$local_frontend_image" ]]; } ||
  { [[ -n "$local_image" ]] && [[ -z "$local_reverse_attempt_image" ]]; } ||
  { [[ -z "$local_image" ]] && [[ -n "$local_reverse_attempt_image" ]]; }; then
  echo "MARKWEAVE_E2E_LOCAL_IMAGE and MARKWEAVE_E2E_LOCAL_FRONTEND_IMAGE must be supplied together with MARKWEAVE_E2E_LOCAL_REVERSE_ATTEMPT_IMAGE." >&2
  exit 2
fi
if [[ -n "$published_image" && -n "$local_image" ]]; then
  echo "Published and local E2E image pairs are mutually exclusive." >&2
  exit 2
fi
if [[ -n "$local_image" ]] && {
  [[ ! "$local_image" =~ ^localhost/md-converter:[a-zA-Z0-9_.-]+$ ]] ||
    [[ ! "$local_frontend_image" =~ ^localhost/md-converter-web:[a-zA-Z0-9_.-]+$ ]] ||
    [[ ! "$local_reverse_attempt_image" =~ ^localhost/md-converter-reverse-attempt:[a-zA-Z0-9_.-]+$ ]];
}; then
  echo "Local E2E images must use the isolated localhost Markweave package names." >&2
  exit 2
fi
if [[ -n "$local_image" ]]; then
  backend_version="${local_image##*:}"
  frontend_version="${local_frontend_image##*:}"
  if [[ "$backend_version" != "$frontend_version" || "$backend_version" != "${local_reverse_attempt_image##*:}" ]]; then
    echo "Local backend, frontend and reverse-attempt E2E image versions must match." >&2
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
readonly browser_runner_name="$prefix-browser-runner"
readonly frontend_image="${published_frontend_image:-${local_frontend_image:-localhost/markweave-web:t64-$profile}}"
readonly reverse_attempt_image="${published_reverse_attempt_image:-${local_reverse_attempt_image:-localhost/md-converter-reverse-attempt:t73-$profile}}"
readonly clamav_name="$prefix-clamav"
readonly clamav_probe_name="$prefix-clamav-probe"
readonly composer_provider_name="$prefix-composer-provider"
readonly postgres_name="$prefix-postgres"
readonly rustfs_name="$prefix-rustfs"
readonly worker_one_name="$prefix-worker-1"
readonly worker_two_name="$prefix-worker-2"
readonly runtime_uid="${T21_RUNTIME_UID:-51000}"
readonly artifact_directory="$repository/artifacts/e2e/$profile"
readonly seccomp_profile="$repository/toolchain/document-engines/chrome-seccomp.json"

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
composer_provider_directory="$temporary_directory/composer-provider"
composer_key_file="$temporary_directory/composer-key"
provisioned_username="e2e-provisioned-$profile"
provisioned_initial_password="Provisioned-$profile-initial"
provisioned_renewed_password="Provisioned-$profile-browser-renewed"
provisioned_replacement_password="Provisioned-$profile-replacement"
broker_directory="$temporary_directory/reverse-broker"
broker_pid=""
reverse_pause_pid=""
reverse_diagnostics_pid=""
created=()
succeeded=false
browser_smoke_succeeded=false

# shellcheck source=scripts/e2e/runtime-settings.sh
source "$repository/scripts/e2e/runtime-settings.sh"

remove_artifacts() {
  if [[ "$artifact_directory" != "$repository/artifacts/e2e/$profile" ]]; then
    echo "Refusing to remove an unexpected artifact path." >&2
    return 1
  fi
  rm -rf -- "$artifact_directory"
}

retain_frontend_admission_evidence() {
  local evidence_name evidence_value
  for evidence_name in \
    frontend-admission-ready \
    frontend-admission-high-water \
    frontend-saturated; do
    if [[ ! -f "$evidence_directory/$evidence_name" \
      || -L "$evidence_directory/$evidence_name" ]]; then
      continue
    fi
    evidence_value="$(head -c 16 -- "$evidence_directory/$evidence_name" 2>/dev/null \
      || true)"
    case "$evidence_name" in
      frontend-admission-ready)
        [[ "$evidence_value" == true ]] || continue
        ;;
      frontend-admission-high-water)
        [[ "$evidence_value" =~ ^[0-9]{1,3}$ ]] || continue
        ((10#$evidence_value <= 128)) || continue
        ;;
      frontend-saturated)
        [[ "$evidence_value" == 128 ]] || continue
        ;;
    esac
    printf '%s\n' "$evidence_value" >"$artifact_directory/$evidence_name"
  done
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
  retain_frontend_admission_evidence
  if [[ -f "$broker_directory/process.log" && ! -L "$broker_directory/process.log" ]]; then
    tail -c 16384 -- "$broker_directory/process.log" >"$artifact_directory/reverse-broker.log"
  fi
  printf 'profile=%s\nresult=failed\n' "$profile" >"$artifact_directory/summary.txt"
}

require_reverse_workers_removed() {
  local worker status
  for worker in "$application_name" "$expiry_application_name" \
    "$insecure_application_name" "$worker_one_name" "$worker_two_name"; do
    if podman container exists "$worker"; then
      echo "Reverse worker removal was not proven." >&2
      return 1
    else
      status=$?
      if [[ "$status" -ne 1 ]]; then
        echo "Reverse worker absence could not be inspected." >&2
        return 1
      fi
    fi
  done
}

cleanup() {
  local exit_code=$?
  local resource
  local broker_cleanup_proven=true
  if [[ "$succeeded" != true ]]; then
    if ! collect_failure_artifacts; then
      exit_code=1
    fi
  fi
  for resource in "$reverse_pause_pid" "$reverse_diagnostics_pid"; do
    if [[ -n "$resource" ]]; then
      kill -TERM "$resource" 2>/dev/null || true
      wait "$resource" || true
    fi
  done
  podman rm --force "$browser_runner_name" >/dev/null 2>&1 || true
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
  if [[ -n "$broker_pid" ]]; then
    kill -TERM "$broker_pid" 2>/dev/null || true
    wait "$broker_pid" || exit_code=1
  fi
  if [[ -f "$broker_directory/broker.json" ]]; then
    # No authorized worker may reconnect while startup reconciliation sweeps units.
    if ! require_reverse_workers_removed || \
      ! uv run python -m scripts.e2e.reverse_broker sweep --root "$broker_directory"; then
      echo "Reverse broker cleanup did not prove managed-unit removal." >&2
      exit_code=1
      broker_cleanup_proven=false
    fi
  fi
  if [[ "$succeeded" == true && "$browser_smoke_succeeded" != true ]]; then
    if ! remove_artifacts; then
      exit_code=1
    fi
  fi
  if [[ "$broker_cleanup_proven" == true ]]; then
    if ! e2e_remove_harness_directory \
      "$temporary_directory" "$temporary_directory_identity"; then
      exit_code=1
    fi
  else
    echo "Private harness state retained because broker cleanup was not proven." >&2
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
    "$composer_provider_name" \
    "$postgres_name" "$rustfs_name" \
    "$expiry_application_name" "$insecure_application_name" "$worker_one_name" \
    "$worker_two_name" "$frontend_name" "$router_name" "$browser_runner_name"; do
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

reserve_browser_cgroup_receipt() {
  local test_stem="$1"
  local index=1
  local receipt_directory="$temporary_directory/browser-artifacts"
  local reservation receipt
  while ((index <= 100)); do
    printf -v reservation '%s/.%s-cgroup-%03d.reserved' \
      "$receipt_directory" "$test_stem" "$index"
    printf -v receipt '/browser-artifacts/%s-cgroup-%03d.txt' \
      "$test_stem" "$index"
    if [[ -e "$receipt_directory/${receipt##*/}" ]]; then
      ((index += 1))
      continue
    fi
    if mkdir -m 0700 -- "$reservation" 2>/dev/null; then
      printf '%s\n' "$receipt"
      return 0
    fi
    if [[ ! -e "$reservation" ]]; then
      echo "Could not reserve a browser cgroup receipt." >&2
      return 1
    fi
    ((index += 1))
  done
  echo "Browser cgroup receipt sequence exhausted." >&2
  return 1
}

run_browser_test() {
  local backend_container="$1"
  local test_file="$2"
  local test_stem="${test_file##*/}"
  local app_memory_limit app_cgroup_limit receipt_path
  local -a browser_credentials=()
  shift 2
  [[ "$test_file" =~ ^/e2e/browser-[a-z0-9-]+\.test\.mjs$ || \
    "$test_file" == /e2e/browser-runner-smoke.mjs ]] || {
    echo "Refusing an unexpected browser test path." >&2
    return 1
  }
  if [[ "$test_file" == /e2e/browser-next-composer-real.test.mjs ]]; then
    browser_credentials=(
      --volume "$composer_provider_directory/server.crt:/run/composer-e2e-ca.crt:ro,z"
      --volume "$composer_provider_directory/client.crt:/run/composer-e2e-client.crt:ro,z"
      --volume "$composer_provider_directory/client.key:/run/composer-e2e-client.key:ro,z"
    )
  elif [[ "$test_file" == /e2e/browser-next-composer-resilience.test.mjs ]]; then
    browser_credentials=(
      --volume "$composer_provider_directory/server.crt:/run/composer-e2e-ca.crt:ro,z"
    )
  fi
  test_stem="${test_stem%.mjs}"
  test_stem="${test_stem%.test}"
  if ! receipt_path="$(reserve_browser_cgroup_receipt "$test_stem")"; then
    return 1
  fi
  app_memory_limit="$(podman inspect "$backend_container" --format '{{.HostConfig.Memory}}')"
  if [[ "$app_memory_limit" != 805306368 ]]; then
    echo "The application container did not retain its 768 MiB memory limit." >&2
    return 1
  fi
  app_cgroup_limit="$(podman exec "$backend_container" cat /sys/fs/cgroup/memory.max)"
  if [[ "$app_cgroup_limit" != "$app_memory_limit" ]]; then
    echo "The application memory cgroup differs from its configured limit." >&2
    return 1
  fi
  printf 'Browser runner for %s: application memory.max=%s, runner memory.max=2147483648.\n' \
    "$test_stem" "$app_memory_limit"
  # Override the image entrypoint so this container can only run the selected
  # browser test, never an application or worker process.
  if e2e_run_in_harness_directory \
    "$temporary_directory" "$temporary_directory_identity" \
    timeout --signal=TERM --kill-after=15s 25m \
    podman run --rm --name "$browser_runner_name" \
    --network "container:$backend_container" --user "$runtime_uid:0" \
    --read-only --cap-drop=all --security-opt=no-new-privileges \
    --security-opt="seccomp=$seccomp_profile" \
    --memory=2g --cpus=2 --pids-limit=512 --shm-size=256m \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777 \
    --tmpfs /work:rw,nosuid,nodev,size=256m,mode=0770 \
    --volume "$browser_runtime_directory:/e2e:ro,z" \
    --volume "$node_runtime_directory:/node_modules:ro,z" \
    --volume "$evidence_directory:/evidence:rw,z" \
    --volume "$temporary_directory/browser-artifacts:/browser-artifacts:rw,z" \
    --volume "$browser_session_directory:/browser-session:rw,z" \
    "${browser_credentials[@]}" \
    --env "MARKWEAVE_E2E_APP_MEMORY_LIMIT=$app_cgroup_limit" \
    "$@" --entrypoint /bin/sh "$image" -c '
      evidence="$1"
      test_file="$2"
      # The image entrypoint normally prepares these private browser paths.
      # This driver deliberately skips that entrypoint, so prepare them here.
      umask 0077
      if [ "$HOME" != /work/home ] || [ "$TMPDIR" != /work/tmp ] || \
        [ "$XDG_CACHE_HOME" != /work/xdg/cache ] || \
        [ "$XDG_CONFIG_HOME" != /work/xdg/config ] || \
        [ "$XDG_DATA_HOME" != /work/xdg/data ] || \
        [ "$XDG_RUNTIME_DIR" != /work/xdg/runtime ]; then
        echo "Browser runner environment directories differ from the image contract." >&2
        exit 1
      fi
      for directory in "$HOME" "$TMPDIR" "$XDG_CACHE_HOME" \
        "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_RUNTIME_DIR"; do
        if ! mkdir -p -- "$directory" || ! chmod 0700 -- "$directory"; then
          echo "Could not prepare a private browser runtime directory." >&2
          exit 1
        fi
      done
      memory_limit="$(cat /sys/fs/cgroup/memory.max)"
      if ! printf "application_memory_max=%s\nrunner_memory_max=%s\n" \
        "$MARKWEAVE_E2E_APP_MEMORY_LIMIT" "$memory_limit" > "$evidence"; then
        echo "Could not write the browser runner cgroup limit receipt." >&2
        exit 1
      fi
      if [ "$memory_limit" != 2147483648 ]; then
        echo "Browser runner memory cgroup does not match 2 GiB." >&2
        exit 1
      fi
      node --test "$test_file"
      result=$?
      receipt_failed=0
      if peak="$(cat /sys/fs/cgroup/memory.peak)"; then
        printf "runner_memory_peak=%s\n" "$peak" >> "$evidence" || receipt_failed=1
      else
        receipt_failed=1
      fi
      cat /sys/fs/cgroup/memory.events >> "$evidence" || receipt_failed=1
      if [ "$receipt_failed" -ne 0 ]; then
        echo "Browser runner cgroup peak/events receipt is incomplete (browser status $result)." >&2
        if [ "$result" -eq 0 ]; then exit 1; fi
      fi
      exit "$result"
    ' browser-runner "$receipt_path" "$test_file"; then
    return 0
  else
    local runner_exit=$?
    podman rm --force "$browser_runner_name" >/dev/null 2>&1 || true
    echo "Browser runner $test_stem exited with status $runner_exit." >&2
    return "$runner_exit"
  fi
}

start_production_router() {
  local backend_container="$1"
  local backend_origin="${2:-http://127.0.0.1:8080}"
  local frontend_origin="${3:-}"
  if [[ -z "$frontend_origin" ]]; then
    frontend_origin="$(admission_frontend_origin)"
  fi
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

probe_scanner_outage_routes() {
  local frontend_origin
  frontend_origin="$(admission_frontend_origin)"
  podman exec --env "E2E_FRONTEND_ORIGIN=$frontend_origin" \
    "$application_name" node --input-type=module -e '
const targets = [
  ["router_login", "http://localhost:3100/login"],
  ["frontend_alias", "http://frontend:3000/login"],
  ["frontend_numeric", `${process.env.E2E_FRONTEND_ORIGIN}/login`],
  ["backend_ready", "http://127.0.0.1:8080/health/ready"],
];
const results = await Promise.all(targets.map(async ([name, url]) => {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(2000) });
    return [name, String(response.status)];
  } catch (error) {
    return [name, `error:${error.cause?.code ?? error.name}`];
  }
}));
for (const [name, status] of results)
  console.log(`scanner-outage route ${name} status=${status}`);
if (results.some(([name, status]) => name !== "frontend_alias" && status !== "200"))
  process.exitCode = 1;
'
}

admission_frontend_origin() {
  local frontend_address octet
  local -a address_octets
  frontend_address="$(podman inspect "$frontend_name" --format \
    "{{with index .NetworkSettings.Networks \"$network_name\"}}{{.IPAddress}}{{end}}")"
  if [[ ! "$frontend_address" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Admission frontend has no unambiguous IPv4 address on the E2E network." >&2
    return 1
  fi
  IFS=. read -r -a address_octets <<<"$frontend_address"
  for octet in "${address_octets[@]}"; do
    if [[ ! "$octet" =~ ^[0-9]{1,3}$ ]] || ((10#$octet > 255)); then
      echo "Admission frontend has an invalid IPv4 address." >&2
      return 1
    fi
  done
  printf 'http://%s:3000\n' "$frontend_address"
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

restore_composer_snapshot() {
  # Snapshot only after the browser has created real Composer records. Clear the
  # test profile and restore its full database/object set while the app is stopped.
  podman rm --force "$router_name" >/dev/null
  podman stop --time 15 "$application_name" >/dev/null
  # The envelope key is intentionally outside both storage profiles and must
  # travel with an operator backup to open restored connection credentials.
  podman unshare cp -a -- "$composer_key_file" \
    "$temporary_directory/composer-key-backup"
  if [[ "$profile" == distributed ]]; then
    podman stop --time 15 "$worker_one_name" "$worker_two_name" >/dev/null
    podman exec "$postgres_name" pg_dump --username postgres \
      --dbname md_converter_e2e --format=custom \
      --file=/tmp/md-converter-composer-e2e.dump
    podman cp "$postgres_name:/tmp/md-converter-composer-e2e.dump" \
      "$temporary_directory/composer-postgres.dump"
    uv run python -m scripts.e2e.s3_backup backup \
      --endpoint-url "http://127.0.0.1:$rustfs_port" --region us-east-1 \
      --access-key-id e2eaccess --secret-access-key e2esecret \
      --bucket md-converter-t21 \
      --directory "$temporary_directory/composer-s3-backup"
    podman exec "$postgres_name" dropdb --username postgres md_converter_e2e
    podman exec "$postgres_name" createdb --username postgres md_converter_e2e
    podman cp "$temporary_directory/composer-postgres.dump" \
      "$postgres_name:/tmp/md-converter-composer-e2e.dump"
    podman exec "$postgres_name" pg_restore --username postgres \
      --dbname md_converter_e2e --exit-on-error \
      /tmp/md-converter-composer-e2e.dump
    uv run python -m scripts.e2e.s3_backup restore \
      --endpoint-url "http://127.0.0.1:$rustfs_port" --region us-east-1 \
      --access-key-id e2eaccess --secret-access-key e2esecret \
      --bucket md-converter-t21 \
      --directory "$temporary_directory/composer-s3-backup"
    podman start "$worker_one_name" "$worker_two_name" >/dev/null
  else
    mkdir -m 0700 "$temporary_directory/composer-standalone-backup"
    podman unshare cp -a -- "$data_directory/." \
      "$temporary_directory/composer-standalone-backup/"
    podman unshare find "$data_directory" -mindepth 1 -delete
    podman unshare cp -a -- \
      "$temporary_directory/composer-standalone-backup/." "$data_directory/"
  fi
  podman unshare cp -a -- "$temporary_directory/composer-key-backup" \
    "$composer_key_file"
  podman unshare cmp -- "$temporary_directory/composer-key-backup" \
    "$composer_key_file"
  test "$(podman unshare stat -c '%a' "$composer_key_file")" = 400
  podman start "$application_name" >/dev/null
  wait_for_url "http://127.0.0.1:$(podman port "$application_name" 8080/tcp | sed 's/.*://')/health/ready" \
    "$application_name" '"status":"ready"'
  start_production_router "$application_name"
}

prove_composer_key_loss_continuity() {
  podman rm --force "$router_name" >/dev/null
  podman stop --time 15 "$application_name" >/dev/null
  if [[ "$profile" == distributed ]]; then
    podman stop --time 15 "$worker_one_name" "$worker_two_name" >/dev/null
  fi
  podman unshare sh -c \
    'chmod 0600 "$1"; openssl rand -hex 32 > "$1"; chmod 0400 "$1"' \
    sh "$composer_key_file"
  podman start "$application_name" >/dev/null
  if [[ "$profile" == distributed ]]; then
    podman start "$worker_one_name" "$worker_two_name" >/dev/null
  fi
  wait_for_url "http://127.0.0.1:$(podman port "$application_name" 8080/tcp | sed 's/.*://')/health/ready" \
    "$application_name" '"status":"ready"'
  start_production_router "$application_name"
  run_browser_test "$application_name" /e2e/browser-next-composer-resilience.test.mjs \
    --env MARKWEAVE_E2E_BASE_URL=http://localhost:3100 \
    --env MARKWEAVE_E2E_PROFILE="$profile" \
    --env MARKWEAVE_E2E_COMPOSER_PHASE=key-unavailable \
    --env "MARKWEAVE_E2E_COMPOSER_STATE=/browser-session/composer-$profile.json"
  podman rm --force "$router_name" >/dev/null
  podman stop --time 15 "$application_name" >/dev/null
  if [[ "$profile" == distributed ]]; then
    podman stop --time 15 "$worker_one_name" "$worker_two_name" >/dev/null
  fi
  podman unshare chmod 0600 "$composer_key_file"
  podman unshare cp -a -- "$temporary_directory/composer-key-backup" \
    "$composer_key_file"
  podman unshare cmp -- "$temporary_directory/composer-key-backup" \
    "$composer_key_file"
  podman start "$application_name" >/dev/null
  if [[ "$profile" == distributed ]]; then
    podman start "$worker_one_name" "$worker_two_name" >/dev/null
  fi
  wait_for_url "http://127.0.0.1:$(podman port "$application_name" 8080/tcp | sed 's/.*://')/health/ready" \
    "$application_name" '"status":"ready"'
  start_production_router "$application_name"
  run_browser_test "$application_name" /e2e/browser-next-composer-resilience.test.mjs \
    --env MARKWEAVE_E2E_BASE_URL=http://localhost:3100 \
    --env MARKWEAVE_E2E_PROFILE="$profile" \
    --env MARKWEAVE_E2E_COMPOSER_PHASE=key-restored \
    --env "MARKWEAVE_E2E_COMPOSER_STATE=/browser-session/composer-$profile.json"
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


start_reverse_broker() {
  uv run --directory "$temporary_directory" --project "$repository" \
    python -m markweave.broker.process "$broker_directory/broker.json" \
    >>"$broker_directory/process.log" 2>&1 &
  broker_pid=$!
  uv run python -m scripts.e2e.reverse_broker ready --root "$broker_directory"
}

wait_reverse_marker() {
  local marker="$1" observer="$2"
  for _ in $(seq 1 600); do
    if [[ -f "$marker" ]]; then return 0; fi
    if ! kill -0 "$observer" 2>/dev/null; then
      wait "$observer"
      echo "Reverse fault-injection observer exited without its marker." >&2
      return 1
    fi
    sleep 0.05
  done
  echo "Reverse fault-injection marker timed out." >&2
  return 1
}

run_reverse_lifecycle() {
  local scenario="$1"
  local frontend_outage="${2:-false}"
  local broker_exit=0
  local frontend_running=""
  local lifecycle_url="http://127.0.0.1:$(podman port "$application_name" 8080/tcp | sed 's/.*://')"
  local stem="reverse-$scenario"
  local state="$browser_session_directory/$stem.json"
  local binding="$browser_session_directory/$stem-binding.json"
  local diagnostics_watching="$browser_session_directory/$stem-binding-watching.json"
  local diagnostics="$browser_session_directory/$stem-diagnostics.json"
  local receipt="$browser_session_directory/$stem-result.json"
  local barrier="$broker_directory/$stem-paused.json"
  local runtime="$application_name"
  # Hold execution before admission, then prearm exact-unit observers.
  if [[ "$profile" == standalone ]]; then
    uv run python -m scripts.e2e.reverse_broker signal --root "$broker_directory" \
      --parent-pid "$broker_pid" --signal TERM
    wait "$broker_pid"
    broker_pid=""
    wait_for_url "$lifecycle_url/metrics" "$application_name" 'md_converter_reversion_broker_ready 0'
  else
    runtime="$worker_one_name"
    # Preserve the reconciliation lease during the admission-only hold.
    podman pause "$runtime" >/dev/null
  fi
  uv run python -m tests.e2e.reverse_lifecycle_workflow prepare \
    --base-url "$lifecycle_url" --profile "$profile" --scenario "$scenario" --state-file "$state"
  chmod 0644 "$state"
  (
    podman exec "$application_name" /opt/md-converter/venv/bin/python \
      /e2e/reverse_lifecycle_workflow.py diagnostics --wait-for-recovery-attempt \
      --state-file "/browser-session/$stem.json" --output "/browser-session/$stem-binding.json" \
      --ready-marker "/browser-session/$stem-binding-watching.json" \
      --output-ready-marker "/browser-session/$stem-binding.ready"
  ) &
  reverse_diagnostics_pid=$!
  uv run python -m scripts.e2e.reverse_broker watch-pause --root "$broker_directory" \
    --state "$state" --binding "$binding" --barrier "$barrier" \
    --diagnostics "$temporary_directory/browser-artifacts/$stem-pause-state.json" &
  reverse_pause_pid=$!
  wait_reverse_marker "$diagnostics_watching" "$reverse_diagnostics_pid"
  wait_reverse_marker "${barrier%.json}.ready" "$reverse_pause_pid"
  if [[ "$profile" == standalone ]]; then start_reverse_broker; else podman unpause "$runtime" >/dev/null; fi
  wait_reverse_marker "$barrier" "$reverse_pause_pid"
  wait "$reverse_diagnostics_pid"
  reverse_diagnostics_pid=""
  # The marker proves signed inventory, runtime incarnation and synthetic DB binding.
  if [[ "$frontend_outage" == true ]]; then
    test "$scenario" = broker-restart
    # Kill only the frontend while the exact attempt is held; API/router remain live.
    e2e_podman kill --signal KILL "$frontend_name" >/dev/null
    frontend_running="$(podman inspect "$frontend_name" --format '{{.State.Running}}')"
    echo "Reverse fault injection frontend running after KILL: $frontend_running."
    test "$frontend_running" = false
  fi
  if [[ "$scenario" == worker-restart ]]; then
    podman kill --signal KILL "$runtime" >/dev/null
    test "$(podman inspect "$runtime" --format '{{.State.ExitCode}}')" = 137
  else
    uv run python -m scripts.e2e.reverse_broker signal --root "$broker_directory" \
      --parent-pid "$broker_pid" --signal KILL
    wait "$broker_pid" || broker_exit=$?
    echo "Reverse fault injection broker supervisor exit: $broker_exit."
    test "$broker_exit" = 137
    broker_pid=""
  fi
  echo "Reverse fault injection $scenario verified; releasing the bound attempt."
  touch "${barrier%.json}.release"
  wait "$reverse_pause_pid"
  reverse_pause_pid=""
  if [[ "$scenario" == worker-restart ]]; then podman start "$runtime" >/dev/null; else start_reverse_broker; fi
  wait_for_url "$lifecycle_url/health/ready" "$application_name" '"status":"ready"'
  uv run python -m tests.e2e.reverse_lifecycle_workflow verify \
    --base-url "$lifecycle_url" --profile "$profile" --scenario "$scenario" --state-file "$state"
  chmod 0644 "$state"
  podman exec "$application_name" /opt/md-converter/venv/bin/python \
    /e2e/reverse_lifecycle_workflow.py diagnostics --state-file "/browser-session/$stem.json" \
    --output "/browser-session/$stem-diagnostics.json"
  podman exec "$application_name" chmod 0644 "/browser-session/$stem-diagnostics.json"
  uv run python -m tests.e2e.reverse_lifecycle_workflow verify \
    --base-url "$lifecycle_url" --profile "$profile" --scenario "$scenario" --state-file "$state" \
    --diagnostics-file "$diagnostics" --result-receipt "$receipt"
  chmod 0644 "$receipt"
  if [[ "$frontend_outage" == true ]]; then
    test "$(podman inspect "$frontend_name" --format '{{.State.Running}}')" = false
  fi
  echo "Reverse lifecycle $scenario passed for $profile with persisted fencing evidence."
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
mkdir -m 0755 -- "$composer_provider_directory"
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -noenc \
  -days 1 -sha256 -subj /CN=e2e-llm \
  -addext 'subjectAltName=DNS:e2e-llm' \
  -addext 'basicConstraints=critical,CA:TRUE' \
  -keyout "$composer_provider_directory/server.key" \
  -out "$composer_provider_directory/server.crt" >/dev/null 2>&1
openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -noenc \
  -subj /CN=e2e-composer-client \
  -keyout "$composer_provider_directory/client.key" \
  -out "$composer_provider_directory/client.csr" >/dev/null 2>&1
printf 'extendedKeyUsage=clientAuth\n' >"$composer_provider_directory/client.ext"
openssl x509 -req -in "$composer_provider_directory/client.csr" \
  -CA "$composer_provider_directory/server.crt" \
  -CAkey "$composer_provider_directory/server.key" \
  -CAcreateserial -days 1 -sha256 \
  -extfile "$composer_provider_directory/client.ext" \
  -out "$composer_provider_directory/client.crt" >/dev/null 2>&1
openssl rand -hex 32 >"$composer_key_file"
chmod 0400 "$composer_provider_directory/server.key" \
  "$composer_provider_directory/client.key" "$composer_key_file"
chmod 0444 "$composer_provider_directory/server.crt" \
  "$composer_provider_directory/client.crt"
podman unshare chown "$runtime_uid:0" \
  "$composer_provider_directory/server.key" \
  "$composer_provider_directory/client.key" "$composer_key_file"
cp -a "$repository/tests/e2e" "$browser_runtime_directory"
COREPACK_ENABLE_NETWORK=0 pnpm install --frozen-lockfile --ignore-scripts --filter md-converter-web-tests
cp -a "$repository/node_modules" "$node_runtime_directory"
chmod -R a+rX "$browser_runtime_directory" "$node_runtime_directory"
refuse_existing_resources

test "$(podman info --format '{{.Host.Security.Rootless}}')" = true
if [[ -n "$published_image" ]]; then
  podman pull --quiet "$image"
  test "$(podman image inspect "$image" --format '{{.Digest}}')" = "${image##*@}"
  podman pull --quiet "$frontend_image"
  test "$(podman image inspect "$frontend_image" --format '{{.Digest}}')" = \
    "${frontend_image##*@}"
  podman pull --quiet "$reverse_attempt_image"
  test "$(podman image inspect "$reverse_attempt_image" --format '{{.Digest}}')" = "${reverse_attempt_image##*@}"
elif [[ -n "$local_image" ]]; then
  podman image exists "$reverse_attempt_image"
  podman image exists "$image"
  podman image exists "$frontend_image"
else
  bash scripts/ci/pull-immutable-image.sh "$base_image" "$base_digest"
  bash scripts/container/build.sh "$image"
  podman build --format oci --tag "$frontend_image" --file web/Containerfile .
  bash scripts/container/build-reverse-attempt.sh "$reverse_attempt_image"
fi


backend_runtime_version="$(podman run --rm --network none --read-only --cap-drop all \
  --security-opt no-new-privileges --user "$runtime_uid:0" --entrypoint python \
  "$image" -c 'from importlib.metadata import version; print(version("markweave"))')"
reverse_runtime_version="$(podman run --rm --network none --read-only --cap-drop all \
  --security-opt no-new-privileges --user "$runtime_uid:0" --entrypoint python \
  "$reverse_attempt_image" -c 'from importlib.metadata import version; print(version("markweave"))')"
frontend_runtime_version="$(podman run --rm --network none --read-only --cap-drop all \
  --security-opt no-new-privileges --user "$runtime_uid:0" "$frontend_image" \
  node --input-type=module -e 'import {readProjectVersion} from "./project-version.mjs"; console.log(readProjectVersion())')"
if [[ "$backend_runtime_version" != "$frontend_runtime_version" || \
  "$backend_runtime_version" != "$reverse_runtime_version" ]] || \
  { [[ -n "$published_image" ]] && [[ "$backend_runtime_version" != "$backend_version" ]]; }; then
  echo "Final E2E image contents do not carry one matching package version." >&2
  exit 1
fi

podman network create "$network_name" >/dev/null
created+=("network:$network_name")
scanner_static_address="$(podman network inspect "$network_name" | uv run python -c '
import ipaddress
import json
import sys

networks = json.load(sys.stdin)
if len(networks) != 1:
    raise SystemExit("E2E network inspection must return one network")
for subnet in networks[0].get("subnets", []):
    try:
        network = ipaddress.IPv4Network(subnet["subnet"])
        gateway = ipaddress.IPv4Address(subnet["gateway"])
    except (KeyError, ValueError, TypeError):
        continue
    address = gateway + 1
    if gateway in network and address in network and address != network.broadcast_address:
        print(address)
        break
else:
    raise SystemExit("E2E network has no usable scanner IPv4 address")
')"
readonly scanner_static_address

created=("$clamav_name" "${created[@]}")
e2e_run_in_harness_directory \
  "$temporary_directory" "$temporary_directory_identity" \
  podman run --detach --name "$clamav_name" --network "$network_name" \
  --network-alias e2e-clamav --ip "$scanner_static_address" \
  --read-only --cap-drop=all \
  --env MARKWEAVE_TEST_CLAMAV_REJECT_EICAR=true \
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
if [[ "$clamav_address" != "$scanner_static_address" ]]; then
  echo "Fake ClamAV did not receive its reserved E2E network address." >&2
  exit 1
fi
# The unmapped peer above proves the network alias. Application and worker
# containers use this mapping to avoid later transient Netavark DNS failures.
scanner_host_mapping=(--add-host "e2e-clamav:$clamav_address")

# The provider is a private TLS peer on the harness network. Its certificate
# and the independent Composer encryption key are generated only for this run.
created=("$composer_provider_name" "${created[@]}")
e2e_run_in_harness_directory \
  "$temporary_directory" "$temporary_directory_identity" \
  podman run --detach --name "$composer_provider_name" --network "$network_name" \
  --network-alias e2e-llm --user "$runtime_uid:0" --read-only \
  --cap-drop=all --security-opt=no-new-privileges --pids-limit=64 \
  --memory=128m --cpus=0.5 \
  --volume "$browser_runtime_directory:/e2e:ro,z" \
  --volume "$composer_provider_directory:/provider:ro,z" \
  --entrypoint /opt/md-converter/venv/bin/python \
  "$image" /e2e/composer_https_provider.py >/dev/null
composer_provider_ready=false
for _ in $(seq 1 120); do
  if podman exec "$composer_provider_name" python -c \
    'import socket, ssl; c=ssl.create_default_context(cafile="/provider/server.crt"); s=socket.create_connection(("127.0.0.1",8443),1); c.wrap_socket(s,server_hostname="e2e-llm").close(); c.load_cert_chain("/provider/client.crt","/provider/client.key"); s=socket.create_connection(("127.0.0.1",8444),1); c.wrap_socket(s,server_hostname="e2e-llm").close()' \
    >/dev/null 2>&1; then
    composer_provider_ready=true
    break
  fi
  if [[ "$(podman inspect "$composer_provider_name" --format '{{.State.Running}}' 2>/dev/null)" != true ]]; then
    podman logs "$composer_provider_name" >&2 || true
    exit 1
  fi
  sleep 0.25
done
if [[ "$composer_provider_ready" != true ]]; then
  echo "Timed out waiting for the Composer HTTPS provider." >&2
  exit 1
fi
composer_provider_address="$(podman inspect "$composer_provider_name" \
  --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')"
if [[ ! "$composer_provider_address" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Composer E2E provider has no IPv4 address." >&2
  exit 1
fi
scanner_host_mapping+=(--add-host "e2e-llm:$composer_provider_address")

reverse_digest="$(podman image inspect "$reverse_attempt_image" --format '{{.Digest}}')"
if [[ ! "$reverse_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "The reverse-attempt image has no immutable manifest digest." >&2
  exit 1
fi
reverse_repository="${reverse_attempt_image%@*}"
reverse_repository="${reverse_repository%:*}"
broker_worker_host="$(podman exec "$clamav_name" python -c 'import socket; print(socket.gethostbyname("host.containers.internal"))')"
uv run python -m scripts.e2e.reverse_broker prepare --root "$broker_directory" \
  --image-repository "$reverse_repository" --image-digest "$reverse_digest" \
  --worker-host "$broker_worker_host"
# Client keys are owner-only inside arbitrary-UID containers; host broker keys never mount.
podman unshare chown -R "$runtime_uid:0" "$broker_directory/worker"
uv run --directory "$temporary_directory" --project "$repository" \
  python -m markweave.broker.process "$broker_directory/broker.json" \
  >"$broker_directory/process.log" 2>&1 &
broker_pid=$!
uv run python -m scripts.e2e.reverse_broker ready --root "$broker_directory"
e2e_reverse_runtime_settings
reverse_worker_runtime=(
  "${REVERSE_E2E_SETTINGS[@]}"
  --env-file "$broker_directory/worker.env"
  --volume "$broker_directory/worker:/run/reverse-client:ro,z"
)

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

# Browser-only fixture and receipt directories are shared with the separate
# browser driver. Keep application data and credential mounts private below.
application_volumes=(
  --volume "$browser_runtime_directory:/e2e:ro,z"
  --volume "$node_runtime_directory:/node_modules:ro,z"
  --volume "$evidence_directory:/evidence:rw,z"
  --volume "$temporary_directory/browser-artifacts:/browser-artifacts:rw,z"
  --volume "$browser_session_directory:/browser-session:rw,z"
  --volume "$provisioning_file:/run/secrets/users.csv:ro,Z"
  --volume "$composer_key_file:/run/secrets/composer-key:ro,Z"
  --volume "$composer_provider_directory/server.crt:/run/composer-e2e-ca.crt:ro,z"
  --volume "$composer_provider_directory/client.crt:/run/composer-e2e-client.crt:ro,z"
  --volume "$composer_provider_directory/client.key:/run/composer-e2e-client.key:ro,z"
)
if [[ "$profile" == standalone ]]; then
  application_volumes+=(--volume "$data_directory:/data:rw,Z" "${reverse_worker_runtime[@]}")
fi

application_mode=serve
application_settings=(
  "${E2E_SETTINGS[@]}"
  --env MARKWEAVE_USER_PROVISIONING_FILE=/run/secrets/users.csv
  --env MARKWEAVE_COMPOSER_ENABLED=true
  --env MARKWEAVE_COMPOSER_SECRET_KEY_PATH=/run/secrets/composer-key
  --env MARKWEAVE_COMPOSER_UPLOAD_MAX_BYTES=1000000
  --env MARKWEAVE_COMPOSER_HTTP_REQUEST_MAX_BYTES=1100000
  --env MARKWEAVE_COMPOSER_MAXIMUM_REQUEST_BYTES=131072
  --env MARKWEAVE_COMPOSER_MAXIMUM_RESPONSE_BYTES=262144
  --env MARKWEAVE_COMPOSER_MAXIMUM_MODELS=32
  --env MARKWEAVE_COMPOSER_MAXIMUM_ALLOWED_USERS=32
  --env MARKWEAVE_COMPOSER_MAXIMUM_MODEL_NAME_LENGTH=128
  --env MARKWEAVE_COMPOSER_MAXIMUM_CREDENTIAL_BYTES=16384
  --env MARKWEAVE_COMPOSER_MAXIMUM_OUTPUT_TOKENS=256
  --env MARKWEAVE_COMPOSER_MAXIMUM_CONCURRENT_CALLS=1
  --env MARKWEAVE_COMPOSER_RETRY_AFTER_SECONDS=2
  --env MARKWEAVE_COMPOSER_TIMEOUT_SECONDS=3
  --env MARKWEAVE_COMPOSER_PENDING_PUBLICATION_STALE_SECONDS=60
  --env MARKWEAVE_COMPOSER_DRAFT_RETENTION_SECONDS=86400
)
if [[ "$profile" == standalone ]]; then
  application_settings+=(
    --env MARKWEAVE_COMPOSER_ADMIN_POLICY_DELEGATED=true
    --env 'MARKWEAVE_COMPOSER_ALLOWED_DESTINATIONS=[]'
    --env 'MARKWEAVE_COMPOSER_ALLOWED_NETWORKS=[]'
  )
else
  application_settings+=(
    --env 'MARKWEAVE_COMPOSER_ALLOWED_DESTINATIONS=["e2e-llm:8443","e2e-llm:8444"]'
    --env "MARKWEAVE_COMPOSER_ALLOWED_NETWORKS=[\"$composer_provider_address/32\"]"
  )
fi
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
    # The broker grants one principal-exclusive reverse supervisor. The second
    # worker retains forward capacity and never receives broker credentials.
    worker_reverse_runtime=()
    if [[ "$worker" == "$worker_one_name" ]]; then
      worker_reverse_runtime=("${reverse_worker_runtime[@]}")
    fi
    created=("$worker" "${created[@]}")
    e2e_run_in_harness_directory \
      "$temporary_directory" "$temporary_directory_identity" \
      podman run --detach --name "$worker" --network "$network_name" \
      "${scanner_host_mapping[@]}" \
      --publish 127.0.0.1::9464 "${hardened_runtime[@]}" "${E2E_SETTINGS[@]}" \
      "${worker_reverse_runtime[@]}" \
      "$image" worker >/dev/null
  done
fi

application_port="$(podman port "$application_name" 8080/tcp | sed 's/.*://')"
base_url="http://127.0.0.1:$application_port"
wait_for_url "$base_url/health/ready" "$application_name" '"status":"ready"'
if [[ "$browser_runner_smoke_only" == 1 ]]; then
  # This opt-in probe starts only the exact frontend/router pair and browser
  # driver, then exits before service workflows and checkpoint mutations.
  created=("$router_name" "$frontend_name" "${created[@]}")
  start_frontend
  start_production_router "$application_name"
  run_browser_test "$application_name" /e2e/browser-runner-smoke.mjs \
    --env MARKWEAVE_E2E_BASE_URL=http://localhost:3100
  podman unshare chown -R 0:0 -- "$temporary_directory/browser-artifacts"
  test -s "$temporary_directory/browser-artifacts/browser-runner-smoke.png"
  test -s "$temporary_directory/browser-artifacts/browser-runner-smoke-cgroup-001.txt"
  mkdir -p -- "$artifact_directory"
  cp -a -- "$temporary_directory/browser-artifacts/browser-runner-smoke.png" \
    "$temporary_directory/browser-artifacts/browser-runner-smoke-cgroup-001.txt" \
    "$artifact_directory/"
  printf 'profile=%s\nresult=browser-runner-smoke-passed\n' "$profile" \
    >"$artifact_directory/summary.txt"
  browser_smoke_succeeded=true
  succeeded=true
  echo "Final-image $profile browser runner smoke passed; evidence: $artifact_directory."
  exit 0
fi
bash "$repository/scripts/container/wait-for-fake-clamav.sh" \
  "$clamav_name" "$profile-mapped" "$application_name" e2e-clamav
podman exec "$application_name" python \
  /e2e/engine_network_isolation_final_image.py
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

uv run python -m tests.e2e.reverse_workflow --base-url "$base_url" --profile "$profile"
uv run python -m tests.e2e.reverse_cli_workflow \
  --container "$application_name" --profile "$profile" --phase primary
if [[ "${MARKWEAVE_E2E_REVERSE_PRIMARY_ONLY:-false}" == true ]]; then
  echo "Reverse primary-path diagnostic passed for $profile; full qualification is not claimed."
  succeeded=true
  exit 0
fi

uv run python -m tests.e2e.reverse_corpus_workflow --base-url "$base_url" --profile "$profile"
uv run python -m tests.e2e.structured_pptx_workflow --base-url "$base_url" --profile "$profile"

run_reverse_lifecycle worker-restart

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
run_browser_test "$application_name" /e2e/browser-provisioning-restart.test.mjs \
  --env MARKWEAVE_E2E_BASE_URL=http://localhost:3100 \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_PROVISIONED_USERNAME="$provisioned_username" \
  --env MARKWEAVE_E2E_PROVISIONED_OLD_PASSWORD="$provisioned_renewed_password" \
  --env MARKWEAVE_E2E_PROVISIONED_PASSWORD="$provisioned_replacement_password"
run_browser_test "$application_name" /e2e/browser-recovery-checkpoint.test.mjs \
  --env MARKWEAVE_E2E_BASE_URL=http://localhost:3100 \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_RECOVERY_STATE=/browser-session/admin.json \
  --env MARKWEAVE_E2E_ARTIFACT_DIR=/browser-artifacts \
  --env MARKWEAVE_E2E_ADMIN_USERNAME=e2e-admin \
  --env MARKWEAVE_E2E_ADMIN_PASSWORD=e2e-admin-password
kill_backend_and_reconnect_router "$application_name"
run_browser_test "$application_name" /e2e/browser-recovery.test.mjs \
  --env MARKWEAVE_E2E_BASE_URL=http://localhost:3100 \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_RECOVERY_STATE=/browser-session/admin.json \
  --env MARKWEAVE_E2E_ARTIFACT_DIR=/browser-artifacts
run_browser_test "$application_name" /e2e/browser-next-auth.test.mjs \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_ARTIFACT_DIR=/browser-artifacts
run_browser_test "$application_name" /e2e/browser-next-composer-connections.test.mjs \
  --env MARKWEAVE_E2E_BASE_URL=http://localhost:3100 \
  --env MARKWEAVE_E2E_PROFILE="$profile"
run_browser_test "$application_name" /e2e/browser-next-composer-real.test.mjs \
  --env MARKWEAVE_E2E_BASE_URL=http://localhost:3100 \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env "MARKWEAVE_E2E_COMPOSER_PROVIDER_ADDRESS=$composer_provider_address" \
  --env "MARKWEAVE_E2E_COMPOSER_STATE=/browser-session/composer-$profile.json"
run_browser_test "$application_name" /e2e/browser-next-composer-pairing.test.mjs \
  --env MARKWEAVE_E2E_BASE_URL=http://localhost:3100 \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env "MARKWEAVE_E2E_COMPOSER_STATE=/browser-session/composer-$profile.json"
if podman logs "$application_name" 2>&1 | \
  grep -Fq 'composer-e2e-write-only-secret'; then
  echo "Composer credential appeared in application logs." >&2
  exit 1
fi
if podman logs "$composer_provider_name" 2>&1 | \
  grep -Fq 'composer-e2e-write-only-secret'; then
  echo "Composer credential appeared in provider logs." >&2
  exit 1
fi
provider_events="$(podman logs "$composer_provider_name")"
if [[ "$(grep -Fc '"operation": "chat", "status": 503' <<<"$provider_events")" -ne 2 || \
  "$(grep -Fc '"operation": "chat", "status": 200' <<<"$provider_events")" -ne 10 || \
  "$(grep -Fc '"operation": "authorization", "status": 401' <<<"$provider_events")" -ne 2 ]]; then
  echo "Composer HTTPS provider did not observe the expected outage and retry calls." >&2
  exit 1
fi
podman stop --time 5 "$clamav_name" >/dev/null
probe_scanner_outage_routes
if ! run_browser_test "$application_name" /e2e/browser-next-composer-resilience.test.mjs \
  --env MARKWEAVE_E2E_BASE_URL=http://localhost:3100 \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_COMPOSER_PHASE=scanner-unavailable \
  --env "MARKWEAVE_E2E_COMPOSER_STATE=/browser-session/composer-$profile.json"; then
  probe_scanner_outage_routes || true
  exit 1
fi
podman start "$clamav_name" >/dev/null
restarted_clamav_address="$(podman inspect "$clamav_name" \
  --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')"
if [[ "$restarted_clamav_address" != "$clamav_address" ]]; then
  echo "Fake ClamAV changed its reserved E2E network address after restart." >&2
  exit 1
fi
bash "$repository/scripts/container/wait-for-fake-clamav.sh" \
  "$clamav_name" "$profile-resumed" "$application_name" e2e-clamav
wait_for_url "http://127.0.0.1:$(podman port "$application_name" 8080/tcp | sed 's/.*://')/health/ready" \
  "$application_name" '"status":"ready"'
restore_composer_snapshot
run_browser_test "$application_name" /e2e/browser-next-composer-resilience.test.mjs \
  --env MARKWEAVE_E2E_BASE_URL=http://localhost:3100 \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_COMPOSER_PHASE=restored-backup \
  --env "MARKWEAVE_E2E_COMPOSER_STATE=/browser-session/composer-$profile.json"
prove_composer_key_loss_continuity
provider_events="$(podman logs "$composer_provider_name")"
if [[ "$(grep -Fc '"operation": "chat", "status": 200' <<<"$provider_events")" -ne 15 ]]; then
  echo "Restored Composer calls, cancelled step, replacement step, and occupying connection test did not reach the provider." >&2
  exit 1
fi
if podman logs "$application_name" 2>&1 | \
  grep -Fq 'composer-e2e-write-only-secret'; then
  echo "Restored Composer credential appeared in application logs." >&2
  exit 1
fi
run_browser_test "$application_name" /e2e/browser-next-conversion.test.mjs \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_ARTIFACT_DIR=/browser-artifacts \
  --env MARKWEAVE_E2E_CONVERSION_STATE=/browser-session/next-conversion.json
run_browser_test "$application_name" /e2e/browser-next-conversion-failure.test.mjs \
  --env MARKWEAVE_E2E_PROFILE="$profile"
run_browser_test "$application_name" /e2e/browser-next-reversion.test.mjs \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_REVERSE_PHASE=primary
uv run python -m tests.e2e.reverse_cli_workflow \
  --container "$application_name" --profile "$profile" --phase structured-pptx
podman cp "$application_name:/tmp/markweave-t83-edited.pptx" \
  "$evidence_directory/markweave-t83-edited.pptx"
chmod a+r "$evidence_directory/markweave-t83-edited.pptx"
run_browser_test "$application_name" /e2e/browser-next-reversion.test.mjs \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_REVERSE_PHASE=structured \
  --env MARKWEAVE_E2E_STRUCTURED_PPTX_SOURCE=/evidence/markweave-t83-edited.pptx
run_browser_test "$application_name" /e2e/browser-next-presentations.test.mjs
run_browser_test "$application_name" /e2e/browser-workspace-ui.test.mjs

uv run python -m tests.e2e.reverse_cli_workflow \
  --container "$application_name" --profile "$profile" --phase expiry
uv run python -m tests.e2e.reverse_cli_workflow \
  --container "$application_name" --profile "$profile" --phase unavailable

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
  --env MARKWEAVE_REVERSION_ACTIVE_LIMIT_PER_USER=2 \
  --env MARKWEAVE_REVERSION_UPLOAD_MAX_BYTES=1024 \
  --env MARKWEAVE_JOB_GLOBAL_QUEUE_CAPACITY=3 \
  --env MARKWEAVE_WORKER_IDLE_POLL_SECONDS=600 \
  "$image" "$application_mode" >/dev/null
wait_for_url "http://127.0.0.1:$(podman port "$application_name" 8080/tcp | sed 's/.*://')/health/ready" \
  "$application_name" '"status":"ready"'
if [[ "$profile" == standalone ]]; then
  wait_for_embedded_worker_idle "$application_name"
fi
start_production_router "$application_name"
run_browser_test "$application_name" /e2e/browser-next-composer-resilience.test.mjs \
  --env MARKWEAVE_E2E_BASE_URL=http://localhost:3100 \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_COMPOSER_PHASE=restart \
  --env "MARKWEAVE_E2E_COMPOSER_STATE=/browser-session/composer-$profile.json"
run_browser_test "$application_name" /e2e/browser-next-conversion-admission.test.mjs \
  --env MARKWEAVE_E2E_PROFILE="$profile"

uv run python -m tests.e2e.conversion_cli_workflow \
  --container "$application_name" --profile "$profile" --reverse-held-queue --reverse-upload-max-bytes 1024
run_browser_test "$application_name" /e2e/browser-next-reversion.test.mjs \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_REVERSE_PHASE=admission

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
run_browser_test "$application_name" /e2e/browser-next-conversion-restart-prepare.test.mjs \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_CONVERSION_STATE=/browser-session/next-conversion.json
restart_backend_and_router "$application_name"
podman exec "$application_name" node -e \
  'fetch("http://localhost:3100/api/v1/session").then(r => process.exit(r.status === 401 ? 0 : 1))'
run_browser_test "$application_name" /e2e/browser-next-conversion-restart.test.mjs \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_CONVERSION_STATE=/browser-session/next-conversion.json

# Keep the T62 durable-result checkpoint inside its deliberate 60-second
# retention window. The longer administration journey runs only after restart
# recovery has proved the original result remains authoritative.
run_browser_test "$application_name" /e2e/browser-next-admin-cookie.test.mjs
run_browser_test "$application_name" /e2e/browser-next-admin.test.mjs \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_ARTIFACT_DIR=/browser-artifacts \
  --env MARKWEAVE_E2E_CHECKPOINT_USER_IDLE_MINUTES="$checkpoint_user_idle_minutes" \
  --env MARKWEAVE_E2E_CHECKPOINT_ADMIN_IDLE_MINUTES="$checkpoint_admin_idle_minutes" \
  --env MARKWEAVE_E2E_CHECKPOINT_POLICY_REVISION="$checkpoint_policy_revision"

# Prove asymmetric runtime failures and the custom-server admission boundary
# through the production router against the exact final images.
run_reverse_lifecycle broker-restart true
run_browser_test "$application_name" /e2e/browser-next-runtime-failures.test.mjs \
  --env MARKWEAVE_E2E_RUNTIME_FAILURE=frontend-outage
start_frontend
start_production_router "$application_name"
run_browser_test "$application_name" /e2e/browser-next-reversion.test.mjs \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_REVERSE_PHASE=recovered \
  --env MARKWEAVE_E2E_REVERSE_RECOVERY_STATE=/browser-session/reverse-broker-restart.json \
  --env MARKWEAVE_E2E_REVERSE_RESULT_RECEIPT=/browser-session/reverse-broker-restart-result.json
start_production_router "$application_name" http://127.0.0.1:1 \
  "$(admission_frontend_origin)" 502
run_browser_test "$application_name" /e2e/browser-next-runtime-failures.test.mjs \
  --env MARKWEAVE_E2E_RUNTIME_FAILURE=backend-outage

e2e_podman rm --force "$router_name" >/dev/null
e2e_podman rm --force "$frontend_name" >/dev/null
rm -f -- "$evidence_directory"/frontend-*
e2e_run_in_harness_directory \
  "$temporary_directory" "$temporary_directory_identity" \
  podman run --detach --name "$frontend_name" --network "$network_name" \
  --network-alias frontend --user "$runtime_uid:0" --read-only --cap-drop=all \
  --security-opt=no-new-privileges --pids-limit=64 --memory=256m --cpus=0.5 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m \
  --volume "$browser_runtime_directory:/e2e:ro,z" \
  --volume "$evidence_directory:/evidence:rw,z" \
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
admission_origin="$(admission_frontend_origin)"
start_production_router "$application_name" http://127.0.0.1:8080 \
  "$admission_origin" 401 false
run_browser_test "$application_name" /e2e/browser-next-runtime-failures.test.mjs \
  --env MARKWEAVE_E2E_RUNTIME_FAILURE=admission &
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
run_browser_test "$expiry_application_name" /e2e/browser-next-auth-expiry.test.mjs \
  --env MARKWEAVE_E2E_PROFILE="$profile"
run_browser_test "$expiry_application_name" /e2e/browser-next-conversion-expiry.test.mjs \
  --env MARKWEAVE_E2E_PROFILE="$profile"

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
