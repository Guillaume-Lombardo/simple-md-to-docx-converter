#!/usr/bin/env bash
set -euo pipefail
umask 0077

repository="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly repository
readonly state_home="${XDG_STATE_HOME:-$HOME/.local/state}"
readonly state_directory="$state_home/markweave-quickstart-simple"
readonly password_file="$state_directory/password.env"
readonly template_file="$state_directory/quickstart-template.docx"
readonly podman_config_file="$state_directory/podman-containers.conf"
readonly project="${MARKWEAVE_SIMPLE_PROJECT:-markweave-simple}"
readonly port="${MARKWEAVE_SIMPLE_PORT:-8080}"
readonly public_origin="${MARKWEAVE_PUBLIC_ORIGIN:-http://localhost:$port}"
readonly work_volume="${project}_markweave-work"
readonly requested_runtime="${MARKWEAVE_SIMPLE_RUNTIME:-auto}"
readonly -a original_arguments=("$@")
runtime_name=""
runtime_command=()
compose_command=()
podman_service_pid=""
podman_socket=""
runtime_env=""
starting=false
start_succeeded=false
compose_started=false
trusted_upstream_antivirus=false

fail() {
  echo "$1" >&2
  exit 1
}

validate_project_and_port() {
  [[ "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || \
    fail "The simple quickstart project name must contain only lowercase letters, numbers, underscores, and hyphens."
  [[ "$port" =~ ^[0-9]+$ && "$port" -ge 1 && "$port" -le 65535 ]] || \
    fail "The simple quickstart port must be an integer from 1 through 65535."
  [[ "$public_origin" != *$'\n'* && "$public_origin" != *$'\r'* ]] || \
    fail "The public origin must be a single-line HTTP origin."
}

select_runtime() {
  local candidate="$requested_runtime"
  if [[ "$candidate" == auto ]]; then
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 && \
      docker compose version >/dev/null 2>&1; then
      candidate=docker
    elif command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1 && \
      podman compose version >/dev/null 2>&1; then
      candidate=podman
    else
      fail "A running Docker Engine with Compose or rootless Podman with Compose is required."
    fi
  fi

  case "$candidate" in
    docker)
      if ! command -v docker >/dev/null 2>&1 || \
        ! docker info >/dev/null 2>&1 || \
        ! docker compose version >/dev/null 2>&1; then
        fail "The selected Docker runtime and Compose provider must be available."
      fi
      runtime_name=docker
      runtime_command=(docker)
      compose_command=(docker compose)
      ;;
    podman)
      if ! command -v podman >/dev/null 2>&1 || \
        ! podman info >/dev/null 2>&1 || \
        ! podman compose version >/dev/null 2>&1; then
        fail "The selected Podman runtime and Compose provider must be available."
      fi
      [[ "$(podman info --format '{{.Host.Security.Rootless}}')" == true ]] || \
        fail "The simple Podman quickstart supports rootless Podman only."
      if [[ "$trusted_upstream_antivirus" == true ]]; then
        command -v slirp4netns >/dev/null 2>&1 || \
          fail "The trusted-upstream Podman quickstart requires slirp4netns."
      fi
      runtime_name=podman
      runtime_command=(podman)
      start_private_podman_service
      compose_command=(podman --url "unix://$podman_socket" compose)
      ;;
    *)
      fail "MARKWEAVE_SIMPLE_RUNTIME must be auto, docker, or podman."
      ;;
  esac
}

validate_private_file() {
  local path="$1"
  [[ -f "$path" && ! -L "$path" && -O "$path" ]] || \
    fail "Refusing unsafe simple-quickstart state file: $path"
}

