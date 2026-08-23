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
readonly WORK_STORAGE="${TOOLCHAIN_WORK_STORAGE:-tmpfs}"
readonly VALIDATION_SCOPE="${1:-documents}"

cleanup_disk_work() {
    if [[ -n "${WORK_DIRECTORY:-}" && -d "${WORK_DIRECTORY}" ]]; then
        docker run --rm \
            --user "${RUNTIME_UID}:0" \
            --mount "type=bind,source=${WORK_DIRECTORY},target=/work" \
            --entrypoint /usr/bin/find \
            "${IMAGE}" /work -mindepth 1 -exec chmod a+rwX '{}' + >/dev/null 2>&1 || true
        rm -rf -- "${WORK_DIRECTORY}"
    fi
}

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
    --env "EXPECTED_UID=${RUNTIME_UID}"
)

case "${WORK_STORAGE}" in
    tmpfs)
        runtime_args+=(
            --tmpfs "/work:rw,nosuid,nodev,size=${WORK_SIZE},uid=${RUNTIME_UID},gid=0,mode=0770"
            --env EXPECTED_WORK_STORAGE=tmpfs
        )
        ;;
    disk)
        WORK_DIRECTORY="$(mktemp -d "${SCRIPT_DIR}/.t00-work.XXXXXX")"
        readonly WORK_DIRECTORY
        trap cleanup_disk_work EXIT
        chmod 0777 "${WORK_DIRECTORY}"
        runtime_args+=(
            --mount "type=bind,source=${WORK_DIRECTORY},target=/work"
            --env EXPECTED_WORK_STORAGE=disk
        )
        ;;
    *)
        printf 'Unknown work storage: %s\n' "${WORK_STORAGE}" >&2
        exit 2
        ;;
esac

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
