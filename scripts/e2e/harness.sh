#!/usr/bin/env bash

e2e_create_harness_directory() {
  local temporary_root="${TMPDIR:-/tmp}"

  if [[ "$temporary_root" != /* || ! -d "$temporary_root" || \
    -L "$temporary_root" ]]; then
    echo "Refusing to create a harness directory below an unsafe temporary root." >&2
    return 1
  fi

  mktemp --directory --tmpdir="$temporary_root" markweave-e2e.XXXXXXXXXX
}

e2e_harness_directory_identity() {
  local harness_directory="$1"

  stat --dereference --format='%d:%i' -- "$harness_directory"
}

e2e_remove_unidentified_harness_directory() {
  local harness_directory="$1"

  if [[ "$harness_directory" != /* || "$harness_directory" == / || \
    "$harness_directory" == "${TMPDIR:-/tmp}" || \
    "${harness_directory##*/}" != markweave-e2e.* || \
    ! -d "$harness_directory" || -L "$harness_directory" ]]; then
    echo "Refusing to remove an unidentified harness directory." >&2
    return 1
  fi

  rmdir -- "$harness_directory"
}

e2e_initialize_harness_directory() {
  local directory_variable="$1"
  local identity_variable="$2"
  local created_directory
  local created_identity

  if [[ ! "$directory_variable" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ || \
    ! "$identity_variable" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
    echo "Refusing invalid harness output variable names." >&2
    return 1
  fi
  created_directory="$(e2e_create_harness_directory)" || return 1
  if ! created_identity="$(
    e2e_harness_directory_identity "$created_directory"
  )"; then
    e2e_remove_unidentified_harness_directory "$created_directory"
    return 1
  fi

  printf -v "$directory_variable" '%s' "$created_directory"
  printf -v "$identity_variable" '%s' "$created_identity"
}

e2e_require_owned_harness_directory() {
  local harness_directory="$1"
  local expected_identity="$2"
  local actual_identity

  if [[ "$harness_directory" != /* || "$harness_directory" == / || \
    "$harness_directory" == "${TMPDIR:-/tmp}" || \
    "${harness_directory##*/}" != markweave-e2e.* || \
    ! -d "$harness_directory" || -L "$harness_directory" ]]; then
    echo "Refusing to run from an unexpected harness directory." >&2
    return 1
  fi
  if [[ "$(stat --format='%u' -- "$harness_directory")" != "$(id -u)" ]]; then
    echo "Refusing a harness directory owned by another user." >&2
    return 1
  fi
  actual_identity="$(e2e_harness_directory_identity "$harness_directory")"
  if [[ -z "$expected_identity" || "$actual_identity" != "$expected_identity" ]]; then
    echo "Refusing a harness directory whose identity changed." >&2
    return 1
  fi
}

# Run commands that may launch long-lived helpers from the harness-owned
# directory. Some container monitors write compatibility markers relative to
# their inherited working directory, even when a persistent path is supplied.
e2e_run_in_harness_directory() {
  local harness_directory="$1"
  local expected_identity="$2"
  shift 2

  e2e_require_owned_harness_directory \
    "$harness_directory" "$expected_identity" || return 1
  if [[ $# -eq 0 ]]; then
    echo "A command is required for the harness directory." >&2
    return 1
  fi

  (cd -- "$harness_directory" && "$@")
}

e2e_remove_harness_directory() {
  local harness_directory="$1"
  local expected_identity="$2"

  e2e_require_owned_harness_directory \
    "$harness_directory" "$expected_identity" || return 1
  podman unshare rm -rf -- "$harness_directory" >/dev/null 2>&1 || \
    rm -rf -- "$harness_directory"
}

e2e_capture_worktree_state() {
  local worktree_repository="$1"
  local destination="$2"

  e2e_get_worktree_state "$worktree_repository" >"$destination"
}

e2e_get_worktree_state() {
  local worktree_repository="$1"

  git -C "$worktree_repository" status --porcelain=v1 --untracked-files=all
}

e2e_require_worktree_unchanged() {
  local worktree_repository="$1"
  local baseline="$2"
  local current="$baseline.current"

  e2e_capture_worktree_state "$worktree_repository" "$current"
  if cmp --silent -- "$baseline" "$current"; then
    return 0
  fi

  echo "Final-image E2E changed the repository worktree:" >&2
  diff --unified=0 -- "$baseline" "$current" >&2 || true
  return 1
}

e2e_require_worktree_state_unchanged() {
  local worktree_repository="$1"
  local baseline_state="$2"
  local current_state

  current_state="$(e2e_get_worktree_state "$worktree_repository")" || return 1
  if [[ "$baseline_state" == "$current_state" ]]; then
    return 0
  fi

  echo "Final-image E2E changed the repository worktree:" >&2
  diff --unified=0 --label=before --label=after \
    <(printf '%s\n' "$baseline_state") \
    <(printf '%s\n' "$current_state") >&2 || true
  return 1
}
