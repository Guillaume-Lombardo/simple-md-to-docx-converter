#!/usr/bin/env bash

# The caller may use sudo to signal a process group containing root-owned
# descendants. Ordinary-user tests leave this array empty.
if ! declare -p PROCESS_PRIVILEGE >/dev/null 2>&1; then
    PROCESS_PRIVILEGE=()
fi

matching_process_ids() {
    local token="$1"
    local cmdline
    local process_path
    for process_path in /proc/[0-9]*/cmdline; do
        [[ -r "${process_path}" ]] || continue
        cmdline="$(tr '\0' ' ' <"${process_path}" 2>/dev/null || true)"
        [[ "${cmdline}" == *"${token}"* ]] || continue
        printf '%s\n' "${process_path#/proc/}" | cut -d/ -f1
    done
}

process_group_exists() {
    local process_group="$1"
    ps -eo pgid= | awk -v expected="${process_group}" '
        $1 == expected {found = 1}
        END {exit !found}
    '
}

kubectl_process_identities() {
    local process_ids=()
    local process_id
    local start_time
    mapfile -t process_ids < <(ps -eo pid=,comm= | awk '$2 == "kubectl" {print $1}')
    for process_id in "${process_ids[@]}"; do
        [[ -n "${process_id}" ]] || continue
        if [[ ! -r "/proc/${process_id}/stat" ]]; then
            # A process may exit between ps and /proc. Only fail when it still
            # exists but its immutable start time cannot be inspected.
            [[ ! -e "/proc/${process_id}" ]] && continue
            printf 'Cannot inspect kubectl process identity: %s\n' "${process_id}" >&2
            return 1
        fi
        start_time="$(awk '{print $22}' "/proc/${process_id}/stat" 2>/dev/null || true)"
        if [[ -z "${start_time}" ]]; then
            [[ ! -e "/proc/${process_id}" ]] && continue
            printf 'Cannot read kubectl process start time: %s\n' "${process_id}" >&2
            return 1
        fi
        printf '%s:%s\n' "${process_id}" "${start_time}"
    done
}

new_kubectl_process_identities() {
    local baseline="$1"
    local current=""
    local identity
    current="$(kubectl_process_identities)" || return 1
    while read -r identity; do
        [[ -n "${identity}" ]] || continue
        grep -Fxq -- "${identity}" <<<"${baseline}" || printf '%s\n' "${identity}"
    done <<<"${current}"
}

verify_no_new_kubectl_processes() {
    local baseline="$1"
    local remaining=""
    for _ in {1..20}; do
        if ! remaining="$(new_kubectl_process_identities "${baseline}")"; then
            printf 'Cannot verify the post-cleanup kubectl process set.\n' >&2
            return 1
        fi
        [[ -z "${remaining}" ]] && return 0
        sleep 0.1
    done
    printf 'New kubectl process identities survived cleanup: %s\n' \
        "${remaining//$'\n'/, }" >&2
    return 1
}

signal_process_group() {
    local signal="$1"
    local process_group="$2"
    "${PROCESS_PRIVILEGE[@]}" kill "-${signal}" -- "-${process_group}" 2>/dev/null || true
}

stop_tracked_process_group() {
    local launcher_pid="$1"
    local process_group="$2"
    local match_token="$3"
    local status=0
    local matched_pid
    local matched_group

    if [[ -n "${process_group}" ]] && process_group_exists "${process_group}"; then
        signal_process_group TERM "${process_group}"
        for _ in {1..50}; do
            process_group_exists "${process_group}" || break
            sleep 0.1
        done
        if process_group_exists "${process_group}"; then
            signal_process_group KILL "${process_group}"
        fi
    fi

    [[ -z "${launcher_pid}" ]] || wait "${launcher_pid}" 2>/dev/null || true

    # The token is registered before launch, so this also catches interruption
    # between process creation and capture of the launcher PID or process group.
    while read -r matched_pid; do
        [[ -n "${matched_pid}" ]] || continue
        matched_group="$(ps -o pgid= -p "${matched_pid}" 2>/dev/null | tr -d ' ' || true)"
        [[ -n "${matched_group}" ]] || continue
        signal_process_group TERM "${matched_group}"
    done < <(matching_process_ids "${match_token}")
    for _ in {1..50}; do
        [[ -z "$(matching_process_ids "${match_token}")" ]] && break
        sleep 0.1
    done
    while read -r matched_pid; do
        [[ -n "${matched_pid}" ]] || continue
        matched_group="$(ps -o pgid= -p "${matched_pid}" 2>/dev/null | tr -d ' ' || true)"
        [[ -n "${matched_group}" ]] || continue
        signal_process_group KILL "${matched_group}"
    done < <(matching_process_ids "${match_token}")
    [[ -z "$(matching_process_ids "${match_token}")" ]] || status=1
    return "${status}"
}

start_tracked_process_group() {
    local log_file="$1"
    local match_token="$2"
    shift 2

    setsid --wait "$@" >"${log_file}" 2>&1 &
    TRACKED_PROCESS_PID=$!
    TRACKED_PROCESS_GROUP="$(
        ps -o pgid= -p "${TRACKED_PROCESS_PID}" 2>/dev/null | tr -d ' '
    )"
    [[ -n "${TRACKED_PROCESS_GROUP}" ]] || return 1
    [[ "${TRACKED_PROCESS_GROUP}" == "${TRACKED_PROCESS_PID}" ]] || return 1
    [[ -n "$(matching_process_ids "${match_token}")" ]]
}
