#!/usr/bin/env bash
set -euo pipefail

SELF_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
readonly SELF_PATH
SCRIPT_DIR="$(cd -- "$(dirname -- "${SELF_PATH}")" && pwd)"
readonly SCRIPT_DIR
# shellcheck disable=SC2034
PROCESS_PRIVILEGE=()
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/k3s-process-guards.sh"

mock_kubectl() {
    case "${1:-}" in
        version)
            return 0
            ;;
        get)
            if [[ "${2:-}" == namespace && -e "${MOCK_STATE}/namespace.json" ]]; then
                if [[ " $* " == *" -o name "* ]]; then
                    printf 'namespace/%s\n' "${TOOLCHAIN_K3S_RUN_ID}"
                else
                    tee <"${MOCK_STATE}/namespace.json"
                fi
                return 0
            fi
            [[ " $* " == *" --ignore-not-found "* ]]
            return
            ;;
        create)
            jq --arg uid "uid-${TOOLCHAIN_K3S_RUN_ID}" \
                '.metadata.uid=$uid' >"${MOCK_STATE}/namespace.json"
            tee <"${MOCK_STATE}/namespace.json"
            ;;
        proxy)
            printf 'Starting to serve on 127.0.0.1:43123\n'
            trap 'exit 0' INT TERM
            while :; do sleep 1; done
            ;;
        wait)
            return 0
            ;;
        *)
            printf 'Unexpected mock kubectl arguments: %s\n' "$*" >&2
            return 2
            ;;
    esac
}

mock_ctr() {
    [[ "${1:-}" == images ]] || return 2
    case "${2:-}" in
        list)
            if [[ -e "${MOCK_STATE}/ctr-digest" ]]; then
                if [[ "${MOCK_CTR_FAIL_ONCE:-false}" == true \
                    && ! -e "${MOCK_STATE}/ctr-failure-fired" ]]; then
                    touch "${MOCK_STATE}/ctr-failure-fired"
                    return 19
                fi
                printf 'REF TYPE DIGEST SIZE PLATFORMS LABELS\n'
                printf 'image type %s size platform labels\n' \
                    "$(<"${MOCK_STATE}/ctr-digest")"
            fi
            ;;
        import)
            archive="$3"
            tar -xOf "${archive}" index.json \
                | jq -er '.manifests[0].digest' >"${MOCK_STATE}/ctr-digest"
            ;;
        remove)
            rm -- "${MOCK_STATE}/ctr-digest"
            ;;
        *)
            return 2
            ;;
    esac
}

mock_podman() {
    if [[ "${1:-}" == image && "${2:-}" == exists ]]; then
        [[ -e "${MOCK_STATE}/local-tag" ]]
        return
    fi
    if [[ "${1:-}" == image && "${2:-}" == inspect ]]; then
        if [[ "${3:-}" == simple-md-toolchain:t00 || -e "${MOCK_STATE}/local-tag" ]]; then
            printf '%s\n' "${MOCK_SOURCE_ID}"
            return 0
        fi
        return 1
    fi
    if [[ "${1:-}" == image && "${2:-}" == tag ]]; then
        touch "${MOCK_STATE}/local-tag"
        return 0
    fi
    if [[ "${1:-}" == image && "${2:-}" == untag ]]; then
        rm -- "${MOCK_STATE}/local-tag"
        return 0
    fi
    if [[ "${1:-}" == save ]]; then
        output=""
        while (($#)); do
            if [[ "$1" == --output ]]; then
                output="$2"
                break
            fi
            shift
        done
        cp -- "${MOCK_IMAGE_ARCHIVE}" "${output}"
        return 0
    fi
    printf 'Unexpected mock podman arguments: %s\n' "$*" >&2
    return 2
}

mock_curl() {
    rm -- "${MOCK_STATE}/namespace.json"
}

case "$(basename -- "$0")" in
    kubectl)
        mock_kubectl "$@"
        exit
        ;;
    ctr)
        mock_ctr "$@"
        exit
        ;;
    podman)
        mock_podman "$@"
        exit
        ;;
    curl)
        mock_curl "$@"
        exit
        ;;
esac

TEST_ROOT="$(mktemp -d)"
readonly TEST_ROOT
trap 'rm -rf -- "${TEST_ROOT}"' EXIT
mkdir "${TEST_ROOT}/bin" "${TEST_ROOT}/seccomp" "${TEST_ROOT}/state"
for command_name in kubectl ctr podman curl; do
    ln -s -- "${SELF_PATH}" "${TEST_ROOT}/bin/${command_name}"
