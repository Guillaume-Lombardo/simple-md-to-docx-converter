#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly PROFILE_SHA256=bbd643f78d48b477111dd8597a69ba6bee4db68ce199dbf09d87bf90a1377f46
readonly SOURCE_IMAGE="${TOOLCHAIN_IMAGE:-simple-md-toolchain:t00}"
readonly SECCOMP_DIR="${TOOLCHAIN_K3S_SECCOMP_DIR:-/var/lib/kubelet/seccomp}"

KUBECTL=(kubectl)
CTR=(ctr)
PRIVILEGE=()
RUNNER_ARGS=()
if [[ "${1:-}" == "--sudo-k3s" ]]; then
    KUBECTL=(sudo /usr/local/bin/k3s kubectl)
    CTR=(sudo /usr/local/bin/k3s ctr)
    PRIVILEGE=(sudo)
    RUNNER_ARGS=(--sudo-k3s)
    shift
fi
if (($#)); then
    printf 'Usage: test-k3s-validation.sh [--sudo-k3s]\n' >&2
    exit 2
fi

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/k3s-resource-guards.sh"

command -v jq >/dev/null 2>&1 || {
    printf 'jq is required for the k3s validation probes.\n' >&2
    exit 127
}
command -v podman >/dev/null 2>&1 || {
    printf 'Podman is required to transfer the T00 image into k3s.\n' >&2
    exit 127
}

RUN_ID="${TOOLCHAIN_K3S_RUN_ID:-$(tr -d '-' </proc/sys/kernel/random/uuid | cut -c1-12)}"
[[ "${RUN_ID}" =~ ^[a-z0-9][a-z0-9-]{7,39}$ ]] || {
    printf 'TOOLCHAIN_K3S_RUN_ID must be an 8-40 character lowercase run identifier.\n' >&2
    exit 2
}
readonly RUN_ID
readonly PROFILE_NAME="t00-k3s-chrome-bbd643f78d48-${RUN_ID}.json"
readonly PROFILE_PATH="${SECCOMP_DIR}/${PROFILE_NAME}"
readonly PROFILE_MARKER="${SECCOMP_DIR}/.${PROFILE_NAME}.owner"
readonly IMPORTED_IMAGE="localhost/simple-md-toolchain:t00-${RUN_ID}"
readonly NAMESPACE="t00-k3s-${RUN_ID}"
RUNNER="${SCRIPT_DIR}/run-k3s-validation.sh"
if [[ "${T00_K3S_TEST_MODE:-false}" == true && -n "${T00_K3S_TEST_RUNNER:-}" ]]; then
    RUNNER="${T00_K3S_TEST_RUNNER}"
fi
readonly RUNNER

RESULTS="$(mktemp -d)"
readonly RESULTS
PROFILE_STAGE=""
PROFILE_STAGE_ID=""
MARKER_STAGE=""
MARKER_STAGE_ID=""
PROFILE_ID=""
MARKER_ID=""
PROFILE_CLAIM_INTENT=false
MARKER_CLAIM_INTENT=false
IMPORTED_IMAGE_DIGEST=""
IMPORTED_IMAGE_INTENT=false
LOCAL_IMAGE_ID=""
LOCAL_IMAGE_INTENT=false
IMAGE_ARCHIVE="${RESULTS}/toolchain-image.tar"

inject_test_failure() {
    local point="$1"
    [[ "${T00_K3S_TEST_MODE:-false}" == true ]] || return 0
    [[ "${T00_K3S_TEST_FAIL_AT:-}" == "${point}" ]] || return 0
    mkdir "${RESULTS}/injected-${point}" 2>/dev/null || return 0
    printf 'Injected test failure after %s.\n' "${point}" >&2
    return 86
}

current_image_digest() {
    "${CTR[@]}" images list "name==\"${IMPORTED_IMAGE}\"" \
        | awk 'NR == 2 {print $3}'
}

get_image_digest() {
    local digest=""
    for _ in {1..3}; do
        if digest="$(current_image_digest)"; then
            printf '%s' "${digest}"
            return 0
        fi
        sleep 0.1
    done
    return 1
}

get_namespace_name() {
    local namespace_name=""
    for _ in {1..3}; do
        if namespace_name="$(
            "${KUBECTL[@]}" get namespace "${NAMESPACE}" \
                --ignore-not-found -o name 2>/dev/null
        )"; then
            printf '%s' "${namespace_name}"
            return 0
        fi
        sleep 0.1
    done
    return 1
}

remove_stage() {
    local path="$1"
    local identity="$2"
    [[ -n "${path}" ]] || return 0
    if "${PRIVILEGE[@]}" test -e "${path}"; then
        [[ "$(guard_identity "${path}")" == "${identity}" ]] || {
            printf 'Refusing to remove changed staging resource: %s\n' "${path}" >&2
            return 1
        }
        "${PRIVILEGE[@]}" rm -- "${path}"
    fi
}

cleanup_global_resources() {
    local status=0
    local namespace_name
    if ! namespace_name="$(get_namespace_name)"; then
        printf 'Refusing global cleanup: namespace absence cannot be verified.\n' >&2
        return 1
    fi
    if [[ -n "${namespace_name}" ]]; then
        printf 'Refusing global cleanup while namespace %s still exists.\n' \
            "${NAMESPACE}" >&2
        return 1
    fi
    if [[ "${IMPORTED_IMAGE_INTENT}" == true ]]; then
        if ! current_digest="$(get_image_digest)"; then
            printf 'Refusing image cleanup: containerd state cannot be verified.\n' >&2
            status=1
            current_digest=""
        fi
        if image_reference_is_absent "${current_digest}"; then
            :
        elif ! image_digest_is_owned "${current_digest}" "${IMPORTED_IMAGE_DIGEST}"; then
            printf 'Refusing to remove changed or unowned image: %s\n' \
                "${IMPORTED_IMAGE}" >&2
            status=1
        else
            "${CTR[@]}" images remove "${IMPORTED_IMAGE}" >/dev/null || status=1
            if ! remaining_digest="$(get_image_digest)" \
                || ! image_reference_is_absent "${remaining_digest}"; then
                printf 'Imported image still exists after cleanup: %s\n' \
                    "${IMPORTED_IMAGE}" >&2
                status=1
            fi
        fi
    fi
    if [[ "${PROFILE_CLAIM_INTENT}" == true ]] \
        && "${PRIVILEGE[@]}" test -e "${PROFILE_PATH}"; then
        remove_owned_file "${PROFILE_PATH}" "${PROFILE_MARKER}" "${RUN_ID}" \
            "${PROFILE_SHA256}" "${PROFILE_ID}" "${MARKER_ID}" || status=1
    elif [[ "${MARKER_CLAIM_INTENT}" == true ]] \
        && "${PRIVILEGE[@]}" test -e "${PROFILE_MARKER}"; then
        remove_owned_marker "${PROFILE_MARKER}" "${RUN_ID}" "${MARKER_ID}" || status=1
    fi
    if [[ "${LOCAL_IMAGE_INTENT}" == true ]]; then
        current_local_id="$(podman image inspect "${IMPORTED_IMAGE}" --format '{{.Id}}' \
            2>/dev/null || true)"
        if [[ -z "${current_local_id}" ]]; then
            :
        elif [[ "${current_local_id}" != "${LOCAL_IMAGE_ID}" ]]; then
            printf 'Refusing to remove changed local image tag: %s\n' "${IMPORTED_IMAGE}" >&2
            status=1
        else
            podman image untag "${SOURCE_IMAGE}" "${IMPORTED_IMAGE}" >/dev/null || status=1
        fi
    fi
    remove_stage "${PROFILE_STAGE}" "${PROFILE_STAGE_ID}" || status=1
    remove_stage "${MARKER_STAGE}" "${MARKER_STAGE_ID}" || status=1
    return "${status}"
}

on_exit() {
    local status=$?
    trap - EXIT
    if ! cleanup_global_resources; then
        status=1
    fi
    rm -rf -- "${RESULTS}"
    exit "${status}"
}
trap on_exit EXIT

printf '%s  %s\n' "${PROFILE_SHA256}" "${SCRIPT_DIR}/chrome-seccomp.json" \
    | sha256sum --check --strict >/dev/null
"${KUBECTL[@]}" version >/dev/null
"${PRIVILEGE[@]}" test -d "${SECCOMP_DIR}" || {
    printf 'Kubelet seccomp directory does not exist: %s\n' "${SECCOMP_DIR}" >&2
    exit 1
}
guard_resources_absent "${PROFILE_PATH}" "${PROFILE_MARKER}"
if ! existing_namespace="$(
    "${KUBECTL[@]}" get namespace "${NAMESPACE}" \
        --ignore-not-found -o name 2>/dev/null
)"; then
    printf 'Cannot preflight namespace ownership: %s\n' "${NAMESPACE}" >&2
    exit 1
