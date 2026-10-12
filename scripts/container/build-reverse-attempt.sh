#!/usr/bin/env bash
set -euo pipefail

readonly image="${1:-localhost/markweave-reverse-attempt:t70}"
readonly source_date_epoch="${SOURCE_DATE_EPOCH:-1788470400}"

bash scripts/container/pull-base-images.sh containers/reverse-attempt/Containerfile
env -u SOURCE_DATE_EPOCH podman build \
  --pull=never \
  --format oci \
  --timestamp="$source_date_epoch" \
  --file containers/reverse-attempt/Containerfile \
  --tag "$image" \
  .
