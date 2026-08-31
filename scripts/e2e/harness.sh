#!/usr/bin/env bash

# Run commands that may launch long-lived helpers from the harness-owned
# directory. Some container monitors write compatibility markers relative to
# their inherited working directory, even when a persistent path is supplied.
e2e_run_in_harness_directory() {
  local harness_directory="$1"
  shift

  if [[ "$harness_directory" != /tmp/tmp.* || ! -d "$harness_directory" || \
    -L "$harness_directory" ]]; then
    echo "Refusing to run from an unexpected harness directory." >&2
    return 1
  fi
  if [[ $# -eq 0 ]]; then
    echo "A command is required for the harness directory." >&2
    return 1
  fi

  (cd -- "$harness_directory" && "$@")
}

e2e_capture_worktree_state() {
  local worktree_repository="$1"
  local destination="$2"

  git -C "$worktree_repository" status --porcelain=v1 --untracked-files=all \
    >"$destination"
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
