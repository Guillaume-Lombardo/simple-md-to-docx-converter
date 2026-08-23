#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly IMAGE="${TOOLCHAIN_IMAGE:-simple-md-toolchain:t00}"
CONTAINER_RUNTIME=docker

usage() {
    cat <<'EOF'
Usage: test-validation.sh [--runtime docker|podman]

Run the T00 success and expected-failure probes with the selected container
runtime. Docker remains the default for backward compatibility.
EOF
}

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
        *)
            printf 'Unexpected argument: %s\n' "$1" >&2
            exit 2
            ;;
    esac
done
readonly CONTAINER_RUNTIME

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

RESULTS="$(mktemp -d)"
readonly RESULTS
trap 'rm -rf -- "${RESULTS}"' EXIT

"${SCRIPT_DIR}/run-validation.sh" --runtime "${CONTAINER_RUNTIME}" documents
TOOLCHAIN_WORK_STORAGE=disk \
    "${SCRIPT_DIR}/run-validation.sh" --runtime "${CONTAINER_RUNTIME}" documents

"${CONTAINER_RUNTIME}" run --rm --network none --entrypoint /bin/bash "${IMAGE}" -c \
    'LC_ALL=C sort --check /opt/toolchain/evidence/rpm-inventory.txt'
"${CONTAINER_RUNTIME}" run --rm --network none --entrypoint /bin/cat "${IMAGE}" \
    /opt/toolchain/evidence/rpm-inventory.txt >"${RESULTS}/rpm-inventory.txt"
cmp "${SCRIPT_DIR}/../../docs/evidence/t00-rpm-inventory.txt" \
    "${RESULTS}/rpm-inventory.txt"

if "${SCRIPT_DIR}/run-validation.sh" --runtime "${CONTAINER_RUNTIME}" unsupported \
    >"${RESULTS}/unsupported.out" 2>"${RESULTS}/unsupported.err"; then
    printf 'An unsupported Chrome profile unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'Unknown validation scope: unsupported' "${RESULTS}/unsupported.err"

if [[ "${CONTAINER_RUNTIME}" == podman ]]; then
    command -v jq >/dev/null 2>&1 || {
        printf 'jq is required to review the Podman seccomp delta.\n' >&2
        exit 127
    }
    jq --sort-keys \
        '.syscalls |= map(select((.names | index("chroot")) == null))' \
        /usr/share/containers/seccomp.json >"${RESULTS}/runtime-without-chroot.json"
    jq --sort-keys \
        '.syscalls |= map(select((.names | index("chroot")) == null))' \
        "${SCRIPT_DIR}/chrome-seccomp.json" >"${RESULTS}/profile-without-chroot.json"
    cmp "${RESULTS}/runtime-without-chroot.json" \
        "${RESULTS}/profile-without-chroot.json"
    jq -e '[.syscalls[] | select(.names | index("chroot"))] == [{
        "names": ["chroot"],
        "action": "SCMP_ACT_ALLOW",
        "args": [],
        "comment": "Chrome namespace sandbox chroot",
        "includes": {},
        "excludes": {}
    }]' "${SCRIPT_DIR}/chrome-seccomp.json" >/dev/null

    "${SCRIPT_DIR}/run-validation.sh" --runtime podman target \
        >"${RESULTS}/target.out" 2>"${RESULTS}/target.err"
    grep -Fq 'chrome_sandbox=passed' "${RESULTS}/target.out"
    grep -Fq 'validation=passed' "${RESULTS}/target.out"

    if TOOLCHAIN_CHROME_SECCOMP_MODE=runtime-default \
        "${SCRIPT_DIR}/run-validation.sh" --runtime podman target \
        >"${RESULTS}/target-default.out" 2>"${RESULTS}/target-default.err"; then
        printf 'Chrome unexpectedly started under runtime-default seccomp.\n' >&2
        exit 1
    fi
    grep -Fq 'Check failed: sys_chroot("/proc/self/fdinfo/") == 0' \
        "${RESULTS}/target-default.err"
    grep -Fq 'zygote_host_impl_linux.cc' "${RESULTS}/target-default.err"

    cp "${SCRIPT_DIR}/chrome-seccomp.json" "${RESULTS}/tampered-seccomp.json"
    printf ' ' >>"${RESULTS}/tampered-seccomp.json"
    if TOOLCHAIN_CHROME_SECCOMP_PROFILE="${RESULTS}/tampered-seccomp.json" \
        "${SCRIPT_DIR}/run-validation.sh" --runtime podman target \
        >"${RESULTS}/tampered.out" 2>"${RESULTS}/tampered.err"; then
        printf 'A modified Chrome seccomp profile unexpectedly passed validation.\n' >&2
        exit 1
    fi
    grep -Fq 'Chrome seccomp profile integrity check failed' "${RESULTS}/tampered.err"
