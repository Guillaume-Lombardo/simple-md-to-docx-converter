#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 6 || ! "$1" =~ ^[1-9][0-9]*$ || ! "$2" =~ ^[1-9][0-9]*$ \
  || -z "$3" || -z "$4" || "$5" != "--" ]]; then
  echo "Usage: run-bounded-benchmark-command.sh TIMEOUT_SECONDS GRACE_SECONDS LOG LABEL -- COMMAND [ARGUMENT ...]" >&2
  exit 2
fi

readonly timeout_seconds="$1"
readonly grace_seconds="$2"
readonly log_file="$3"
readonly label="$4"
shift 5

{
  printf 'boundary_start label=%q timeout_seconds=%s grace_seconds=%s command=' \
    "$label" "$timeout_seconds" "$grace_seconds"
  printf '%q ' "$@"
  printf '\n'
} >> "$log_file"

status=0
{
  timeout --signal=TERM --kill-after="${grace_seconds}s" "${timeout_seconds}s" \
    "$@" || status=$?
} >> "$log_file" 2>&1

if [[ "$status" -eq 124 || "$status" -eq 137 ]]; then
  printf 'boundary_timeout label=%q timeout_seconds=%s grace_seconds=%s normalized_status=124 observed_status=%s\n' \
    "$label" "$timeout_seconds" "$grace_seconds" "$status" >> "$log_file"
  exit 124
fi
if [[ "$status" -ne 0 ]]; then
  printf 'boundary_exit label=%q status=%s\n' "$label" "$status" >> "$log_file"
  exit "$status"
fi
printf 'boundary_complete label=%q status=0\n' "$label" >> "$log_file"
