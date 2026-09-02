#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ( "$1" != standalone && "$1" != distributed ) ]]; then
  echo "Usage: scripts/e2e/rollback-rehearsal.sh {standalone|distributed}" >&2
  exit 2
fi

readonly profile="$1"
readonly released_digest=sha256:7d6c69ff76004bf1db6781eeec49fadac9633dbc3d8725e19060b67538fc8d8e
readonly released_image="ghcr.io/guillaume-lombardo/md-converter:0.5.2@$released_digest"
readonly repository="$PWD"
runtime_directory="$(mktemp -d -t markweave-t64-rollback.XXXXXXXX)"
readonly runtime_directory

cleanup() {
  rm -rf -- "$runtime_directory"
}
trap cleanup EXIT

test "$(podman info --format '{{.Host.Security.Rootless}}')" = true
podman pull --quiet "$released_image"
test "$(podman image inspect "$released_image" --format '{{.Digest}}')" = \
  "$released_digest"

cd "$runtime_directory"
export PYTHONPATH="$repository"
export UV_PROJECT="$repository/pyproject.toml"
if [[ "$profile" == standalone ]]; then
  MARKWEAVE_EXPECT_LEGACY_ROUTE_MANIFEST=true \
    MARKWEAVE_REPOSITORY_ROOT="$repository" \
    bash "$repository/scripts/container/api-smoke.sh" "$released_image"
else
  MARKWEAVE_EXPECT_LEGACY_ROUTE_MANIFEST=true \
    MARKWEAVE_REPOSITORY_ROOT="$repository" \
    bash "$repository/scripts/container/distributed-api-smoke.sh" "$released_image"
  MARKWEAVE_REPOSITORY_ROOT="$repository" \
    bash "$repository/scripts/container/recovery-cli-smoke.sh" "$released_image"
fi

echo "Released 0.5.2 $profile rollback rehearsal passed after pre-removal gate 33686251439."