else
    if "${SCRIPT_DIR}/run-validation.sh" --runtime docker target \
        >"${RESULTS}/target.out" 2>"${RESULTS}/target.err"; then
        printf 'Chrome unexpectedly started under Docker runtime-default seccomp.\n' >&2
        exit 1
    fi
    grep -Fq 'The setuid sandbox is not running as root' "${RESULTS}/target.err"
    grep -Fq 'failed: errno = Operation not permitted' "${RESULTS}/target.err"
fi

common_security_args=(
    --rm
    --user 1000710000:0
    --read-only
    --network none
    --cap-drop ALL
    --security-opt no-new-privileges=true
    --memory 128m
    --cpus 0.5
    --pids-limit 64
    --tmpfs "/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777"
    --env EXPECTED_UID=1000710000
    --env VALIDATION_SCOPE=security
)
shm_args=(--shm-size 64m)
runtime_namespace_args=()
work_tmpfs_args=(
    --tmpfs "/work:rw,nosuid,nodev,size=32m,uid=1000710000,gid=0,mode=0770"
)
if [[ "${CONTAINER_RUNTIME}" == podman ]]; then
    runtime_namespace_args=(
        --read-only-tmpfs=false
        --uidmap 0:0:1
        --uidmap 1000710000:1:1
        --gidmap 0:0:1
    )
    common_security_args=(
        "${runtime_namespace_args[@]}"
        "${common_security_args[@]}"
    )
    shm_args=(
        --tmpfs "/dev/shm:rw,nosuid,nodev,noexec,size=64m,mode=1777"
    )
    work_tmpfs_args=(
        --mount \
        "type=tmpfs,destination=/work,tmpfs-size=32m,tmpfs-mode=0770,U=true"
    )
fi
common_security_args+=("${shm_args[@]}" "${work_tmpfs_args[@]}")

"${CONTAINER_RUNTIME}" run "${common_security_args[@]}" "${IMAGE}" \
    >"${RESULTS}/security.out" 2>"${RESULTS}/security.err"
grep -Fq 'security_properties=passed' "${RESULTS}/security.out"

if "${CONTAINER_RUNTIME}" run \
    "${common_security_args[@]}" --user 0:0 --env EXPECTED_UID=0 "${IMAGE}" \
    >"${RESULTS}/root.out" 2>"${RESULTS}/root.err"; then
    printf 'Root execution unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'the validation must not run as root' "${RESULTS}/root.err"

if "${CONTAINER_RUNTIME}" run "${common_security_args[@]}" --cap-add CHOWN "${IMAGE}" \
    >"${RESULTS}/capability.out" 2>"${RESULTS}/capability.err"; then
    printf 'Execution with a non-empty capability set unexpectedly succeeded.\n' >&2
    exit 1
fi
if [[ "${CONTAINER_RUNTIME}" == podman ]]; then
    grep -Fq 'effective capabilities are not empty' "${RESULTS}/capability.err"
else
    grep -Fq 'capability bounding set is not empty' "${RESULTS}/capability.err"
fi

if "${CONTAINER_RUNTIME}" run \
    "${common_security_args[@]}" --security-opt no-new-privileges=false "${IMAGE}" \
    >"${RESULTS}/privileges.out" 2>"${RESULTS}/privileges.err"; then
    printf 'Execution without no-new-privileges unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'no-new-privileges is not enabled' "${RESULTS}/privileges.err"

if "${CONTAINER_RUNTIME}" run \
    "${common_security_args[@]}" --security-opt seccomp=unconfined "${IMAGE}" \
    >"${RESULTS}/seccomp.out" 2>"${RESULTS}/seccomp.err"; then
    printf 'Execution without seccomp unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'seccomp filter mode is not enabled' "${RESULTS}/seccomp.err"

if "${CONTAINER_RUNTIME}" run \
    "${common_security_args[@]}" --privileged "${IMAGE}" \
    >"${RESULTS}/privileged.out" 2>"${RESULTS}/privileged.err"; then
    printf 'Privileged execution unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Eq \
    'capabilit(y|ies).*not empty|root filesystem is writable|seccomp filter mode is not enabled' \
    "${RESULTS}/privileged.err"

