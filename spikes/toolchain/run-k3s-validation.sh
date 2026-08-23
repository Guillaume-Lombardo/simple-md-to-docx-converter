#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
# Used by the sourced ownership guards.
# shellcheck disable=SC2034
PRIVILEGE=()
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/k3s-resource-guards.sh"
readonly RUN_ID="${TOOLCHAIN_K3S_RUN_ID:-}"
readonly IMAGE="${TOOLCHAIN_K3S_IMAGE:-}"
readonly PROFILE_NAME="${TOOLCHAIN_K3S_PROFILE_NAME:-}"
readonly PROFILE_SHA256=bbd643f78d48b477111dd8597a69ba6bee4db68ce199dbf09d87bf90a1377f46

[[ "${RUN_ID}" =~ ^[a-z0-9][a-z0-9-]{7,39}$ ]] || {
    printf 'TOOLCHAIN_K3S_RUN_ID must be an 8-40 character lowercase run identifier.\n' >&2
    exit 2
}
[[ "${IMAGE}" =~ ^[a-zA-Z0-9./:_-]+$ ]] || {
    printf 'TOOLCHAIN_K3S_IMAGE is missing or invalid.\n' >&2
    exit 2
}
[[ "${PROFILE_NAME}" =~ ^t00-k3s-chrome-[a-z0-9-]+\.json$ ]] || {
    printf 'TOOLCHAIN_K3S_PROFILE_NAME is missing or invalid.\n' >&2
    exit 2
}
readonly NAMESPACE="t00-k3s-${RUN_ID}"

KUBECTL=(kubectl)
if [[ "${1:-}" == "--sudo-k3s" ]]; then
    KUBECTL=(sudo /usr/local/bin/k3s kubectl)
    shift
fi
if (($#)); then
    printf 'Usage: run-k3s-validation.sh [--sudo-k3s]\n' >&2
    exit 2
fi

command -v jq >/dev/null 2>&1 || {
    printf 'jq is required for the k3s validation probes.\n' >&2
    exit 127
}
command -v curl >/dev/null 2>&1 || {
    printf 'curl is required for UID-preconditioned namespace cleanup.\n' >&2
    exit 127
}

k() {
    "${KUBECTL[@]}" "$@"
}

namespace_is_owned() {
    local namespace_json="$1"
    namespace_json_is_owned "${namespace_json}" "${NAMESPACE}" "${NAMESPACE_UID}" "${RUN_ID}"
}

cleanup_namespace() {
    [[ -n "${NAMESPACE_UID:-}" ]] || return 0
    local namespace_json
    if ! namespace_json="$(
        k get namespace "${NAMESPACE}" --ignore-not-found -o json 2>/dev/null
    )"; then
        printf 'Refusing namespace cleanup: Kubernetes ownership cannot be verified.\n' >&2
        return 1
    fi
    [[ -n "${namespace_json}" ]] || return 0
    namespace_is_owned "${namespace_json}" || {
        printf 'Refusing to delete namespace %s: ownership metadata changed.\n' \
            "${NAMESPACE}" >&2
        return 1
    }
    delete_namespace_with_uid
}

stop_api_proxy() {
    [[ -n "${API_PROXY_PID:-}" ]] || return 0
    if kill -0 "${API_PROXY_PID}" 2>/dev/null; then
        kill "${API_PROXY_PID}" 2>/dev/null || true
        wait "${API_PROXY_PID}" 2>/dev/null || true
    fi
    API_PROXY_PID=""
}

delete_namespace_with_uid() {
    local delete_options="${RESULTS}/namespace-delete-options.json"
    local proxy_log="${RESULTS}/kubectl-proxy.log"
    local proxy_port=""
    k proxy --address=127.0.0.1 --port=0 \
        --accept-hosts='^localhost$,^127\.0\.0\.1$' \
        --accept-paths="^/api/v1/namespaces/${NAMESPACE}$" \
        >"${proxy_log}" 2>&1 &
    API_PROXY_PID=$!
    for _ in {1..30}; do
        proxy_port="$(sed -n 's/^Starting to serve on 127\.0\.0\.1:\([0-9][0-9]*\)$/\1/p' \
            "${proxy_log}")"
        [[ -n "${proxy_port}" ]] && break
        kill -0 "${API_PROXY_PID}" 2>/dev/null || break
        sleep 0.1
    done
    [[ -n "${proxy_port}" ]] || {
        printf 'Kubernetes API proxy did not expose its loopback port.\n' >&2
        stop_api_proxy
        return 1
    }
    jq -n --arg uid "${NAMESPACE_UID}" '{
        apiVersion: "v1",
        kind: "DeleteOptions",
        propagationPolicy: "Foreground",
        preconditions: {uid: $uid}
    }' >"${delete_options}"
    if ! curl --fail-with-body --silent --show-error \
        --request DELETE \
        --header 'Content-Type: application/json' \
        --data-binary "@${delete_options}" \
        "http://127.0.0.1:${proxy_port}/api/v1/namespaces/${NAMESPACE}" >/dev/null; then
        stop_api_proxy
        return 1
    fi
    stop_api_proxy
    k wait --for=delete "namespace/${NAMESPACE}" --timeout=120s >/dev/null
}

