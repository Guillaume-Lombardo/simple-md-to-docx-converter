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

group_leader_pid=""
timer_pid=""

group_is_alive() {
  [[ -n "$group_leader_pid" ]] && kill -0 -- "-$group_leader_pid" 2>/dev/null
}

terminate_remaining_group() {
  group_is_alive || return 0
  kill -TERM -- "-$group_leader_pid" 2>/dev/null || true
  sleep "$grace_seconds"
  kill -KILL -- "-$group_leader_pid" 2>/dev/null || true
  for _ in {1..40}; do
    group_is_alive || return 0
    sleep 0.05
  done
  return 1
}

record_failure() {
  local message="$1"
  printf '%s\n' "$message" >> "$log_file"
  if [[ "$log_file" != /dev/stderr && "$log_file" != /dev/stdout ]]; then
    printf '%s\n' "$message" >&2
  fi
}

cleanup() {
  if [[ -n "$timer_pid" ]]; then
    kill "$timer_pid" 2>/dev/null || true
    wait "$timer_pid" 2>/dev/null || true
  fi
  terminate_remaining_group >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
trap 'exit 129' HUP

{
  printf 'boundary_start label=%q timeout_seconds=%s grace_seconds=%s command=' \
    "$label" "$timeout_seconds" "$grace_seconds"
  printf '%q ' "$@"
  printf '\n'
} >> "$log_file"

setsid --wait "$@" >> "$log_file" 2>&1 &
group_leader_pid=$!
sleep "$timeout_seconds" &
timer_pid=$!

status=0
completed_pid=""
wait -n -p completed_pid "$group_leader_pid" "$timer_pid" || status=$?
deadline_reached=false
if [[ "$completed_pid" == "$timer_pid" ]]; then
  deadline_reached=true
  timer_pid=""
  kill -TERM -- "-$group_leader_pid" 2>/dev/null || true
  sleep "$grace_seconds"
  kill -KILL -- "-$group_leader_pid" 2>/dev/null || true
  status=0
  wait "$group_leader_pid" 2>/dev/null || status=$?
else
  kill "$timer_pid" 2>/dev/null || true
  wait "$timer_pid" 2>/dev/null || true
  timer_pid=""
fi

if ! terminate_remaining_group; then
  printf -v failure \
    'boundary_cleanup_failed label=%q group_leader_pid=%s status=125' \
    "$label" "$group_leader_pid"
  record_failure "$failure"
  exit 125
fi
group_leader_pid=""

if [[ "$deadline_reached" == true ]]; then
  printf -v failure \
    'boundary_timeout label=%q timeout_seconds=%s grace_seconds=%s deadline_reached=true observed_status=%s normalized_status=124' \
    "$label" "$timeout_seconds" "$grace_seconds" "$status"
  record_failure "$failure"
  exit 124
fi
if [[ "$status" -ne 0 ]]; then
  printf -v failure 'boundary_exit label=%q deadline_reached=false status=%s' \
    "$label" "$status"
  record_failure "$failure"
  exit "$status"
fi
printf 'boundary_complete label=%q deadline_reached=false status=0\n' "$label" \
  >> "$log_file"
