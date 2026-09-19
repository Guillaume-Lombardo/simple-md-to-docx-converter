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
readonly CHROME_SECCOMP_MODE="${TOOLCHAIN_CHROME_SECCOMP_MODE:-profile}"
readonly CHROME_SECCOMP_PROFILE="${TOOLCHAIN_CHROME_SECCOMP_PROFILE:-${SCRIPT_DIR}/chrome-seccomp.json}"
readonly CHROME_SECCOMP_SHA256=bbd643f78d48b477111dd8597a69ba6bee4db68ce199dbf09d87bf90a1377f46

usage() {
    cat <<'EOF'
Usage: run-validation.sh [--runtime docker|podman] [documents|security|target]

Build and run the T00 toolchain probe with the selected container runtime.
Docker remains the default for backward compatibility.
EOF
}

CONTAINER_RUNTIME=docker
VALIDATION_SCOPE=documents
scope_is_set=false
while (($#)); do
    case "$1" in
        --runtime)
            [[ $# -ge 2 ]] || {
                printf 'Missing value for --runtime.\n' >&2
                exit 2
            }
            CONTAINER_RUNTIME="$2"
            shift 2
            ;;
        --runtime=*)
            CONTAINER_RUNTIME="${1#*=}"
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        -*)
            printf 'Unknown option: %s\n' "$1" >&2
            exit 2
            ;;
        *)
            if [[ "${scope_is_set}" == true ]]; then
                printf 'Unexpected argument: %s\n' "$1" >&2
                exit 2
            fi
            VALIDATION_SCOPE="$1"
            scope_is_set=true
            shift
            ;;
    esac
done
readonly CONTAINER_RUNTIME VALIDATION_SCOPE

case "${CONTAINER_RUNTIME}" in
    docker | podman) ;;
    *)
        printf 'Unknown container runtime: %s\n' "${CONTAINER_RUNTIME}" >&2
        exit 2
        ;;
esac
command -v "${CONTAINER_RUNTIME}" >/dev/null 2>&1 || {
    printf 'Container runtime not found: %s\n' "${CONTAINER_RUNTIME}" >&2
    exit 127
}

runtime_namespace_args=()
runtime_shm_args=(--shm-size 128m)
if [[ "${CONTAINER_RUNTIME}" == podman ]]; then
    # Map the arbitrary container UID sparsely into the rootless subordinate range.
    # Disable Podman's implicit read-only tmpfs mounts; only the explicit bounded
    # /tmp, /work, and /dev/shm mounts below may remain writable.
    runtime_namespace_args=(
        --read-only-tmpfs=false
        --uidmap 0:0:1
        --uidmap "${RUNTIME_UID}:1:1"
        --gidmap 0:0:1
    )
    runtime_shm_args=(
        --tmpfs "/dev/shm:rw,nosuid,nodev,noexec,size=128m,mode=1777"
    )
fi

cleanup_disk_work() {
    if [[ -n "${WORK_DIRECTORY:-}" && -d "${WORK_DIRECTORY}" ]]; then
        "${CONTAINER_RUNTIME}" run --rm \
            "${runtime_namespace_args[@]}" \
            --user "${RUNTIME_UID}:0" \
            --read-only \
            --network none \
            --cap-drop ALL \
            --security-opt no-new-privileges=true \
            --memory 128m \
            --cpus 0.5 \
            --pids-limit 64 \
            --mount "type=bind,source=${WORK_DIRECTORY},target=/work" \
            --entrypoint /usr/bin/find \
            "${IMAGE}" /work -mindepth 1 -exec chmod a+rwX '{}' + >/dev/null 2>&1 || true
        rm -rf -- "${WORK_DIRECTORY}"
    fi
}

runtime_args=(
    --rm
    "${runtime_namespace_args[@]}"
    --user "${RUNTIME_UID}:0"
    --read-only
    --network none
    --cap-drop ALL
    --security-opt no-new-privileges=true
    --memory "${MEMORY}"
    --cpus "${CPUS}"
    --pids-limit "${PIDS}"
    "${runtime_shm_args[@]}"
    --tmpfs "/tmp:rw,nosuid,nodev,noexec,size=${TMP_SIZE},mode=1777"
    --env "EXPECTED_UID=${RUNTIME_UID}"
)

case "${WORK_STORAGE}" in
    tmpfs)
        if [[ "${CONTAINER_RUNTIME}" == podman ]]; then
            runtime_args+=(
                --mount \
                "type=tmpfs,destination=/work,tmpfs-size=${WORK_SIZE},tmpfs-mode=0770,U=true"
            )
        else
            runtime_args+=(
                --tmpfs \
                "/work:rw,nosuid,nodev,size=${WORK_SIZE},uid=${RUNTIME_UID},gid=0,mode=0770"
            )
        fi
        runtime_args+=(--env EXPECTED_WORK_STORAGE=tmpfs)
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

if [[ "${CONTAINER_RUNTIME}" == podman && "${VALIDATION_SCOPE}" == target ]]; then
    case "${CHROME_SECCOMP_MODE}" in
        profile)
            [[ -r "${CHROME_SECCOMP_PROFILE}" ]] || {
                printf 'Chrome seccomp profile is not readable: %s\n' \
                    "${CHROME_SECCOMP_PROFILE}" >&2
                exit 1
            }
            printf '%s  %s\n' "${CHROME_SECCOMP_SHA256}" "${CHROME_SECCOMP_PROFILE}" \
                | sha256sum --check --strict >/dev/null || {
                    printf 'Chrome seccomp profile integrity check failed: %s\n' \
                        "${CHROME_SECCOMP_PROFILE}" >&2
                    exit 1
                }
            runtime_args+=(--security-opt "seccomp=${CHROME_SECCOMP_PROFILE}")
            ;;
        runtime-default) ;;
        *)
            printf 'Unknown Chrome seccomp mode: %s\n' "${CHROME_SECCOMP_MODE}" >&2
            exit 2
            ;;
    esac
fi

"${CONTAINER_RUNTIME}" build \
    --pull=false \
    --file "${SCRIPT_DIR}/Containerfile" \
    --tag "${IMAGE}" \
    "${SCRIPT_DIR}"

"${CONTAINER_RUNTIME}" run "${runtime_args[@]}" "${IMAGE}"
