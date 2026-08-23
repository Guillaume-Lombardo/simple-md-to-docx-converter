#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly IMAGE="${TOOLCHAIN_IMAGE:-simple-md-toolchain:t00}"
readonly RESULTS="$(mktemp -d)"
trap 'rm -rf -- "${RESULTS}"' EXIT

"${SCRIPT_DIR}/run-validation.sh" namespace-lab

if "${SCRIPT_DIR}/run-validation.sh" unsupported \
    >"${RESULTS}/unsupported.out" 2>"${RESULTS}/unsupported.err"; then
    printf 'An unsupported Chrome profile unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'Unknown Chrome profile: unsupported' "${RESULTS}/unsupported.err"

if "${SCRIPT_DIR}/run-validation.sh" target \
    >"${RESULTS}/target.out" 2>"${RESULTS}/target.err"; then
    printf 'Chrome unexpectedly started under the target security profile.\n' >&2
    exit 1
fi
grep -Fq 'The setuid sandbox is not running as root' "${RESULTS}/target.err"
grep -Fq 'failed: errno = Operation not permitted' "${RESULTS}/target.err"

if docker run --rm --user 0:0 "${IMAGE}" \
    >"${RESULTS}/root.out" 2>"${RESULTS}/root.err"; then
    printf 'Root execution unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Fq 'the validation must not run as root' "${RESULTS}/root.err"

if docker run --rm \
    --user 1000710000:0 \
    --read-only \
    --network none \
    --cap-drop ALL \
    --security-opt no-new-privileges=true \
    --env EXPECTED_UID=1000710000 \
    "${IMAGE}" >"${RESULTS}/no-work.out" 2>"${RESULTS}/no-work.err"; then
    printf 'Execution without writable work storage unexpectedly succeeded.\n' >&2
    exit 1
fi
grep -Eq '/work.+(Permission denied|not writable|Read-only file system)' "${RESULTS}/no-work.err"

printf 'failure-probes=passed\n'
