#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly IMAGE="${TOOLCHAIN_IMAGE:-simple-md-toolchain:t00}"
RESULTS="$(mktemp -d)"
readonly RESULTS
trap 'rm -rf -- "${RESULTS}"' EXIT

"${SCRIPT_DIR}/run-validation.sh" documents
TOOLCHAIN_WORK_STORAGE=disk "${SCRIPT_DIR}/run-validation.sh" documents

docker run --rm --entrypoint /bin/bash "${IMAGE}" -c \
    'LC_ALL=C sort --check /opt/toolchain/evidence/rpm-inventory.txt'
docker run --rm --entrypoint /bin/cat "${IMAGE}" \
    /opt/toolchain/evidence/rpm-inventory.txt >"${RESULTS}/rpm-inventory.txt"
cmp "${SCRIPT_DIR}/../../docs/evidence/t00-rpm-inventory.txt" \
    "${RESULTS}/rpm-inventory.txt"

if "${SCRIPT_DIR}/run-validation.sh" unsupported \
    >"${RESULTS}/unsupported.out" 2>"${RESULTS}/unsupported.err"; then
    printf 'An unsupported Chrome profile unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'Unknown validation scope: unsupported' "${RESULTS}/unsupported.err"

if "${SCRIPT_DIR}/run-validation.sh" target \
    >"${RESULTS}/target.out" 2>"${RESULTS}/target.err"; then
    printf 'Chrome unexpectedly started under the target security profile.\n' >&2
    exit 1
fi
grep -Fq 'The setuid sandbox is not running as root' "${RESULTS}/target.err"
grep -Fq 'failed: errno = Operation not permitted' "${RESULTS}/target.err"

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
    --shm-size 64m
    --tmpfs "/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777"
    --tmpfs "/work:rw,nosuid,nodev,size=32m,uid=1000710000,gid=0,mode=0770"
    --env EXPECTED_UID=1000710000
    --env VALIDATION_SCOPE=security
)

docker run "${common_security_args[@]}" "${IMAGE}" \
    >"${RESULTS}/security.out" 2>"${RESULTS}/security.err"
grep -Fq 'security_properties=passed' "${RESULTS}/security.out"

if docker run "${common_security_args[@]}" --user 0:0 --env EXPECTED_UID=0 "${IMAGE}" \
    >"${RESULTS}/root.out" 2>"${RESULTS}/root.err"; then
    printf 'Root execution unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'the validation must not run as root' "${RESULTS}/root.err"

if docker run "${common_security_args[@]}" --cap-add CHOWN "${IMAGE}" \
    >"${RESULTS}/capability.out" 2>"${RESULTS}/capability.err"; then
    printf 'Execution with a non-empty capability set unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'capability bounding set is not empty' "${RESULTS}/capability.err"

if docker run "${common_security_args[@]}" --security-opt no-new-privileges=false "${IMAGE}" \
    >"${RESULTS}/privileges.out" 2>"${RESULTS}/privileges.err"; then
    printf 'Execution without no-new-privileges unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'no-new-privileges is not enabled' "${RESULTS}/privileges.err"

if docker run "${common_security_args[@]}" --read-only=false "${IMAGE}" \
    >"${RESULTS}/writable-root.out" 2>"${RESULTS}/writable-root.err"; then
    printf 'Execution with a writable root unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'the container root filesystem is writable' "${RESULTS}/writable-root.err"

if docker run --rm \
    --user 1000710000:0 \
    --read-only \
    --network bridge \
    --cap-drop ALL \
    --security-opt no-new-privileges=true \
    --memory 128m \
    --cpus 0.5 \
    --pids-limit 64 \
    --shm-size 64m \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777 \
    --tmpfs /work:rw,nosuid,nodev,size=32m,uid=1000710000,gid=0,mode=0770 \
    --env EXPECTED_UID=1000710000 \
    --env VALIDATION_SCOPE=security \
    "${IMAGE}" \
    >"${RESULTS}/network.out" 2>"${RESULTS}/network.err"; then
    printf 'Execution with a network interface unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'network isolation exposed a non-loopback interface' "${RESULTS}/network.err"

if docker run "${common_security_args[@]}" --env EXPECTED_UID=1000710001 "${IMAGE}" \
    >"${RESULTS}/uid.out" 2>"${RESULTS}/uid.err"; then
    printf 'Execution with the wrong arbitrary UID unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'runtime UID does not match EXPECTED_UID=1000710001' "${RESULTS}/uid.err"

if docker run --rm \
    --user 1000710000:0 \
    --read-only \
    --network none \
    --cap-drop ALL \
    --security-opt no-new-privileges=true \
    --shm-size 64m \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777 \
    --tmpfs /work:rw,nosuid,nodev,size=32m,uid=1000710000,gid=0,mode=0770 \
    --env EXPECTED_UID=1000710000 \
    --env VALIDATION_SCOPE=security \
    "${IMAGE}" >"${RESULTS}/unbounded.out" 2>"${RESULTS}/unbounded.err"; then
    printf 'Execution without cgroup budgets unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'memory is not bounded by cgroup' "${RESULTS}/unbounded.err"

if docker run --rm \
    --user 1000710000:0 \
    --read-only \
    --network none \
    --cap-drop ALL \
    --security-opt no-new-privileges=true \
    --memory 128m \
    --cpus 0.5 \
    --pids-limit 64 \
    --shm-size 64m \
    --tmpfs /tmp:rw,suid,dev,exec,size=32m,mode=1777 \
    --tmpfs /work:rw,nosuid,nodev,size=32m,uid=1000710000,gid=0,mode=0770 \
    --env EXPECTED_UID=1000710000 \
    --env VALIDATION_SCOPE=security \
    "${IMAGE}" >"${RESULTS}/tmp-options.out" 2>"${RESULTS}/tmp-options.err"; then
    printf 'Execution with executable temporary storage unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq '/tmp does not have the required bounded tmpfs mount options' \
    "${RESULTS}/tmp-options.err"

if docker run --rm \
    --user 1000710000:0 \
    --read-only \
    --network none \
    --cap-drop ALL \
    --security-opt no-new-privileges=true \
    --memory 128m \
    --cpus 0.5 \
    --pids-limit 64 \
    --shm-size 64m \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777 \
    --env EXPECTED_UID=1000710000 \
    --env VALIDATION_SCOPE=documents \
    "${IMAGE}" >"${RESULTS}/no-work.out" 2>"${RESULTS}/no-work.err"; then
    printf 'Execution without writable work storage unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq '/work does not have the required bounded tmpfs mount options' \
    "${RESULTS}/no-work.err"

if TOOLCHAIN_WORK_STORAGE=unsupported "${SCRIPT_DIR}/run-validation.sh" documents \
    >"${RESULTS}/work-storage.out" 2>"${RESULTS}/work-storage.err"; then
    printf 'An unsupported work storage unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'Unknown work storage: unsupported' "${RESULTS}/work-storage.err"

printf 'failure-probes=passed\n'
