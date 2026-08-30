#!/usr/bin/env bash
set -euo pipefail

readonly image="${1:-localhost/md-converter:t20-ci}"
readonly runtime_uid="${T36_RUNTIME_UID:-53000}"
seccomp_profile="$(pwd)/spikes/toolchain/chrome-seccomp.json"
readonly seccomp_profile

# shellcheck source=scripts/e2e/runtime-settings.sh
source scripts/e2e/runtime-settings.sh
e2e_runtime_settings

podman run --rm \
  --user "$runtime_uid:0" \
  --read-only \
  --cap-drop=all \
  --security-opt=no-new-privileges \
  --security-opt="seccomp=$seccomp_profile" \
  --network=none \
  --memory=768m \
  --cpus=2 \
  --pids-limit=256 \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777 \
  --tmpfs /work:rw,nosuid,nodev,size=256m,mode=0770 \
  --tmpfs /data:rw,nosuid,nodev,noexec,size=64m,mode=0770 \
  --shm-size=128m \
  "${E2E_SETTINGS[@]}" \
  --env MARKWEAVE_STORAGE_PROFILE=standalone \
  --env MARKWEAVE_STANDALONE_DATA_DIRECTORY=/data \
  --env MARKWEAVE_MALWARE_SCANNING_MODE=trusted-upstream \
  --entrypoint /bin/bash \
  "$image" -ceu '
    mkdir -p "$HOME" "$TMPDIR" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" \
      "$XDG_DATA_HOME" "$XDG_RUNTIME_DIR" /data/objects
    chmod 0700 "$HOME" "$TMPDIR" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" \
      "$XDG_DATA_HOME" "$XDG_RUNTIME_DIR" /data/objects

    markweave --json migrate > /work/migration-first.json
    markweave --json migrate > /work/migration-second.json
    grep -Fq "\"changed\":true" /work/migration-first.json
    grep -Fq "\"changed\":false" /work/migration-second.json
    markweave --json --timeout 15 doctor > /work/doctor.json
    grep -Fq "\"profile\":\"standalone\"" /work/doctor.json
    grep -Fq "\"status\":\"passed\"" /work/doctor.json

    if markweave worker > /work/unexpected.out 2> /work/worker.err; then
      echo "Standalone worker unexpectedly succeeded." >&2
      exit 1
    fi
    test ! -s /work/unexpected.out
    grep -Fxq "error: Invalid application configuration." /work/worker.err

    if MARKWEAVE_DISTRIBUTED_DATABASE_URL=postgresql://private.invalid/secret \
      markweave doctor > /work/mixed.out 2> /work/mixed.err; then
      echo "Mixed-profile diagnostics unexpectedly succeeded." >&2
      exit 1
    fi
    test ! -s /work/mixed.out
    grep -Fxq "error: Invalid application configuration." /work/mixed.err
    ! grep -Fq "private.invalid" /work/mixed.err
  '

echo "Final-image runtime CLI smoke passed for $image."