on_exit() {
    local status=$?
    trap - EXIT
    stop_api_proxy
    if ! cleanup_namespace; then
        status=1
    fi
    rm -rf -- "${RESULTS}"
    exit "${status}"
}

wait_for_phase() {
    local pod="$1"
    local phase=""
    for _ in {1..180}; do
        phase="$(
            k -n "${NAMESPACE}" get pod "${pod}" \
                -o jsonpath='{.status.phase}' 2>/dev/null || true
        )"
        if [[ "${phase}" == Succeeded || "${phase}" == Failed ]]; then
            printf '%s\n' "${phase}"
            return 0
        fi
        sleep 1
    done
    printf 'Timed out waiting for pod %s; last phase was %s.\n' "${pod}" "${phase}" >&2
    return 1
}

apply_variant() {
    local name="$1"
    local filter="$2"
    jq --arg name "${name}" --arg namespace "${NAMESPACE}" \
        --arg run_id "${RUN_ID}" --arg image "${IMAGE}" --arg profile "${PROFILE_NAME}" \
        ".metadata.name=\$name
        | .metadata.namespace=\$namespace
        | .metadata.labels[\"t00.g1lom.xyz/run-id\"]=\$run_id
        | .metadata.annotations[\"t00.g1lom.xyz/run-id\"]=\$run_id
        | .spec.securityContext.seccompProfile.localhostProfile=\$profile
        | .spec.initContainers[].image=\$image
        | .spec.containers[].image=\$image
        | ${filter}" \
        "${SCRIPT_DIR}/k3s-validation-pod.json" | k apply -f - >/dev/null
}

expect_success() {
    local pod="$1"
    shift
    local log_file="${RESULTS}/${pod}.log"
    [[ "$(wait_for_phase "${pod}")" == Succeeded ]] || {
        k -n "${NAMESPACE}" logs "${pod}" >&2 || true
        printf 'Pod %s did not succeed.\n' "${pod}" >&2
        return 1
    }
    k -n "${NAMESPACE}" logs "${pod}" >"${log_file}"
    local expected
    for expected in "$@"; do
        grep -Fq "${expected}" "${log_file}"
    done
}

expect_failure() {
    local pod="$1"
    local expected="$2"
    local log_file="${RESULTS}/${pod}.log"
    [[ "$(wait_for_phase "${pod}")" == Failed ]] || {
        printf 'Pod %s unexpectedly succeeded.\n' "${pod}" >&2
        return 1
    }
    k -n "${NAMESPACE}" logs "${pod}" >"${log_file}" 2>&1 || true
    grep -Eq "${expected}" "${log_file}" || {
        printf 'Pod %s failed without the expected diagnostic.\n' "${pod}" >&2
        cat "${log_file}" >&2
        return 1
    }
}

printf '%s  %s\n' "${PROFILE_SHA256}" "${SCRIPT_DIR}/chrome-seccomp.json" \
    | sha256sum --check --strict >/dev/null
k version >/dev/null
if k get namespace "${NAMESPACE}" >/dev/null 2>&1; then
    printf 'Refusing to reuse existing namespace %s.\n' "${NAMESPACE}" >&2
    exit 1
fi

RESULTS="$(mktemp -d)"
readonly RESULTS
NAMESPACE_UID=""
API_PROXY_PID=""
trap on_exit EXIT

namespace_json="$(jq -n --arg name "${NAMESPACE}" --arg run_id "${RUN_ID}" '{
    apiVersion: "v1",
    kind: "Namespace",
    metadata: {
        name: $name,
        labels: {
            "t00.g1lom.xyz/owned": "true",
            "t00.g1lom.xyz/run-id": $run_id
        },
        annotations: {"t00.g1lom.xyz/run-id": $run_id}
    }
}' | k create -f - -o json)"
NAMESPACE_UID="$(jq -er '.metadata.uid' <<<"${namespace_json}")"
readonly NAMESPACE_UID

jq -n --arg namespace "${NAMESPACE}" --arg run_id "${RUN_ID}" '{
    apiVersion: "networking.k8s.io/v1",
    kind: "NetworkPolicy",
    metadata: {
        name: "deny-document-network",
        namespace: $namespace,
        labels: {
            "t00.g1lom.xyz/owned": "true",
            "t00.g1lom.xyz/run-id": $run_id
        },
        annotations: {"t00.g1lom.xyz/run-id": $run_id}
    },
    spec: {
        podSelector: {matchLabels: {"t00.g1lom.xyz/network-denied": "true"}},
        policyTypes: ["Ingress", "Egress"],
        ingress: [],
        egress: []
    }
}' | k apply -f - >/dev/null
sed -e "s|localhost/simple-md-toolchain:t00|${IMAGE}|g" \
    -e "s|T00_RUN_ID|${RUN_ID}|g" \
    "${SCRIPT_DIR}/k3s-network-target.yaml" | k -n "${NAMESPACE}" apply -f - >/dev/null
