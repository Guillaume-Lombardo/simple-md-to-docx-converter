#!/usr/bin/env bash
set -euo pipefail

readonly image="${1:-localhost/md-converter:t20}"
readonly source_date_epoch="${SOURCE_DATE_EPOCH:-1787601600}"
readonly application_version="$(uv version --short --locked)"

podman build \
  --pull=false \
  --timestamp="$source_date_epoch" \
  --build-arg "APPLICATION_VERSION=$application_version" \
  --file Containerfile \
  --tag "$image" \
  .
