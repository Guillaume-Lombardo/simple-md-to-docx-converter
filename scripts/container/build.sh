#!/usr/bin/env bash
set -euo pipefail

readonly image="${1:-localhost/md-converter:t20}"
readonly source_date_epoch="${SOURCE_DATE_EPOCH:-1787601600}"
readonly toolchain_cache_directory="${MARKWEAVE_TOOLCHAIN_CACHE_DIRECTORY:-${XDG_CACHE_HOME:-$HOME/.cache}/markweave/toolchain}"
application_version="$(uv version --short --locked)"
readonly application_version

MARKWEAVE_TOOLCHAIN_CACHE_DIRECTORY="$toolchain_cache_directory" \
  bash scripts/ci/prepare-libreoffice-archive.sh rpm

env -u SOURCE_DATE_EPOCH podman build \
  --pull=false \
  --timestamp="$source_date_epoch" \
  --build-arg "APPLICATION_VERSION=$application_version" \
  --build-context "libreoffice-archive=$toolchain_cache_directory" \
  --file Containerfile \
  --tag "$image" \
  .
