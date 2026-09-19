#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
# Used by the sourced ownership guards.
# shellcheck disable=SC2034
PRIVILEGE=()
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/k3s-resource-guards.sh"

TEST_ROOT="$(mktemp -d)"
readonly TEST_ROOT
trap 'rm -rf -- "${TEST_ROOT}"' EXIT
readonly RUN_ID=t00guardtest
readonly EXPECTED_HASH=ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb

target="${TEST_ROOT}/profile.json"
marker="${TEST_ROOT}/profile.owner"
printf 'pre-existing\n' >"${target}"
if guard_resources_absent "${target}" "${marker}" 2>/dev/null; then
    printf 'A pre-existing profile unexpectedly passed collision checks.\n' >&2
    exit 1
fi
[[ "$(<"${target}")" == pre-existing ]]
rm -- "${target}"

printf 'foreign\n' >"${marker}"
if guard_resources_absent "${target}" "${marker}" 2>/dev/null; then
    printf 'A pre-existing ownership marker unexpectedly passed collision checks.\n' >&2
    exit 1
fi
rm -- "${marker}"

printf 'a' >"${target}"
printf '%s\n' "${RUN_ID}" >"${marker}"
target_identity="$(guard_identity "${target}")"
marker_identity="$(guard_identity "${marker}")"
guard_owned_file "${target}" "${marker}" "${RUN_ID}" "${EXPECTED_HASH}" \
    "${target_identity}" "${marker_identity}"

printf 'tampered' >"${target}"
if remove_owned_file "${target}" "${marker}" "${RUN_ID}" "${EXPECTED_HASH}" \
    "${target_identity}" "${marker_identity}" 2>/dev/null; then
    printf 'A tampered installed profile was unexpectedly removed.\n' >&2
    exit 1
fi
[[ -e "${target}" && -e "${marker}" ]]

printf 'a' >"${target}"
target_identity="$(guard_identity "${target}")"
printf 'foreign\n' >"${marker}"
if remove_owned_file "${target}" "${marker}" "${RUN_ID}" "${EXPECTED_HASH}" \
    "${target_identity}" "${marker_identity}" 2>/dev/null; then
    printf 'A profile with a changed owner marker was unexpectedly removed.\n' >&2
    exit 1
fi
[[ -e "${target}" && -e "${marker}" ]]

printf '%s\n' "${RUN_ID}" >"${marker}"
marker_identity="$(guard_identity "${marker}")"
remove_owned_file "${target}" "${marker}" "${RUN_ID}" "${EXPECTED_HASH}" \
    "${target_identity}" "${marker_identity}"
[[ ! -e "${target}" && ! -e "${marker}" ]]

owned_namespace="$(jq -n --arg run_id "${RUN_ID}" '{metadata: {
    name: "t00-k3s-t00guardtest",
    uid: "namespace-uid",
    labels: {"t00.g1lom.xyz/owned": "true", "t00.g1lom.xyz/run-id": $run_id},
    annotations: {"t00.g1lom.xyz/run-id": $run_id}
}}')"
namespace_json_is_owned "${owned_namespace}" t00-k3s-t00guardtest namespace-uid "${RUN_ID}"
if namespace_json_is_owned "${owned_namespace}" t00-k3s-t00guardtest foreign-uid \
    "${RUN_ID}"; then
    printf 'A namespace with the wrong immutable UID unexpectedly passed ownership checks.\n' >&2
    exit 1
fi
changed_namespace="$(jq '.metadata.annotations["t00.g1lom.xyz/run-id"]="foreign"' \
    <<<"${owned_namespace}")"
if namespace_json_is_owned "${changed_namespace}" t00-k3s-t00guardtest namespace-uid \
    "${RUN_ID}"; then
    printf 'A namespace with changed ownership metadata unexpectedly passed checks.\n' >&2
    exit 1
fi

image_digest_is_owned "sha256:$(printf 'a%.0s' {1..64})" \
    "sha256:$(printf 'a%.0s' {1..64})"
image_reference_is_absent ""
if image_reference_is_absent "sha256:$(printf 'a%.0s' {1..64})"; then
    printf 'A pre-existing image reference unexpectedly passed collision checks.\n' >&2
    exit 1
fi
if image_digest_is_owned "sha256:$(printf 'b%.0s' {1..64})" \
    "sha256:$(printf 'a%.0s' {1..64})"; then
    printf 'A replaced image digest unexpectedly passed ownership checks.\n' >&2
    exit 1
fi

# The literal verifies that cleanup sends the captured UID as an API
# DeleteOptions precondition rather than issuing a name-only deletion.
# shellcheck disable=SC2016
grep -Fq -- 'preconditions: {uid: $uid}' \
    "${SCRIPT_DIR}/run-k3s-validation.sh"

printf 'k3s_resource_collision_probes=passed\n'
printf 'k3s_profile_tampering_cleanup_refusal=passed\n'
printf 'k3s_namespace_ownership_probes=passed\n'
printf 'k3s_image_ownership_probes=passed\n'