if "${CONTAINER_RUNTIME}" run "${common_security_args[@]}" --read-only=false "${IMAGE}" \
    >"${RESULTS}/writable-root.out" 2>"${RESULTS}/writable-root.err"; then
    printf 'Execution with a writable root unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'the container root filesystem is writable' "${RESULTS}/writable-root.err"

if "${CONTAINER_RUNTIME}" run --rm \
    "${runtime_namespace_args[@]}" \
    --user 1000710000:0 \
    --read-only \
    --network host \
    --cap-drop ALL \
    --security-opt no-new-privileges=true \
    --memory 128m \
    --cpus 0.5 \
    --pids-limit 64 \
    "${shm_args[@]}" \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777 \
    "${work_tmpfs_args[@]}" \
    --env EXPECTED_UID=1000710000 \
    --env VALIDATION_SCOPE=security \
    "${IMAGE}" \
    >"${RESULTS}/network.out" 2>"${RESULTS}/network.err"; then
    printf 'Execution with a network interface unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'network isolation exposed a non-loopback interface' "${RESULTS}/network.err"

if "${CONTAINER_RUNTIME}" run \
    "${common_security_args[@]}" --env EXPECTED_UID=1000710001 "${IMAGE}" \
    >"${RESULTS}/uid.out" 2>"${RESULTS}/uid.err"; then
    printf 'Execution with the wrong arbitrary UID unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'runtime UID does not match EXPECTED_UID=1000710001' "${RESULTS}/uid.err"

if "${CONTAINER_RUNTIME}" run --rm \
    "${runtime_namespace_args[@]}" \
    --user 1000710000:0 \
    --read-only \
    --network none \
    --cap-drop ALL \
    --security-opt no-new-privileges=true \
    "${shm_args[@]}" \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777 \
    "${work_tmpfs_args[@]}" \
    --env EXPECTED_UID=1000710000 \
    --env VALIDATION_SCOPE=security \
    "${IMAGE}" >"${RESULTS}/unbounded.out" 2>"${RESULTS}/unbounded.err"; then
    printf 'Execution without cgroup budgets unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'memory is not bounded by cgroup' "${RESULTS}/unbounded.err"

if "${CONTAINER_RUNTIME}" run --rm \
    "${runtime_namespace_args[@]}" \
    --user 1000710000:0 \
    --read-only \
    --network none \
    --cap-drop ALL \
    --security-opt no-new-privileges=true \
    --memory 128m \
    --cpus 0.5 \
    --pids-limit 64 \
    "${shm_args[@]}" \
    --tmpfs /tmp:rw,suid,dev,exec,size=32m,mode=1777 \
    "${work_tmpfs_args[@]}" \
    --env EXPECTED_UID=1000710000 \
    --env VALIDATION_SCOPE=security \
    "${IMAGE}" >"${RESULTS}/tmp-options.out" 2>"${RESULTS}/tmp-options.err"; then
    printf 'Execution with executable temporary storage unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq '/tmp does not have the required bounded tmpfs mount options' \
    "${RESULTS}/tmp-options.err"

if "${CONTAINER_RUNTIME}" run --rm \
    "${runtime_namespace_args[@]}" \
    --user 1000710000:0 \
    --read-only \
    --network none \
    --cap-drop ALL \
    --security-opt no-new-privileges=true \
    --memory 128m \
    --cpus 0.5 \
    --pids-limit 64 \
    "${shm_args[@]}" \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777 \
    --env EXPECTED_UID=1000710000 \
    --env VALIDATION_SCOPE=documents \
    "${IMAGE}" >"${RESULTS}/no-work.out" 2>"${RESULTS}/no-work.err"; then
    printf 'Execution without writable work storage unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq '/work does not have the required bounded tmpfs mount options' \
    "${RESULTS}/no-work.err"

if TOOLCHAIN_WORK_STORAGE=unsupported \
    "${SCRIPT_DIR}/run-validation.sh" --runtime "${CONTAINER_RUNTIME}" documents \
    >"${RESULTS}/work-storage.out" 2>"${RESULTS}/work-storage.err"; then
    printf 'An unsupported work storage unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'Unknown work storage: unsupported' "${RESULTS}/work-storage.err"

printf 'failure-probes=passed\n'
