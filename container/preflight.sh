#!/usr/bin/env bash
set -euo pipefail

status_value() {
  sed -n "s/^$1:[[:space:]]*//p" /proc/self/status
}

if [[ "$(id -u)" == 0 ]]; then
  echo "The Markdown converter refuses to run as root." >&2
  exit 77
fi
if [[ "$(status_value CapEff)" != 0000000000000000 ]] || \
   [[ "$(status_value CapBnd)" != 0000000000000000 ]]; then
  echo "The Markdown converter requires an empty capability set." >&2
  exit 77
fi
if [[ "$(status_value NoNewPrivs)" != 1 ]]; then
  echo "The Markdown converter requires no-new-privileges." >&2
  exit 77
fi
if ! awk '$5 == "/" && $6 ~ /(^|,)ro(,|$)/ { found=1 } END { exit !found }' \
    /proc/self/mountinfo; then
  echo "The Markdown converter requires a read-only root filesystem." >&2
  exit 77
fi
for directory in /tmp /work /dev/shm; do
  if ! awk -v target="$directory" '$5 == target { found=1 } END { exit !found }' \
      /proc/self/mountinfo; then
    echo "The Markdown converter requires a dedicated $directory mount." >&2
    exit 77
  fi
done
