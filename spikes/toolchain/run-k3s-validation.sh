#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly NAMESPACE="${TOOLCHAIN_K3S_NAMESPACE:-t00-k3s-toolchain-validation}"
readonly PROFILE_NAME=t00-k3s-chrome-bbd643f78d48.json
readonly PROFILE_SHA256=bbd643f78d48b477111dd8597a69ba6bee4db68ce199dbf09d87bf90a1377f46

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

k() {
    "${KUBECTL[@]}" "$@"
}

cleanup() {
    if k get namespace "${NAMESPACE}" >/dev/null 2>&1; then
        k delete namespace "${NAMESPACE}" --wait=true --timeout=120s >/dev/null
    fi
}

wait_for_phase() {
    local pod="$1"
    local phase=""
    local attempt
    for attempt in {1..180}; do
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
    jq --arg name "${name}" \
        ".metadata.name=\$name | .metadata.namespace=\"${NAMESPACE}\" | ${filter}" \
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
trap 'cleanup; rm -rf -- "${RESULTS}"' EXIT

k create namespace "${NAMESPACE}" >/dev/null
k label namespace "${NAMESPACE}" t00.g1lom.xyz/owned=true >/dev/null
jq -n --arg namespace "${NAMESPACE}" '{
    apiVersion: "networking.k8s.io/v1",
    kind: "NetworkPolicy",
    metadata: {
        name: "deny-document-network",
        namespace: $namespace,
        labels: {"t00.g1lom.xyz/owned": "true"}
    },
    spec: {
        podSelector: {matchLabels: {"t00.g1lom.xyz/network-denied": "true"}},
        policyTypes: ["Ingress", "Egress"],
        ingress: [],
        egress: []
    }
}' | k apply -f - >/dev/null
k -n "${NAMESPACE}" apply -f "${SCRIPT_DIR}/k3s-network-target.yaml" >/dev/null
k -n "${NAMESPACE}" wait --for=condition=Ready pod/network-target --timeout=90s >/dev/null

apply_variant toolchain-success '.'
expect_success toolchain-success \
    'security_properties=passed' \
    'chrome_sandbox=passed' \
    'validation_scope=target' \
    'work_storage=disk' \
    'validation=passed'

k -n "${NAMESPACE}" get pod toolchain-success -o json | jq -e \
    --arg profile "${PROFILE_NAME}" '
    .spec.automountServiceAccountToken == false
    and .spec.securityContext.runAsNonRoot == true
    and .spec.securityContext.runAsUser == 1000710000
    and .spec.securityContext.seccompProfile == {
        type: "Localhost", localhostProfile: $profile
    }
    and .spec.containers[0].securityContext.allowPrivilegeEscalation == false
    and .spec.containers[0].securityContext.readOnlyRootFilesystem == true
    and .spec.containers[0].securityContext.capabilities.drop == ["ALL"]
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
apply_variant missing-localhost-profile '
    .spec.securityContext.seccompProfile={
        "type":"Localhost",
        "localhostProfile":"t00-k3s-chrome-bbd643f78d48.missing.json"
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
for attempt in {1..90}; do
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
