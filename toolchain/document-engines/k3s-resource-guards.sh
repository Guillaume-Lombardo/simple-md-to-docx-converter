#!/usr/bin/env bash

# The caller may set PRIVILEGE=(sudo). Tests leave it empty.
if ! declare -p PRIVILEGE >/dev/null 2>&1; then
    PRIVILEGE=()
fi

guard_identity() {
    "${PRIVILEGE[@]}" stat -Lc '%d:%i' -- "$1"
}

guard_hash() {
    "${PRIVILEGE[@]}" sha256sum -- "$1" | awk '{print $1}'
}

guard_resources_absent() {
    local path
    for path in "$@"; do
        if "${PRIVILEGE[@]}" test -e "${path}"; then
            printf 'Refusing to overwrite pre-existing resource: %s\n' "${path}" >&2
            return 1
        fi
    done
}

guard_owned_marker() {
    local marker="$1"
    local run_id="$2"
    local marker_identity="$3"
    "${PRIVILEGE[@]}" test -f "${marker}" \
        && [[ "$(guard_identity "${marker}")" == "${marker_identity}" ]] \
        && [[ "$("${PRIVILEGE[@]}" sed -n '1p' -- "${marker}")" == "${run_id}" ]] \
        && [[ "$("${PRIVILEGE[@]}" wc -l -- "${marker}" | awk '{print $1}')" == 1 ]]
}

guard_owned_file() {
    local target="$1"
    local marker="$2"
    local run_id="$3"
    local expected_hash="$4"
    local target_identity="$5"
    local marker_identity="$6"
    guard_owned_marker "${marker}" "${run_id}" "${marker_identity}" \
        && "${PRIVILEGE[@]}" test -f "${target}" \
        && [[ "$(guard_identity "${target}")" == "${target_identity}" ]] \
        && [[ "$(guard_hash "${target}")" == "${expected_hash}" ]]
}

remove_owned_marker() {
    local marker="$1"
    local run_id="$2"
    local marker_identity="$3"
    guard_owned_marker "${marker}" "${run_id}" "${marker_identity}" || {
        printf 'Refusing to remove changed or unowned marker: %s\n' "${marker}" >&2
        return 1
    }
    "${PRIVILEGE[@]}" rm -- "${marker}"
}

remove_owned_file() {
    local target="$1"
    local marker="$2"
    local run_id="$3"
    local expected_hash="$4"
    local target_identity="$5"
    local marker_identity="$6"
    guard_owned_file "${target}" "${marker}" "${run_id}" "${expected_hash}" \
        "${target_identity}" "${marker_identity}" || {
        printf 'Refusing to remove changed or unowned resource: %s\n' "${target}" >&2
        return 1
    }
    "${PRIVILEGE[@]}" rm -- "${target}"
    remove_owned_marker "${marker}" "${run_id}" "${marker_identity}"
}

namespace_json_is_owned() {
    local namespace_json="$1"
    local name="$2"
    local uid="$3"
    local run_id="$4"
    jq -e --arg name "${name}" --arg uid "${uid}" --arg run_id "${run_id}" '
        .metadata.name == $name
        and .metadata.uid == $uid
        and .metadata.labels["t00.g1lom.xyz/owned"] == "true"
        and .metadata.labels["t00.g1lom.xyz/run-id"] == $run_id
        and .metadata.annotations["t00.g1lom.xyz/run-id"] == $run_id
    ' <<<"${namespace_json}" >/dev/null
}

namespace_json_has_run_ownership() {
    local namespace_json="$1"
    local name="$2"
    local run_id="$3"
    jq -e --arg name "${name}" --arg run_id "${run_id}" '
        .metadata.name == $name
        and (.metadata.uid | type == "string" and length > 0)
        and .metadata.labels["t00.g1lom.xyz/owned"] == "true"
        and .metadata.labels["t00.g1lom.xyz/run-id"] == $run_id
        and .metadata.annotations["t00.g1lom.xyz/run-id"] == $run_id
    ' <<<"${namespace_json}" >/dev/null
}

image_digest_is_owned() {
    local current_digest="$1"
    local expected_digest="$2"
    [[ "${current_digest}" =~ ^sha256:[0-9a-f]{64}$ ]] \
        && [[ "${current_digest}" == "${expected_digest}" ]]
}

image_reference_is_absent() {
    [[ -z "$1" ]]
}
