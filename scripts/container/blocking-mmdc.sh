#!/usr/bin/env bash
set -euo pipefail

readonly evidence_directory=/evidence
printf '%s\n' "$$" >"$evidence_directory/active.pid"

terminated() {
  printf 'terminated\n' >"$evidence_directory/terminated"
  exit 143
}
trap terminated TERM INT

while true; do
  sleep 1
done
