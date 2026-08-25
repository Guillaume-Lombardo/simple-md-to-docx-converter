#!/usr/bin/env bash
set -euo pipefail
umask 0077

repository="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly repository
readonly state_home="${XDG_STATE_HOME:-$HOME/.local/state}"
readonly state_directory="$state_home/markweave-quickstart"
readonly password_file="$state_directory/password.env"
readonly work_image="$state_directory/work.ext4"
readonly template_file="$state_directory/quickstart-template.docx"
readonly work_bytes=268435456
readonly project="${MARKWEAVE_QUICKSTART_PROJECT:-markweave}"
readonly port="${MARKWEAVE_QUICKSTART_PORT:-8080}"
readonly work_volume="${project}_markweave-work"
readonly blkid=/usr/sbin/blkid
readonly losetup=/usr/sbin/losetup
readonly mkfs_ext4=/usr/sbin/mkfs.ext4
runtime_env=""
initialization_mount=""
work_image_created=false
loop_attached_by_run=false
starting=false
start_succeeded=false
compose_started=false
work_device_current=""

fail() {
  echo "$1" >&2
  exit 1
}

validate_project_and_port() {
  [[ "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || \
    fail "The quickstart project name must contain only lowercase letters, numbers, underscores, and hyphens."
  [[ "$port" =~ ^[0-9]+$ && "$port" -ge 1 && "$port" -le 65535 ]] || \
    fail "The quickstart port must be an integer from 1 through 65535."
}

require_supported_host() {
  local architecture
  local context
  local endpoint
  local operating_system
  local security_options
  [[ "$(uname -s)" == Linux ]] || \
    fail "This loop-backed quickstart requires a Linux host. Docker Desktop is not supported."
  [[ "$(uname -m)" == x86_64 || "$(uname -m)" == amd64 ]] || \
    fail "This quickstart requires an AMD64 Linux host."
  [[ -z "${DOCKER_HOST:-}" ]] || \
    fail "DOCKER_HOST is not supported; use the local Docker Engine Unix socket."
  docker info >/dev/null 2>&1 || \
    fail "A running, directly accessible Docker Engine daemon is required."
  context="$(docker context show)"
  [[ "$context" == default ]] || \
    fail "A remote or non-default Docker context is not supported."
  endpoint="$(docker context inspect "$context" --format '{{ (index .Endpoints "docker").Host }}')"
  [[ "$endpoint" == unix:///var/run/docker.sock ]] || \
    fail "Docker Engine must use the local unix:///var/run/docker.sock endpoint."
  architecture="$(docker info --format '{{.Architecture}}')"
  operating_system="$(docker info --format '{{.OperatingSystem}}')"
  security_options="$(docker info --format '{{json .SecurityOptions}}')"
  [[ "$architecture" == x86_64 || "$architecture" == amd64 ]] || \
    fail "The Docker Engine daemon must use AMD64 architecture."
  [[ "$context" != desktop-linux && "$operating_system" != *"Docker Desktop"* ]] || \
    fail "Docker Desktop is not supported by the loop-backed quickstart."
  [[ "$security_options" != *rootless* ]] || \
    fail "Rootless Docker is not supported by the loop-backed quickstart; use a rootful Docker Engine daemon."
}

validate_private_file() {
  local path="$1"
  [[ -f "$path" && ! -L "$path" && -O "$path" ]] || \
    fail "Refusing unsafe quickstart state file: $path"
}

loop_for_image() {
  local devices
  devices="$(sudo "$losetup" --noheadings --output NAME -j "$work_image")"
  if [[ "$(sed '/^$/d' <<<"$devices" | wc -l)" -gt 1 ]]; then
    fail "The quickstart work image is attached to more than one loop device."
  fi
  sed -n '1p' <<<"$devices" | xargs
}

device_backs_work_image() {
  local device="$1"
  [[ -n "$device" && -b "$device" ]] || return 1
  [[ "$(sudo "$losetup" --noheadings --raw --output BACK-FILE "$device" 2>/dev/null)" == "$work_image" ]]
}

validate_work_volume_labels() {
  [[ "$(docker volume inspect --format '{{ index .Labels "com.docker.compose.project" }}' "$work_volume")" == "$project" ]] || \
    fail "Refusing a work volume with an unexpected Compose project label."
  [[ "$(docker volume inspect --format '{{ index .Labels "com.docker.compose.volume" }}' "$work_volume")" == markweave-work ]] || \
    fail "Refusing a work volume with an unexpected Compose volume label."
}

work_volume_is_used() {
  [[ -n "$(docker container ls --all --quiet --filter "volume=$work_volume")" ]]
}

remove_labeled_work_volume() {
  if ! docker volume inspect "$work_volume" >/dev/null 2>&1; then
    return 0
  fi
  validate_work_volume_labels
  if work_volume_is_used; then
    fail "Refusing to remove a work volume that is still used by a container."
  fi
  docker volume rm "$work_volume" >/dev/null
}

cleanup() {
  local exit_code=$?
  local device=""
  if [[ -n "$initialization_mount" && -d "$initialization_mount" ]]; then
    if mountpoint -q "$initialization_mount"; then
      sudo umount "$initialization_mount" || true
    fi
    rmdir -- "$initialization_mount" 2>/dev/null || true
  fi
  if [[ "$starting" == true && "$start_succeeded" != true ]]; then
    if [[ "$compose_started" == true ]]; then
      compose down --remove-orphans >/dev/null 2>&1 || true
    fi
    if docker volume inspect "$work_volume" >/dev/null 2>&1; then
      if [[ "$(docker volume inspect --format '{{ index .Labels "com.docker.compose.project" }}' "$work_volume")" == "$project" ]] && \
        [[ "$(docker volume inspect --format '{{ index .Labels "com.docker.compose.volume" }}' "$work_volume")" == markweave-work ]] && \
        ! work_volume_is_used; then
        docker volume rm "$work_volume" >/dev/null || exit_code=1
      else
        echo "Preserving an unexpected or in-use work volume after failed startup." >&2
        exit_code=1
      fi
    fi
    if [[ "$loop_attached_by_run" == true && -e "$work_image" ]]; then
      device="$(loop_for_image)"
      if device_backs_work_image "$device"; then
        sudo "$losetup" --detach "$device" || exit_code=1
      fi
    fi
    if [[ "$work_image_created" == true && -e "$work_image" ]]; then
      if [[ -z "$(loop_for_image)" ]]; then
        validate_private_file "$work_image"
        rm -f -- "$work_image"
      else
        echo "Preserving the work image because its loop device is still attached." >&2
        exit_code=1
      fi
    fi
  fi
  if [[ -n "$runtime_env" && -f "$runtime_env" ]]; then
    rm -f -- "$runtime_env"
  fi
  exit "$exit_code"
}
trap cleanup EXIT

prepare_private_state() {
  if [[ -L "$state_directory" ]]; then
    fail "Refusing symbolic-link state directory: $state_directory"
  fi
  mkdir -p -- "$state_directory"
  [[ -d "$state_directory" && -O "$state_directory" ]] || \
    fail "The quickstart state directory must be owned by the current user."
  chmod 0700 -- "$state_directory"
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
    fail "The stored quickstart password file is invalid."
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

prepare_work_image() {
  local application_running="$1"
  local actual_size
  local temporary
  if [[ ! -e "$work_image" ]]; then
    temporary="$(mktemp "$state_directory/work.XXXXXX")"
    truncate -s "$work_bytes" "$temporary"
    chmod 0600 -- "$temporary"
    mv -- "$temporary" "$work_image"
    work_image_created=true
  fi
  validate_private_file "$work_image"
  actual_size="$(stat -c %s -- "$work_image")"
  if [[ "$actual_size" != "$work_bytes" ]]; then
    [[ "$application_running" != true ]] || \
      fail "The running quickstart work image is not exactly 256 MiB."
  fi
}

resize_stopped_work_image() {
  local actual_size
  local device
  local temporary
  actual_size="$(stat -c %s -- "$work_image")"
  [[ "$actual_size" != "$work_bytes" ]] || return 0
  device="$(loop_for_image)"
  if [[ -n "$device" ]]; then
    device_backs_work_image "$device" || \
      fail "Refusing to detach a loop device with an unexpected backing file."
    sudo "$losetup" --detach "$device"
  fi
  temporary="$(mktemp "$state_directory/work.XXXXXX")"
  truncate -s "$work_bytes" "$temporary"
  chmod 0600 -- "$temporary"
  mv -- "$temporary" "$work_image"
  work_image_created=true
  work_device_current=""
  attach_work_image
}

validate_running_work_image() {
  local actual_size
  local filesystem
  actual_size="$(stat -c %s -- "$work_image")"
  [[ "$actual_size" == "$work_bytes" ]] || \
    fail "The running quickstart work image is not exactly 256 MiB."
  filesystem="$(sudo "$blkid" -p -s TYPE -o value -- "$work_image")"
  [[ "$filesystem" == ext4 ]] || fail "The running quickstart work image is not ext4."
}

attach_work_image() {
  work_device_current="$(loop_for_image)"
  if [[ -z "$work_device_current" ]]; then
    work_device_current="$(sudo "$losetup" --find --show "$work_image")"
    loop_attached_by_run=true
  fi
  [[ "$work_device_current" == /dev/loop* ]] || \
    fail "Unexpected loop device: $work_device_current"
  device_backs_work_image "$work_device_current" || \
    fail "The discovered loop device does not belong to the quickstart work image."
}

initialize_work_device() {
  local device="$1"
  initialization_mount="$(mktemp -d "$state_directory/mount.XXXXXX")"
  sudo mount -t ext4 -o rw,nosuid,nodev "$device" "$initialization_mount"
  sudo chown 1001:0 "$initialization_mount"
  sudo chmod 0770 "$initialization_mount"
  sudo umount "$initialization_mount"
  rmdir -- "$initialization_mount"
  initialization_mount=""
}

format_work_device() {
  local device="$1"
  device_backs_work_image "$device" || \
    fail "Refusing to format a loop device that does not belong to the quickstart work image."
  sudo "$mkfs_ext4" -q -F -m 0 -O '^has_journal' "$device"
  initialize_work_device "$device"
}

write_runtime_env() {
  local device="$1"
  local password
  if [[ -n "$runtime_env" && -f "$runtime_env" ]]; then
    rm -f -- "$runtime_env"
  fi
  runtime_env="$(mktemp "$state_directory/compose.XXXXXX")"
  password="$(sed -n 's/^MARKWEAVE_INITIAL_ADMIN_PASSWORD=//p' "$password_file")"
  printf 'MARKWEAVE_INITIAL_ADMIN_PASSWORD=%s\nMARKWEAVE_PORT=%s\nMARKWEAVE_WORK_DEVICE=%s\n' \
    "$password" "$port" "$device" >"$runtime_env"
  chmod 0600 -- "$runtime_env"
}

compose() {
  docker compose --project-name "$project" --project-directory "$repository" \
    --file "$repository/compose.yaml" --env-file "$runtime_env" "$@"
}

application_container() {
  docker container ls --all --quiet \
    --filter "label=com.docker.compose.project=$project" \
    --filter "label=com.docker.compose.service=markweave"
}

application_is_running() {
  local container
  container="$(application_container)"
  [[ -n "$container" ]] || return 1
  [[ "$(docker inspect --format '{{.State.Running}}' "$container")" == true ]]
}

validate_current_work_volume() {
  local device="$1"
  docker volume inspect "$work_volume" >/dev/null 2>&1 || \
    fail "The running quickstart has no work volume. Stop its containers before retrying."
  validate_work_volume_labels
  [[ "$(docker volume inspect --format '{{ index .Options "type" }}' "$work_volume")" == ext4 ]] || \
    fail "The running work volume is not ext4. Stop its containers before retrying."
  [[ "$(docker volume inspect --format '{{ index .Options "o" }}' "$work_volume")" == rw,nosuid,nodev ]] || \
    fail "The running work volume has unexpected mount options. Stop its containers before retrying."
  [[ "$(docker volume inspect --format '{{ index .Options "device" }}' "$work_volume")" == "$device" ]] || \
    fail "The running work volume does not use the loop device owned by this quickstart."
}

start() {
  local application_running=false
  local device
  validate_project_and_port
  require_supported_host
  prepare_private_state
  prepare_password
  prepare_template
  echo "Preparing the 256 MiB ext4 workspace requires sudo for loop-device and filesystem setup."
  sudo -v
  if application_is_running; then
    application_running=true
  fi
  starting=true
  prepare_work_image "$application_running"
  if [[ "$application_running" == true && "$work_image_created" == true ]]; then
    fail "Refusing to reuse a running stack whose private work image is missing."
  fi
  attach_work_image
  device="$work_device_current"
  write_runtime_env "$device"

  if [[ "$application_running" == true ]]; then
    validate_running_work_image
    validate_current_work_volume "$device"
  else
    # A stopped or vanished stack cannot safely retain a mount keyed by /dev/loopN.
    # Remove only this exact Compose project, discard its labeled scratch volume,
    # and rebuild the disposable filesystem through the backing-file association.
    compose down --remove-orphans
    remove_labeled_work_volume
    resize_stopped_work_image
    device="$work_device_current"
    write_runtime_env "$device"
    format_work_device "$device"
  fi

  compose_started=true
  compose up --detach
  start_succeeded=true
  echo "Markweave is starting at http://localhost:$port"
  echo "Template: $template_file"
  echo "Show the administrator password with: scripts/quickstart.sh password"
}

stop() {
  local device=""
  validate_project_and_port
  require_supported_host
  prepare_private_state
  prepare_password
  if [[ -e "$work_image" ]]; then
    validate_private_file "$work_image"
    echo "Cleaning the 256 MiB ext4 workspace requires sudo for loop-device setup."
    sudo -v
    device="$(loop_for_image)"
  fi
  write_runtime_env "${device:-/dev/null}"
  compose down --remove-orphans
  remove_labeled_work_volume
  if [[ -n "$device" ]]; then
    device_backs_work_image "$device" || \
      fail "Refusing to detach a loop device with an unexpected backing file."
    sudo "$losetup" --detach "$device"
  fi
  if [[ -e "$work_image" ]]; then
    validate_private_file "$work_image"
    rm -f -- "$work_image"
  fi
  echo "Stopped Markweave and removed only its disposable work filesystem."
  echo "Application data, ClamAV signatures, password, and template are retained."
}

show_password() {
  prepare_private_state
  prepare_password
  sed -n 's/^MARKWEAVE_INITIAL_ADMIN_PASSWORD=//p' "$password_file"
}

compose_status() {
  local device
  validate_project_and_port
  require_supported_host
  prepare_private_state
  prepare_password
  validate_private_file "$work_image"
  echo "Inspecting the ext4 workspace requires sudo for loop-device lookup."
  sudo -v
  device="$(loop_for_image)"
  [[ -n "$device" ]] || fail "The work image is not attached; run the up command first."
  write_runtime_env "$device"
  compose "$@"
}

case "${1:-}" in
  up)
    [[ $# -eq 1 ]] || fail "usage: scripts/quickstart.sh up"
    start
    ;;
  down)
    [[ $# -eq 1 ]] || fail "usage: scripts/quickstart.sh down"
    stop
    ;;
  password)
    [[ $# -eq 1 ]] || fail "usage: scripts/quickstart.sh password"
    show_password
    ;;
  ps)
    [[ $# -eq 1 ]] || fail "usage: scripts/quickstart.sh ps"
    compose_status ps
    ;;
  logs)
    [[ $# -eq 1 ]] || fail "usage: scripts/quickstart.sh logs"
    compose_status logs --follow clamav
    ;;
  *)
    fail "usage: scripts/quickstart.sh {up|down|password|ps|logs}"
    ;;
esac