fi
if [[ -n "${existing_namespace}" ]]; then
    printf 'Refusing to reuse pre-existing namespace: %s\n' "${NAMESPACE}" >&2
    exit 1
fi
existing_image_digest="$(get_image_digest)"
image_reference_is_absent "${existing_image_digest}" || {
    printf 'Refusing to overwrite pre-existing image: %s\n' "${IMPORTED_IMAGE}" >&2
    exit 1
}
if podman image exists "${IMPORTED_IMAGE}"; then
    printf 'Refusing to overwrite pre-existing local image tag: %s\n' "${IMPORTED_IMAGE}" >&2
    exit 1
fi

printf '%s\n' "${RUN_ID}" >"${RESULTS}/profile-owner"
MARKER_STAGE="$("${PRIVILEGE[@]}" mktemp \
    "${SECCOMP_DIR}/.${PROFILE_NAME}.owner-stage.XXXXXX")"
MARKER_STAGE_ID="$(guard_identity "${MARKER_STAGE}")"
"${PRIVILEGE[@]}" install -m 0600 "${RESULTS}/profile-owner" "${MARKER_STAGE}"
MARKER_STAGE_ID="$(guard_identity "${MARKER_STAGE}")"
MARKER_ID="${MARKER_STAGE_ID}"
MARKER_CLAIM_INTENT=true
"${PRIVILEGE[@]}" ln -- "${MARKER_STAGE}" "${PROFILE_MARKER}"
inject_test_failure after-marker-create-identity
[[ "$(guard_identity "${PROFILE_MARKER}")" == "${MARKER_ID}" ]]
remove_stage "${MARKER_STAGE}" "${MARKER_STAGE_ID}"
MARKER_STAGE=""