start_private_podman_service() {
  local oci_runtime
  local temporary
  podman_socket="$state_directory/podman-compose.sock"
  if [[ -e "$podman_socket" || -L "$podman_socket" ]]; then
    [[ -S "$podman_socket" && ! -L "$podman_socket" && -O "$podman_socket" ]] || \
      fail "Refusing an unsafe private Podman socket: $podman_socket"
    rm -f -- "$podman_socket"
  fi
  oci_runtime="$(command -v crun || true)"
  if [[ -z "$oci_runtime" ]]; then
    oci_runtime="$(podman info --format '{{.Host.OCIRuntime.Path}}')"
  fi
  [[ "$oci_runtime" == /* && -x "$oci_runtime" ]] || \
    fail "Podman's OCI runtime must be an executable absolute path."
  temporary="$(mktemp "$state_directory/podman-config.XXXXXX")"
  printf '[containers]\nseccomp_profile="%s"\n[engine]\nruntime="%s"\n' \
    "$repository/spikes/toolchain/chrome-seccomp.json" "$oci_runtime" >"$temporary"
  chmod 0600 -- "$temporary"
  mv -- "$temporary" "$podman_config_file"
  CONTAINERS_CONF="$podman_config_file" \
    podman system service --time=0 "unix://$podman_socket" >/dev/null 2>&1 &
  podman_service_pid=$!
  for _ in $(seq 1 100); do
    [[ -S "$podman_socket" ]] && return 0
    kill -0 "$podman_service_pid" 2>/dev/null || \
      fail "The private Podman API service exited during startup."
    sleep 0.1
  done
  fail "Timed out starting the private Podman API service."
}

prepare_private_state() {
  if [[ -L "$state_directory" ]]; then
    fail "Refusing symbolic-link state directory: $state_directory"
  fi
  mkdir -p -- "$state_directory"
  [[ -d "$state_directory" && -O "$state_directory" ]] || \
    fail "The simple-quickstart state directory must be owned by the current user."
  chmod 0700 -- "$state_directory"
}

lock_private_state() {
  local exit_code
  command -v flock >/dev/null 2>&1 || \
    fail "The simple quickstart requires flock from util-linux."
  [[ "${MARKWEAVE_SIMPLE_STATE_LOCKED:-}" != 1 ]] || return 0
  set +e
  flock --exclusive --nonblock --close --conflict-exit-code 75 \
    "$state_directory" env MARKWEAVE_SIMPLE_STATE_LOCKED=1 \
    "$0" "${original_arguments[@]}"
  exit_code=$?
  set -e
  [[ "$exit_code" != 75 ]] || \
    fail "Another simple quickstart command is already using this state directory."
  exit "$exit_code"
}

prepare_password() {
  local password
  local temporary
  if [[ ! -e "$password_file" ]]; then
    temporary="$(mktemp "$state_directory/password.XXXXXX")"
    password="$(openssl rand -hex 24)"
    printf 'MARKWEAVE_INITIAL_ADMIN_PASSWORD=%s\n' "$password" >"$temporary"
    chmod 0600 -- "$temporary"
    mv -- "$temporary" "$password_file"
  fi
  validate_private_file "$password_file"
  chmod 0600 -- "$password_file"
  password="$(sed -n 's/^MARKWEAVE_INITIAL_ADMIN_PASSWORD=//p' "$password_file")"
  [[ "$password" =~ ^[[:xdigit:]]{48}$ ]] || \
    fail "The stored simple-quickstart password file is invalid."
}

prepare_template() {
  local temporary
  if [[ ! -e "$template_file" ]]; then
    temporary="$(mktemp "$state_directory/template.XXXXXX")"
    openssl base64 -d \
      -in "$repository/examples/quickstart-template.docx.base64" \
      -out "$temporary"
    chmod 0600 -- "$temporary"
    mv -- "$temporary" "$template_file"
  fi
  validate_private_file "$template_file"
}

write_runtime_env() {
  local password
  if [[ -n "$runtime_env" && -f "$runtime_env" ]]; then
    rm -f -- "$runtime_env"
  fi
  runtime_env="$(mktemp "$state_directory/compose.XXXXXX")"
  password="$(sed -n 's/^MARKWEAVE_INITIAL_ADMIN_PASSWORD=//p' "$password_file")"
  printf 'MARKWEAVE_INITIAL_ADMIN_PASSWORD=%s\nMARKWEAVE_PORT=%s\nMARKWEAVE_PUBLIC_ORIGIN=%s\nMARKWEAVE_WORK_DEVICE=/dev/null\n' \
    "$password" "$port" "$public_origin" >"$runtime_env"
  chmod 0600 -- "$runtime_env"
}

compose() {
  local files=(
    --file "$repository/compose.yaml"
    --file "$repository/compose.simple.yaml"
  )
  if [[ "$runtime_name" == podman ]]; then
    files+=(--file "$repository/compose.podman.yaml")
  fi
  if [[ "$trusted_upstream_antivirus" == true ]]; then
    files+=(--file "$repository/compose.trusted-upstream.yaml")
    if [[ "$runtime_name" == podman ]]; then
      files+=(--file "$repository/compose.podman-trusted-upstream.yaml")
    fi
  fi
  "${compose_command[@]}" --project-name "$project" --project-directory "$repository" \
    "${files[@]}" --env-file "$runtime_env" "$@"
}

application_container() {
  "${runtime_command[@]}" container ls --all --quiet \
    --filter "label=com.docker.compose.project=$project" \
    --filter "label=com.docker.compose.service=markweave"
}

scanner_container() {
  "${runtime_command[@]}" container ls --all --quiet \
    --filter "label=com.docker.compose.project=$project" \
    --filter "label=com.docker.compose.service=clamav"
}

application_is_running() {
  local container
  container="$(application_container)"
  [[ -n "$container" ]] || return 1
  [[ "$("${runtime_command[@]}" inspect --format '{{.State.Running}}' "$container")" == true ]]
}

work_volume_matches() {
  local options
  "${runtime_command[@]}" volume inspect "$work_volume" >/dev/null 2>&1 && \
    [[ "$("${runtime_command[@]}" volume inspect --format '{{ index .Labels "com.docker.compose.project" }}' "$work_volume")" == "$project" ]] && \
    [[ "$("${runtime_command[@]}" volume inspect --format '{{ index .Labels "com.docker.compose.volume" }}' "$work_volume")" == markweave-work ]] && \
    [[ "$("${runtime_command[@]}" volume inspect --format '{{.Driver}}' "$work_volume")" == local ]] || return 1
  options="$("${runtime_command[@]}" volume inspect --format '{{json .Options}}' "$work_volume")"
  if [[ "$runtime_name" == docker ]]; then
    [[ "$options" == null ]]
  else
    [[ "$options" == "{}" ]]
  fi
}

validate_work_volume() {
  work_volume_matches || \
    fail "Refusing a missing or unexpected simple-quickstart work volume."
}

work_volume_is_used() {
  [[ -n "$("${runtime_command[@]}" container ls --all --quiet --filter "volume=$work_volume")" ]]
}

remove_work_volume() {
  if ! "${runtime_command[@]}" volume inspect "$work_volume" >/dev/null 2>&1; then
    return 0
  fi
  validate_work_volume
  if work_volume_is_used; then
    fail "Refusing to remove a simple-quickstart work volume that is still used."
  fi
  "${runtime_command[@]}" volume rm "$work_volume" >/dev/null
}

initialize_work_volume() {
  local application_image
  "${runtime_command[@]}" volume create \
    --label "com.docker.compose.project=$project" \
    --label "com.docker.compose.volume=markweave-work" \
    "$work_volume" >/dev/null
  validate_work_volume
  application_image="$(compose config --images | awk \
    '/^ghcr\.io\/guillaume-lombardo\/md-converter:/ { print; exit }')"
  [[ -n "$application_image" ]] || fail "Could not resolve the pinned Markweave image."
  "${runtime_command[@]}" run --rm --network none --read-only --user 0:0 \
    --cap-drop ALL --cap-add CHOWN --security-opt no-new-privileges \
    --volume "$work_volume:/work" --entrypoint /bin/sh "$application_image" -c \
    'chmod 0770 /work && chown 1001:0 /work'
  validate_work_volume
}

wait_for_podman_scanner() {
  local container
  for _ in $(seq 1 240); do
    container="$(scanner_container)"
    if [[ -n "$container" ]] && \
      "${runtime_command[@]}" exec "$container" /usr/local/bin/clamdcheck.sh \
        >/dev/null 2>&1; then
      return 0
    fi
    if [[ -n "$container" ]] && \
      [[ "$("${runtime_command[@]}" inspect --format '{{.State.Status}}' "$container")" == exited ]]; then
      fail "ClamAV exited before becoming ready under Podman."
    fi
    sleep 5
  done
  fail "Timed out waiting for ClamAV readiness under Podman."
}

wait_for_application() {
  local container
  for _ in $(seq 1 120); do
    container="$(application_container)"
    if [[ -n "$container" ]] && \
      "${runtime_command[@]}" exec "$container" python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/ready', timeout=2).read()" \
        >/dev/null 2>&1; then
      return 0
    fi
    if [[ -n "$container" ]] && \
      [[ "$("${runtime_command[@]}" inspect --format '{{.State.Status}}' "$container")" == exited ]]; then
      fail "Markweave exited before becoming ready."
    fi
    sleep 2
  done
  fail "Timed out waiting for Markweave readiness."
}

start_podman_stack() {
  compose up --detach clamav
  wait_for_podman_scanner
  compose up --detach markweave
  wait_for_application
}

start_trusted_upstream_stack() {
  # Remove a scanner left by an earlier default-mode start before recreating the
  # application with its explicit trusted-upstream configuration.
  if [[ -n "$(scanner_container)" ]]; then
    compose rm --stop --force clamav >/dev/null
  fi
  compose up --detach markweave
  wait_for_application
}

cleanup() {
  local exit_code=$?
  if [[ "$starting" == true && "$start_succeeded" != true ]]; then
    if [[ "$compose_started" == true ]]; then
      compose logs --no-color >&2 2>/dev/null || true
      compose down --remove-orphans >/dev/null 2>&1 || true
    fi
    if "${runtime_command[@]}" volume inspect "$work_volume" >/dev/null 2>&1; then
      if work_volume_matches && ! work_volume_is_used; then
        "${runtime_command[@]}" volume rm "$work_volume" >/dev/null || exit_code=1
      else
        echo "Preserving an unexpected or in-use work volume after failed startup." >&2
        exit_code=1
      fi
    fi
  fi
  if [[ -n "$runtime_env" && -f "$runtime_env" ]]; then
    rm -f -- "$runtime_env"
  fi
  if [[ -n "$podman_service_pid" ]] && kill -0 "$podman_service_pid" 2>/dev/null; then
    kill "$podman_service_pid" || exit_code=1
    wait "$podman_service_pid" 2>/dev/null || true
  fi
  if [[ -n "$podman_socket" && -S "$podman_socket" && ! -L "$podman_socket" ]]; then
    rm -f -- "$podman_socket"
  fi
  exit "$exit_code"
}
trap cleanup EXIT

start() {
  local running=false
  validate_project_and_port
  prepare_private_state
  lock_private_state
  select_runtime
  prepare_password
  prepare_template
  write_runtime_env
  compose config --quiet
  if application_is_running; then
    running=true
  fi
  starting=true
  if [[ "$running" == true ]]; then
    validate_work_volume
  else
    compose down --remove-orphans
    remove_work_volume
    compose_started=true
    initialize_work_volume
  fi
  compose_started=true
  if [[ "$trusted_upstream_antivirus" == true ]]; then
    start_trusted_upstream_stack
  elif [[ "$runtime_name" == podman ]]; then
    start_podman_stack
  else
    compose up --detach markweave
    wait_for_application
  fi
  validate_work_volume
  start_succeeded=true
  echo "Markweave is ready with $runtime_name at http://localhost:$port"
  echo "Template: $template_file"
  echo "Warning: the simple /work volume has no physical capacity cap."
  if [[ "$trusted_upstream_antivirus" == true ]]; then
    echo "Warning: Trusted upstream antivirus mode is active; Markweave does not scan uploads."
    echo "Warning: Keep Markweave unreachable except through a proxy that scans every upload."
  fi
  echo "Show the administrator password with: scripts/quickstart-simple.sh password"
}

stop() {
  validate_project_and_port
  prepare_private_state
  lock_private_state
  select_runtime
  prepare_password
  write_runtime_env
  compose down --remove-orphans
  remove_work_volume
  echo "Stopped Markweave and removed only its unbounded disposable work volume."
  echo "Application data, ClamAV signatures, password, and template are retained."
}

show_password() {
  prepare_private_state
  lock_private_state
  prepare_password
  sed -n 's/^MARKWEAVE_INITIAL_ADMIN_PASSWORD=//p' "$password_file"
}

compose_status() {
  validate_project_and_port
  prepare_private_state
  lock_private_state
  select_runtime
  prepare_password
  write_runtime_env
  compose "$@"
}

case "${1:-}" in
  up)
    if [[ $# -eq 2 && "${2:-}" == --trust-upstream-antivirus ]]; then
      trusted_upstream_antivirus=true
    elif [[ $# -ne 1 ]]; then
      fail "usage: scripts/quickstart-simple.sh up [--trust-upstream-antivirus]"
    fi
    start
    ;;
  down)
    [[ $# -eq 1 ]] || fail "usage: scripts/quickstart-simple.sh down"
    stop
    ;;
  password)
    [[ $# -eq 1 ]] || fail "usage: scripts/quickstart-simple.sh password"
    show_password
    ;;
  ps)
    [[ $# -eq 1 ]] || fail "usage: scripts/quickstart-simple.sh ps"
    compose_status ps
    ;;
  logs)
    [[ $# -eq 1 ]] || fail "usage: scripts/quickstart-simple.sh logs"
    compose_status logs --follow markweave clamav
    ;;
  *)
    fail "usage: scripts/quickstart-simple.sh {up [--trust-upstream-antivirus]|down|password|ps|logs}"
    ;;
esac