k -n "${NAMESPACE}" wait --for=condition=Ready pod/network-target --timeout=90s >/dev/null

apply_variant toolchain-success '.'
expect_success toolchain-success \
    'security_properties=passed' \
    'chrome_sandbox=passed' \
    'validation_scope=target' \
    'work_storage=disk' \
    'validation=passed'

k -n "${NAMESPACE}" get pod toolchain-success -o json | jq -e \
    --arg profile "${PROFILE_NAME}" --arg image "${IMAGE}" --arg run_id "${RUN_ID}" '
    .metadata.labels["t00.g1lom.xyz/run-id"] == $run_id
    and .metadata.annotations["t00.g1lom.xyz/run-id"] == $run_id
    and .spec.automountServiceAccountToken == false
    and .spec.securityContext.runAsNonRoot == true
    and .spec.securityContext.runAsUser == 1000710000
    and .spec.securityContext.seccompProfile == {
        type: "Localhost", localhostProfile: $profile
    }
    and .spec.containers[0].securityContext.allowPrivilegeEscalation == false
    and .spec.containers[0].securityContext.readOnlyRootFilesystem == true
    and .spec.containers[0].securityContext.capabilities.drop == ["ALL"]
    and .spec.containers[0].image == $image
    and .spec.containers[0].resources.limits == {
        cpu: "2", memory: "2Gi", "ephemeral-storage": "1Gi"
    }
    and ([.spec.volumes[] | select(.name == "tmp")][0].emptyDir
        == {medium: "Memory", sizeLimit: "512Mi"})
    and ([.spec.volumes[] | select(.name == "work")][0].emptyDir
        == {sizeLimit: "1Gi"})
    and ([.spec.volumes[] | select(.name == "shm")][0].emptyDir
        == {medium: "Memory", sizeLimit: "128Mi"})
