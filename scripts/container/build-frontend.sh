#!/usr/bin/env bash
set -euo pipefail

# Build the Next.js frontend image from pre-acquired, digest-verified base
# images. An optional second argument sets the reproducible layer timestamp.

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: scripts/container/build-frontend.sh IMAGE [TIMESTAMP]" >&2
  exit 2
fi
readonly image="$1"
readonly timestamp="${2:-}"
if [[ -n "$timestamp" && ! "$timestamp" =~ ^[0-9]+$ ]]; then
  echo "The frontend build timestamp must be a non-negative integer." >&2
  exit 2
fi

bash scripts/container/pull-base-images.sh web/Containerfile
build_options=(--pull=never --format oci)
if [[ -n "$timestamp" ]]; then
  build_options+=(--timestamp "$timestamp")
fi
podman build "${build_options[@]}" --tag "$image" --file web/Containerfile .
