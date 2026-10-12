#!/usr/bin/env bash
set -euo pipefail

# Acquire every digest-pinned base image that a Containerfile declares as an
# `ARG *_IMAGE=<reference>@sha256:<digest>` default, through the bounded
# immutable-image puller. Builds then run with `--pull=never`, so no base image
# is ever fetched implicitly by `podman build` without retries or verification.

if [[ $# -lt 1 ]]; then
  echo "Usage: scripts/container/pull-base-images.sh CONTAINERFILE..." >&2
  exit 2
fi

repository="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly repository
readonly pinned_base='^ARG [A-Z][A-Z0-9_]*_IMAGE=([^[:space:]@]+@sha256:[0-9a-f]{64})[[:space:]]*$'
declare -A acquired=()

for containerfile in "$@"; do
  if [[ ! -f "$containerfile" ]]; then
    echo "Containerfile $containerfile does not exist." >&2
    exit 2
  fi
  references=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ $pinned_base ]]; then
      references+=("${BASH_REMATCH[1]}")
    fi
  done <"$containerfile"
  if ((${#references[@]} == 0)); then
    echo "Containerfile $containerfile declares no digest-pinned base image." >&2
    exit 1
  fi
  for reference in "${references[@]}"; do
    if [[ -n "${acquired[$reference]:-}" ]]; then
      continue
    fi
    bash "$repository/scripts/ci/pull-immutable-image.sh" \
      "$reference" "${reference##*@}"
    acquired[$reference]=1
  done
done
