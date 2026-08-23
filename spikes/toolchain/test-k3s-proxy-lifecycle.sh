#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
# shellcheck disable=SC2034
PROCESS_PRIVILEGE=()
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/k3s-process-guards.sh"

if [[ "${1:-}" == fake-proxy ]]; then
    token="$2"
    mode="$3"
    trap 'exit 0' INT TERM
    if [[ "${mode}" == failure ]]; then
        sleep 0.2
        exit 17
    fi
    bash -c 'trap "exit 0" INT TERM; while :; do sleep 1; done' "${token}-child" &
    wait
    exit 0
fi

if [[ "${1:-}" == interrupt-harness ]]; then
    pid=""
    group=""
    cleanup_interrupt_harness() {
        trap - EXIT
        stop_tracked_process_group "${pid}" "${group}" "${PROXY_TOKEN}"
    }
    trap cleanup_interrupt_harness EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    start_tracked_process_group "${PROXY_LOG}" "${PROXY_TOKEN}" \
        "$0" fake-proxy "${PROXY_TOKEN}" running
    pid="${TRACKED_PROCESS_PID}"
    group="${TRACKED_PROCESS_GROUP}"
    touch "${READY_FILE:?}"
    while :; do sleep 1; done
fi

TEST_ROOT="$(mktemp -d)"
readonly TEST_ROOT
BASELINE_GROUP=""
REWRITTEN_GROUP=""
COLLISION_GROUP=""
cleanup_test_root() {
    trap - EXIT
    [[ -z "${REWRITTEN_GROUP}" ]] || signal_process_group KILL "${REWRITTEN_GROUP}"
    [[ -z "${COLLISION_GROUP}" ]] || signal_process_group KILL "${COLLISION_GROUP}"
    [[ -z "${BASELINE_GROUP}" ]] || signal_process_group KILL "${BASELINE_GROUP}"
    rm -rf -- "${TEST_ROOT}"
}
trap cleanup_test_root EXIT

wait_for_file() {
    local file="$1"
    for _ in {1..50}; do
        [[ -e "${file}" ]] && return 0
        sleep 0.1
    done
    return 1
}

success_token="t00-proxy-success-$$"
start_tracked_process_group "${TEST_ROOT}/success.log" "${success_token}" \
    "$0" fake-proxy "${success_token}" running
stop_tracked_process_group "${TRACKED_PROCESS_PID}" "${TRACKED_PROCESS_GROUP}" \
    "${success_token}"
[[ -z "$(matching_process_ids "${success_token}")" ]]

failure_token="t00-proxy-failure-$$"
start_tracked_process_group "${TEST_ROOT}/failure.log" "${failure_token}" \
    "$0" fake-proxy "${failure_token}" failure
failure_pid="${TRACKED_PROCESS_PID}"
failure_group="${TRACKED_PROCESS_GROUP}"
wait "${failure_pid}" 2>/dev/null || true
stop_tracked_process_group "${failure_pid}" "${failure_group}" "${failure_token}"
[[ -z "$(matching_process_ids "${failure_token}")" ]]

interrupt_token="t00-proxy-interrupt-$$"
ready_file="${TEST_ROOT}/interrupt.ready"
PROXY_TOKEN="${interrupt_token}" \
PROXY_LOG="${TEST_ROOT}/interrupt-proxy.log" \
READY_FILE="${ready_file}" \
    "$0" interrupt-harness &
harness_pid=$!
wait_for_file "${ready_file}"
kill -TERM "${harness_pid}"
wait "${harness_pid}" 2>/dev/null || true
[[ -z "$(matching_process_ids "${interrupt_token}")" ]]

# k3s rewrites the proxy command line to the single word `kubectl`. Exercise
# the kernel process-name baseline independently of the run-token fallback.
cp -- /bin/sleep "${TEST_ROOT}/kubectl"
setsid "${TEST_ROOT}/kubectl" 30 &
baseline_pid=$!
BASELINE_GROUP="${baseline_pid}"
for _ in {1..20}; do
    baseline_identities="$(kubectl_process_identities)"
    [[ -n "${baseline_identities}" ]] && break
    sleep 0.1
done
[[ -n "${baseline_identities}" ]]

setsid "${TEST_ROOT}/kubectl" 30 &
rewritten_pid=$!
REWRITTEN_GROUP="${rewritten_pid}"
for _ in {1..20}; do
    new_identities="$(new_kubectl_process_identities "${baseline_identities}")"
    [[ -n "${new_identities}" ]] && break
    sleep 0.1
done
[[ -n "${new_identities}" ]]
if verify_no_new_kubectl_processes "${baseline_identities}" 2>/dev/null; then
    printf 'A new argv-rewritten kubectl process escaped baseline detection.\n' >&2
    exit 1
fi
stop_tracked_process_group "${rewritten_pid}" "${REWRITTEN_GROUP}" \
    "t00-token-deliberately-absent-$$"
REWRITTEN_GROUP=""
verify_no_new_kubectl_processes "${baseline_identities}"
kill -0 "${baseline_pid}"

mkdir "${TEST_ROOT}/collision"
cp -- /bin/sh "${TEST_ROOT}/collision/kubectl"
collision_token="t00-proxy-baseline-collision-$$"
# `$0` is deliberately evaluated by the copied shell process.
# shellcheck disable=SC2016
setsid "${TEST_ROOT}/collision/kubectl" -c \
    'trap "exit 0" INT TERM; bash -c '\''trap "exit 0" INT TERM; while :; do sleep 1; done'\'' "$0-child" & wait' \
    "${collision_token}" \
    >"${TEST_ROOT}/collision-baseline.log" 2>&1 &
collision_pid=$!
COLLISION_GROUP="${collision_pid}"
for _ in {1..20}; do
    collision_identity="$(process_identity "${collision_pid}" 2>/dev/null || true)"
    [[ -n "${collision_identity}" ]] \
        && [[ -n "$(matching_process_ids "${collision_token}")" ]] \
        && break
    sleep 0.1
done
[[ -n "${collision_identity}" ]]
collision_baseline="${baseline_identities}"$'\n'"${collision_identity}"
if start_tracked_process_group "${TEST_ROOT}/collision.log" "${collision_token}" \
    "$0" fake-proxy "${collision_token}" running 2>/dev/null; then
    printf 'A pre-existing proxy-token collision unexpectedly launched.\n' >&2
    exit 1
fi
if stop_tracked_process_group "" "" "${collision_token}" \
    "${collision_baseline}"; then
    printf 'Token fallback unexpectedly accepted a baseline-owned process group.\n' >&2
    exit 1
fi
kill -0 "${collision_pid}"
[[ -n "$(matching_process_ids "${collision_token}")" ]]
signal_process_group TERM "${COLLISION_GROUP}"
wait "${collision_pid}" 2>/dev/null || true
COLLISION_GROUP=""

signal_process_group TERM "${BASELINE_GROUP}"
wait "${baseline_pid}" 2>/dev/null || true
BASELINE_GROUP=""

printf 'k3s_proxy_success_cleanup=passed\n'
printf 'k3s_proxy_failure_cleanup=passed\n'
printf 'k3s_proxy_interrupt_cleanup=passed\n'
printf 'k3s_proxy_argv_rewrite_detection=passed\n'
printf 'k3s_proxy_baseline_token_collision=passed\n'
