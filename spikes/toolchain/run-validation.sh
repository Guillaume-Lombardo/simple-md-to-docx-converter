#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly IMAGE="${TOOLCHAIN_IMAGE:-simple-md-toolchain:t00}"
readonly RUNTIME_UID="${TOOLCHAIN_UID:-1000710000}"
readonly MEMORY="${TOOLCHAIN_MEMORY:-2g}"
readonly CPUS="${TOOLCHAIN_CPUS:-2}"
readonly PIDS="${TOOLCHAIN_PIDS:-512}"
readonly WORK_SIZE="${TOOLCHAIN_WORK_SIZE:-1g}"
readonly TMP_SIZE="${TOOLCHAIN_TMP_SIZE:-512m}"
readonly VALIDATION_SCOPE="${1:-documents}"

runtime_args=(
    --rm
    --user "${RUNTIME_UID}:0"
    --read-only
    --network none
    --cap-drop ALL
    --security-opt no-new-privileges=true
    --memory "${MEMORY}"
    --cpus "${CPUS}"
    --pids-limit "${PIDS}"
    --shm-size 128m
    --tmpfs "/tmp:rw,nosuid,nodev,noexec,size=${TMP_SIZE},mode=1777"
    --tmpfs "/work:rw,nosuid,nodev,size=${WORK_SIZE},uid=${RUNTIME_UID},gid=0,mode=0770"
    --env "EXPECTED_UID=${RUNTIME_UID}"
)

case "${VALIDATION_SCOPE}" in
    documents | security | target)
        runtime_args+=(--env "VALIDATION_SCOPE=${VALIDATION_SCOPE}")
        ;;
    *)
        printf 'Unknown validation scope: %s\n' "${VALIDATION_SCOPE}" >&2
        exit 2
        ;;
esac

docker build --pull=false --file "${SCRIPT_DIR}/Containerfile" --tag "${IMAGE}" "${SCRIPT_DIR}"

docker run "${runtime_args[@]}" "${IMAGE}"