PROFILE_STAGE="$("${PRIVILEGE[@]}" mktemp \
    "${SECCOMP_DIR}/.${PROFILE_NAME}.profile-stage.XXXXXX")"
PROFILE_STAGE_ID="$(guard_identity "${PROFILE_STAGE}")"
"${PRIVILEGE[@]}" install -m 0644 "${SCRIPT_DIR}/chrome-seccomp.json" "${PROFILE_STAGE}"
PROFILE_STAGE_ID="$(guard_identity "${PROFILE_STAGE}")"
[[ "$(guard_hash "${PROFILE_STAGE}")" == "${PROFILE_SHA256}" ]]
PROFILE_ID="${PROFILE_STAGE_ID}"
PROFILE_CLAIM_INTENT=true
"${PRIVILEGE[@]}" ln -- "${PROFILE_STAGE}" "${PROFILE_PATH}"
inject_test_failure after-profile-create-identity
[[ "$(guard_identity "${PROFILE_PATH}")" == "${PROFILE_ID}" ]]
remove_stage "${PROFILE_STAGE}" "${PROFILE_STAGE_ID}"
PROFILE_STAGE=""

# Hash the actual kubelet-visible file independently after installation. A
# mismatch aborts before Kubernetes can reference the profile, and cleanup then
# refuses to remove changed content.
installed_profile_sha256="$(guard_hash "${PROFILE_PATH}")"
[[ "${installed_profile_sha256}" == "${PROFILE_SHA256}" ]] || {
    printf 'Installed seccomp profile integrity check failed: %s\n' \
        "${PROFILE_PATH}" >&2
    exit 1
}

LOCAL_IMAGE_ID="$(podman image inspect "${SOURCE_IMAGE}" --format '{{.Id}}')"
LOCAL_IMAGE_INTENT=true
podman image tag "${SOURCE_IMAGE}" "${IMPORTED_IMAGE}"
inject_test_failure after-podman-tag-inspect
[[ "$(podman image inspect "${IMPORTED_IMAGE}" --format '{{.Id}}')" == "${LOCAL_IMAGE_ID}" ]]
podman save --format oci-archive --output "${IMAGE_ARCHIVE}" "${IMPORTED_IMAGE}"
IMPORTED_IMAGE_DIGEST="$(
    tar -xOf "${IMAGE_ARCHIVE}" index.json \
        | jq -er 'if (.manifests | length) == 1 then .manifests[0].digest else error("manifest count") end'
)"
image_digest_is_owned "${IMPORTED_IMAGE_DIGEST}" "${IMPORTED_IMAGE_DIGEST}"
IMPORTED_IMAGE_INTENT=true
"${CTR[@]}" images import "${IMAGE_ARCHIVE}" >/dev/null
inject_test_failure after-containerd-import-digest
current_digest="$(get_image_digest)"
image_digest_is_owned "${current_digest}" "${IMPORTED_IMAGE_DIGEST}" || {
    printf 'Imported image did not expose one exact digest: %s\n' "${IMPORTED_IMAGE}" >&2
    exit 1
}

TOOLCHAIN_K3S_RUN_ID="${RUN_ID}" \
TOOLCHAIN_K3S_IMAGE="${IMPORTED_IMAGE}" \
TOOLCHAIN_K3S_PROFILE_NAME="${PROFILE_NAME}" \
    "${RUNNER}" "${RUNNER_ARGS[@]}"

remaining_namespace="$(
    "${KUBECTL[@]}" get namespace "${NAMESPACE}" \
        --ignore-not-found -o name
)"
[[ -z "${remaining_namespace}" ]] || {
    printf 'Validation namespace still exists after the harness: %s\n' "${NAMESPACE}" >&2
    exit 1
}

printf 'installed_profile_sha256=%s\n' "${installed_profile_sha256}"
printf 'k3s_global_resource_ownership=passed\n'
