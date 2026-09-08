#!/usr/bin/env bash
set -euo pipefail

readonly image="${1:-localhost/markweave-reverse-attempt:t70}"
readonly source_date_epoch="${SOURCE_DATE_EPOCH:-1788470400}"

env -u SOURCE_DATE_EPOCH podman build \
  --pull=false \
  --format oci \
  --timestamp="$source_date_epoch" \
  --file containers/reverse-attempt/Containerfile \
  --tag "$image" \
  .
