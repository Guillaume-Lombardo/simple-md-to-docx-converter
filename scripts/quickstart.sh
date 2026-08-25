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
readonly work_bytes=335544320
readonly project=markweave
readonly work_volume=markweave_markweave-work
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

cleanup() {
  local exit_code=$?
  local device=""
  if [[ -n "$initialization_mount" && -d "$initialization_mount" ]]; then
    if mountpoint -q "$initialization_mount"; then
      sudo umount "$initialization_mount" || true
    fi
    rmdir -- "$initialization_mount" 2>/dev/null || true
  fi
  if [[ "$starting" == true && "$start_succeeded" != true && "$loop_attached_by_run" == true ]]; then
    device="$(loop_for_image)"
    if [[ "$compose_started" == true ]]; then
      compose down --remove-orphans >/dev/null 2>&1 || true
    fi
    if docker volume inspect "$work_volume" >/dev/null 2>&1; then
      if [[ "$(docker volume inspect --format '{{ index .Labels "com.docker.compose.project" }}' "$work_volume")" == "$project" ]] && \
        [[ "$(docker volume inspect --format '{{ index .Labels "com.docker.compose.volume" }}' "$work_volume")" == markweave-work ]] && \
        [[ "$(docker volume inspect --format '{{ index .Options "type" }}' "$work_volume")" == ext4 ]] && \
        [[ "$(docker volume inspect --format '{{ index .Options "o" }}' "$work_volume")" == rw,nosuid,nodev ]] && \
        [[ "$(docker volume inspect --format '{{ index .Options "device" }}' "$work_volume")" == "$device" ]]; then
        docker volume rm "$work_volume" >/dev/null || exit_code=1
      else
        echo "Preserving an unexpected work volume after failed startup." >&2
        exit_code=1
      fi
    fi
    if [[ -n "$device" && "$(sudo "$losetup" --noheadings --raw --output BACK-FILE "$device")" == "$work_image" ]]; then
      sudo "$losetup" --detach "$device" || exit_code=1
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

validate_private_file() {
  local path="$1"
  [[ -f "$path" && ! -L "$path" && -O "$path" ]] || \
    fail "Refusing unsafe quickstart state file: $path"
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
  local actual_size
  local filesystem
  local temporary
  if [[ ! -e "$work_image" ]]; then
    temporary="$(mktemp "$state_directory/work.XXXXXX")"
    truncate -s "$work_bytes" "$temporary"
    "$mkfs_ext4" -q -F -m 0 -O '^has_journal' "$temporary"
    chmod 0600 -- "$temporary"
    mv -- "$temporary" "$work_image"
    work_image_created=true
  fi
  validate_private_file "$work_image"
  actual_size="$(stat -c %s -- "$work_image")"
  [[ "$actual_size" == "$work_bytes" ]] || \
    fail "The quickstart work image is not exactly 320 MiB."
  filesystem="$(sudo "$blkid" -p -s TYPE -o value -- "$work_image")"
  [[ "$filesystem" == ext4 ]] || fail "The quickstart work image is not ext4."
}

initialize_work_device() {
  local device="$1"
  [[ "$work_image_created" == true || "$loop_attached_by_run" == true ]] || return 0
  initialization_mount="$(mktemp -d "$state_directory/mount.XXXXXX")"
  sudo mount -t ext4 -o rw,nosuid,nodev "$device" "$initialization_mount"
  sudo chown 1001:0 "$initialization_mount"
  sudo chmod 0770 "$initialization_mount"
  sudo umount "$initialization_mount"
  rmdir -- "$initialization_mount"
  initialization_mount=""
}

loop_for_image() {
  local devices
  devices="$(sudo "$losetup" --noheadings --output NAME -j "$work_image")"
  if [[ "$(sed '/^$/d' <<<"$devices" | wc -l)" -gt 1 ]]; then
    fail "The quickstart work image is attached to more than one loop device."
  fi
  sed -n '1p' <<<"$devices" | xargs
}

attach_work_image() {
  work_device_current="$(loop_for_image)"
  if [[ -z "$work_device_current" ]]; then
    work_device_current="$(sudo "$losetup" --find --show "$work_image")"
    loop_attached_by_run=true
  fi
  [[ "$work_device_current" == /dev/loop* ]] || \
    fail "Unexpected loop device: $work_device_current"
}

write_runtime_env() {
  local device="$1"
  local password
  runtime_env="$(mktemp "$state_directory/compose.XXXXXX")"
  password="$(sed -n 's/^MARKWEAVE_INITIAL_ADMIN_PASSWORD=//p' "$password_file")"
  printf 'MARKWEAVE_INITIAL_ADMIN_PASSWORD=%s\nMARKWEAVE_WORK_DEVICE=%s\n' \
    "$password" "$device" >"$runtime_env"
  chmod 0600 -- "$runtime_env"
}

compose() {
  docker compose --project-directory "$repository" \
    --file "$repository/compose.yaml" --env-file "$runtime_env" "$@"
}

validate_work_volume() {
  validate_work_volume_labels
  [[ "$(docker volume inspect --format '{{ index .Options "type" }}' "$work_volume")" == ext4 ]] || \
    fail "The existing work volume is not the bounded ext4 volume. Run scripts/quickstart.sh down, then retry."
  [[ "$(docker volume inspect --format '{{ index .Options "o" }}' "$work_volume")" == rw,nosuid,nodev ]] || \
    fail "The existing work volume has unexpected mount options. Run scripts/quickstart.sh down, then retry."
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

reconcile_work_volume() {
  local device="$1"
  local configured_device
  if ! docker volume inspect "$work_volume" >/dev/null 2>&1; then
    return 0
  fi
  validate_work_volume_labels
  configured_device="$(docker volume inspect --format '{{ index .Options "device" }}' "$work_volume")"
  if [[ "$(docker volume inspect --format '{{ index .Options "type" }}' "$work_volume")" == ext4 ]] && \
    [[ "$(docker volume inspect --format '{{ index .Options "o" }}' "$work_volume")" == rw,nosuid,nodev ]] && \
    [[ "$configured_device" == "$device" ]]; then
    return 0
  fi
  if work_volume_is_used; then
    fail "The obsolete work volume is still used by a container. Run scripts/quickstart.sh down, then retry."
  fi
  docker volume rm "$work_volume" >/dev/null
}

start() {
  local device
  starting=true
  prepare_private_state
  prepare_password
  prepare_template
  echo "Preparing a bounded ext4 workspace requires sudo for blkid and loop-device setup."
  sudo -v
  prepare_work_image
  attach_work_image
  device="$work_device_current"
  initialize_work_device "$device"
  write_runtime_env "$device"
  reconcile_work_volume "$device"
  compose_started=true
  compose up --detach
  start_succeeded=true
  echo "Markweave is starting at http://localhost:8080"
  echo "Template: $template_file"
  echo "Show the administrator password with: scripts/quickstart.sh password"
}

stop() {
  local device=""
  prepare_private_state
  prepare_password
  if [[ -e "$work_image" ]]; then
    validate_private_file "$work_image"
    echo "Detaching the bounded ext4 workspace requires sudo."
    sudo -v
    device="$(loop_for_image)"
  fi
  write_runtime_env "${device:-/dev/null}"
  compose down --remove-orphans
  if docker volume inspect "$work_volume" >/dev/null 2>&1; then
    validate_work_volume_labels
    if work_volume_is_used; then
      fail "Refusing to remove a work volume that is still used by a container."
    fi
    if [[ "$(docker volume inspect --format '{{ index .Options "type" }}' "$work_volume")" == ext4 ]]; then
      [[ -n "$device" ]] || fail "Refusing to remove the current ext4 work volume without its image."
      [[ "$(docker volume inspect --format '{{ index .Options "device" }}' "$work_volume")" == "$device" ]] || \
        fail "Refusing a current ext4 work volume attached to an unexpected device."
    fi
    docker volume rm "$work_volume" >/dev/null
  fi
  if [[ -n "$device" ]]; then
    [[ "$(sudo "$losetup" --noheadings --raw --output BACK-FILE "$device")" == "$work_image" ]] || \
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
  prepare_private_state
  prepare_password
  validate_private_file "$work_image"
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