' >/dev/null
k -n "${NAMESPACE}" get networkpolicy deny-document-network -o json | jq -e '
    .spec.podSelector.matchLabels["t00.g1lom.xyz/network-denied"] == "true"
    and (.spec.policyTypes | sort) == ["Egress", "Ingress"]
    and (.spec.ingress // []) == []
    and (.spec.egress // []) == []
' >/dev/null
for resource in pod/network-target service/network-target; do
    k -n "${NAMESPACE}" get "${resource}" -o json | jq -e --arg run_id "${RUN_ID}" '
        .metadata.labels["t00.g1lom.xyz/owned"] == "true"
        and .metadata.labels["t00.g1lom.xyz/run-id"] == $run_id
        and .metadata.annotations["t00.g1lom.xyz/run-id"] == $run_id
    ' >/dev/null
done

apply_variant network-control '
    .metadata.labels["t00.g1lom.xyz/network-denied"]="false"
    | (.spec.containers[0].env[] | select(.name == "EXPECTED_NETWORK_ACCESS").value)="allowed"
    | (.spec.containers[0].env[] | select(.name == "VALIDATION_SCOPE").value)="security"
'
expect_success network-control 'security_properties=passed'

apply_variant runtime-default '
    .spec.securityContext.seccompProfile={"type":"RuntimeDefault"}
'
apply_variant wrong-uid '
    .spec.securityContext.runAsUser=1000710001
    | (.spec.containers[0].env[] | select(.name == "VALIDATION_SCOPE").value)="security"
'
apply_variant added-capability '
    .spec.containers[0].securityContext.capabilities.add=["NET_RAW"]
    | (.spec.containers[0].env[] | select(.name == "VALIDATION_SCOPE").value)="security"
'
apply_variant privilege-escalation '
    .spec.containers[0].securityContext.allowPrivilegeEscalation=true
    | (.spec.containers[0].env[] | select(.name == "VALIDATION_SCOPE").value)="security"
'
apply_variant unconfined-seccomp '
    .spec.securityContext.seccompProfile={"type":"Unconfined"}
    | (.spec.containers[0].env[] | select(.name == "VALIDATION_SCOPE").value)="security"
'
apply_variant writable-root '
    .spec.containers[0].securityContext.readOnlyRootFilesystem=false
    | (.spec.containers[0].env[] | select(.name == "VALIDATION_SCOPE").value)="security"
'
apply_variant unbounded-resources '
    del(.spec.containers[0].resources)
    | (.spec.containers[0].env[] | select(.name == "VALIDATION_SCOPE").value)="security"
'
apply_variant missing-work '
    .spec.containers[0].volumeMounts |= map(select(.name != "work"))
    | .spec.volumes |= map(select(.name != "work"))
    | (.spec.containers[0].env[] | select(.name == "VALIDATION_SCOPE").value)="security"
'
# `$profile` is deliberately evaluated by jq inside apply_variant.
# shellcheck disable=SC2016
apply_variant missing-localhost-profile '
    .spec.securityContext.seccompProfile={
        "type":"Localhost",
        "localhostProfile":($profile | sub("\\.json$"; ".missing.json"))
    }
'

expect_failure runtime-default \
    'zygote_host_impl_linux.cc|Failed to move to new namespace'
expect_failure wrong-uid 'runtime UID does not match EXPECTED_UID=1000710000'
expect_failure added-capability 'capabilit(y|ies).*not empty'
expect_failure privilege-escalation 'no-new-privileges is not enabled'
expect_failure unconfined-seccomp 'seccomp filter mode is not enabled'
expect_failure writable-root 'the container root filesystem is writable'
expect_failure unbounded-resources 'memory is not bounded by cgroup'
expect_failure missing-work '/work is not a dedicated writable disk-backed mount'

missing_message=""
for _ in {1..90}; do
    missing_message="$(
        k -n "${NAMESPACE}" get pod missing-localhost-profile \
            -o jsonpath='{.status.initContainerStatuses[0].state.waiting.message}' \
            2>/dev/null || true
    )"
    [[ "${missing_message}" == *"cannot load seccomp profile"* ]] && break
    sleep 1
done
[[ "${missing_message}" == *"${PROFILE_NAME%.json}.missing.json"* ]] || {
    printf 'Missing Localhost profile did not fail closed: %s\n' "${missing_message}" >&2
    exit 1
}

printf 'k3s_success_probe=passed\n'
printf 'k3s_network_policy_probe=passed\n'
printf 'k3s_runtime_default_failure=passed\n'
printf 'k3s_security_failure_probes=passed\n'
printf 'k3s_missing_profile_failure=passed\n'