done

MOCK_SOURCE_ID="$(podman image inspect simple-md-toolchain:t00 --format '{{.Id}}')"
MOCK_IMAGE_ARCHIVE="${TEST_ROOT}/source-image.tar"
podman save --format oci-archive --output "${MOCK_IMAGE_ARCHIVE}" \
    simple-md-toolchain:t00 >/dev/null
export MOCK_SOURCE_ID MOCK_IMAGE_ARCHIVE

reset_mock_state() {
    find "${TEST_ROOT}/state" -mindepth 1 -maxdepth 1 -type f -delete
    find "${TEST_ROOT}/seccomp" -mindepth 1 -maxdepth 1 -type f -delete
}

assert_wrapper_resources_absent() {
    [[ ! -e "${TEST_ROOT}/state/namespace.json" ]]
    [[ ! -e "${TEST_ROOT}/state/ctr-digest" ]]
    [[ ! -e "${TEST_ROOT}/state/local-tag" ]]
    [[ -z "$(find "${TEST_ROOT}/seccomp" -mindepth 1 -maxdepth 1 -type f -print -quit)" ]]
}

run_wrapper_injection() {
    local point="$1"
    local run_id="$2"
    local log_file="${TEST_ROOT}/${point}.log"
    reset_mock_state
    if PATH="${TEST_ROOT}/bin:${PATH}" \
        MOCK_STATE="${TEST_ROOT}/state" \
        TOOLCHAIN_K3S_SECCOMP_DIR="${TEST_ROOT}/seccomp" \
        TOOLCHAIN_K3S_RUN_ID="${run_id}" \
        T00_K3S_TEST_MODE=true \
        T00_K3S_TEST_FAIL_AT="${point}" \
        "${SCRIPT_DIR}/test-k3s-validation.sh" >"${log_file}" 2>&1; then
        printf 'Injected failure unexpectedly succeeded: %s\n' "${point}" >&2
        return 1
    fi
    grep -Fq "Injected test failure after ${point}." "${log_file}"
    assert_wrapper_resources_absent
}

run_wrapper_injection after-marker-create-identity acquiremarker
run_wrapper_injection after-profile-create-identity acquireprofile
run_wrapper_injection after-podman-tag-inspect acquirepodman
run_wrapper_injection after-containerd-import-digest acquiredigest

reset_mock_state
PATH="${TEST_ROOT}/bin:${PATH}" \
MOCK_STATE="${TEST_ROOT}/state" \
MOCK_CTR_FAIL_ONCE=true \
TOOLCHAIN_K3S_SECCOMP_DIR="${TEST_ROOT}/seccomp" \
TOOLCHAIN_K3S_RUN_ID=acquirectr \
T00_K3S_TEST_MODE=true \
T00_K3S_TEST_RUNNER=/bin/true \
    "${SCRIPT_DIR}/test-k3s-validation.sh" >/dev/null
assert_wrapper_resources_absent

reset_mock_state
PATH="${TEST_ROOT}/bin:${PATH}" \
MOCK_STATE="${TEST_ROOT}/state" \
TOOLCHAIN_K3S_RUN_ID=acquireapi \
TOOLCHAIN_K3S_IMAGE=localhost/simple-md-toolchain:t00-acquireapi \
TOOLCHAIN_K3S_PROFILE_NAME=t00-k3s-chrome-test-acquireapi.json \
T00_K3S_TEST_MODE=true \
T00_K3S_TEST_FAIL_AT=after-namespace-create-api \
    "${SCRIPT_DIR}/run-k3s-validation.sh" >"${TEST_ROOT}/api-failure.log" 2>&1 || true
grep -Fq 'Injected test failure after after-namespace-create-api.' \
    "${TEST_ROOT}/api-failure.log" || {
    sed -n '1,160p' "${TEST_ROOT}/api-failure.log" >&2
    exit 1
}
[[ ! -e "${TEST_ROOT}/state/namespace.json" ]]
[[ -z "$(matching_process_ids '--accept-paths=^/api/v1/namespaces/t00-k3s-acquireapi$')" ]]

printf 'k3s_post_create_identity_failures=passed\n'
printf 'k3s_post_create_digest_failure=passed\n'
printf 'k3s_transient_ctr_failure=passed\n'
printf 'k3s_post_create_api_failure=passed\n'
