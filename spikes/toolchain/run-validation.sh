#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly IMAGE="${TOOLCHAIN_IMAGE:-simple-md-toolchain:t00}"
readonly RUNTIME_UID="${TOOLCHAIN_UID:-1000710000}"
readonly MEMORY="${TOOLCHAIN_MEMORY:-2g}"
readonly CPUS="${TOOLCHAIN_CPUS:-2}"
readonly PIDS="${TOOLCHAIN_PIDS:-512}"
readonly WORK_SIZE="${TOOLCHAIN_WORK_SIZE:-1g}"
readonly TMP_SIZE="${TOOLCHAIN_TMP_SIZE:-512m}"
readonly CHROME_PROFILE="${1:-target}"

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
    --tmpfs "/tmp:rw,nosuid,nodev,noexec,size=${TMP_SIZE},mode=1777"
    --tmpfs "/work:rw,nosuid,nodev,size=${WORK_SIZE},uid=${RUNTIME_UID},gid=0,mode=0770"
    --env "EXPECTED_UID=${RUNTIME_UID}"
)

case "${CHROME_PROFILE}" in
    target)
        ;;
    namespace-lab)
        runtime_args+=(
            --security-opt seccomp=unconfined
            --env CHROME_SANDBOX_MODE=namespace-lab
        )
        ;;
    *)
        printf 'Unknown Chrome profile: %s\n' "${CHROME_PROFILE}" >&2
        exit 2
        ;;
esac

docker build --pull=false --file "${SCRIPT_DIR}/Containerfile" --tag "${IMAGE}" "${SCRIPT_DIR}"

docker run "${runtime_args[@]}" "${IMAGE}"
