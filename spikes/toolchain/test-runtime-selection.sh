#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
RESULTS="$(mktemp -d)"
readonly RESULTS
trap 'rm -rf -- "${RESULTS}"' EXIT

FAKE_BIN="${RESULTS}/bin"
RUNTIME_LOG="${RESULTS}/runtime.log"
readonly FAKE_BIN RUNTIME_LOG
mkdir "${FAKE_BIN}"
export RUNTIME_LOG

for runtime in docker podman; do
    sed "s/@RUNTIME@/${runtime}/g" >"${FAKE_BIN}/${runtime}" <<'EOF'
#!/usr/bin/env bash
printf '%s' '@RUNTIME@' >>"${RUNTIME_LOG}"
printf ' <%s>' "$@" >>"${RUNTIME_LOG}"
printf '\n' >>"${RUNTIME_LOG}"
if [[ "${FAKE_RUNTIME_FAIL_COMMAND:-}" == "${1:-}" ]]; then
    exit "${FAKE_RUNTIME_EXIT_CODE:-1}"
fi
EOF
    chmod 0755 "${FAKE_BIN}/${runtime}"
done

run_probe() {
    PATH="${FAKE_BIN}:${PATH}" "${SCRIPT_DIR}/run-validation.sh" "$@"
}

assert_contains() {
    local expected="$1"
    local file="$2"
    grep -Fq -- "${expected}" "${file}" || {
        printf 'Expected %q in %s.\n' "${expected}" "${file}" >&2
        exit 1
    }
}

assert_not_contains() {
    local unexpected="$1"
    local file="$2"
    if grep -Fq -- "${unexpected}" "${file}"; then
        printf 'Did not expect %q in %s.\n' "${unexpected}" "${file}" >&2
        exit 1
    fi
}

run_probe security
assert_contains 'docker <build>' "${RUNTIME_LOG}"
assert_contains 'docker <run>' "${RUNTIME_LOG}"
assert_contains ' <--network> <none>' "${RUNTIME_LOG}"
assert_contains ' <--cap-drop> <ALL>' "${RUNTIME_LOG}"
assert_contains ' <--security-opt> <no-new-privileges=true>' "${RUNTIME_LOG}"
assert_not_contains 'podman ' "${RUNTIME_LOG}"

: >"${RUNTIME_LOG}"
run_probe --runtime podman security
assert_contains 'podman <build>' "${RUNTIME_LOG}"
assert_contains 'podman <run>' "${RUNTIME_LOG}"
assert_contains ' <--read-only>' "${RUNTIME_LOG}"
assert_contains ' <--read-only-tmpfs=false>' "${RUNTIME_LOG}"
assert_contains ' <--uidmap> <1000710000:1:1>' "${RUNTIME_LOG}"
assert_contains ' <type=tmpfs,destination=/work,tmpfs-size=1g,tmpfs-mode=0770,U=true>' \
    "${RUNTIME_LOG}"
assert_not_contains 'docker ' "${RUNTIME_LOG}"

: >"${RUNTIME_LOG}"
run_probe --runtime podman target
assert_contains \
    " <--security-opt> <seccomp=${SCRIPT_DIR}/chrome-seccomp.json>" \
    "${RUNTIME_LOG}"
assert_not_contains ' <seccomp=unconfined>' "${RUNTIME_LOG}"
assert_not_contains ' <--privileged>' "${RUNTIME_LOG}"
assert_not_contains ' <--cap-add>' "${RUNTIME_LOG}"
assert_not_contains ' <--network> <host>' "${RUNTIME_LOG}"
assert_not_contains ' <--no-sandbox>' "${RUNTIME_LOG}"

if TOOLCHAIN_CHROME_SECCOMP_MODE=unsupported run_probe --runtime podman target \
    >"${RESULTS}/seccomp-mode.out" 2>"${RESULTS}/seccomp-mode.err"; then
    printf 'An unsupported Chrome seccomp mode unexpectedly succeeded.\n' >&2
    exit 1
fi
assert_contains 'Unknown Chrome seccomp mode: unsupported' "${RESULTS}/seccomp-mode.err"

if run_probe --runtime nerdctl security \
    >"${RESULTS}/unknown.out" 2>"${RESULTS}/unknown.err"; then
    printf 'An unsupported runtime unexpectedly succeeded.\n' >&2
    exit 1
fi
assert_contains 'Unknown container runtime: nerdctl' "${RESULTS}/unknown.err"

MISSING_BIN="${RESULTS}/missing-bin"
readonly MISSING_BIN
mkdir "${MISSING_BIN}"
ln -s /usr/bin/dirname "${MISSING_BIN}/dirname"
if PATH="${MISSING_BIN}" /usr/bin/bash "${SCRIPT_DIR}/run-validation.sh" \
    --runtime podman security >"${RESULTS}/missing.out" 2>"${RESULTS}/missing.err"; then
    printf 'A missing runtime unexpectedly succeeded.\n' >&2
    exit 1
fi
assert_contains 'Container runtime not found: podman' "${RESULTS}/missing.err"

: >"${RUNTIME_LOG}"
if PATH="${FAKE_BIN}:${PATH}" \
    FAKE_RUNTIME_FAIL_COMMAND=build \
    FAKE_RUNTIME_EXIT_CODE=23 \
    "${SCRIPT_DIR}/run-validation.sh" --runtime podman security; then
    printf 'A failed runtime build unexpectedly succeeded.\n' >&2
    exit 1
else
    status=$?
fi
[[ "${status}" == 23 ]] || {
    printf 'Runtime failure exited with %s instead of 23.\n' "${status}" >&2
    exit 1
}
assert_contains 'podman <build>' "${RUNTIME_LOG}"
assert_not_contains 'podman <run>' "${RUNTIME_LOG}"

before_disk_work="$(find "${SCRIPT_DIR}" -maxdepth 1 -type d -name '.t00-work.*' | wc -l)"
readonly before_disk_work
: >"${RUNTIME_LOG}"
if PATH="${FAKE_BIN}:${PATH}" \
    FAKE_RUNTIME_FAIL_COMMAND=run \
    FAKE_RUNTIME_EXIT_CODE=42 \
    TOOLCHAIN_WORK_STORAGE=disk \
    "${SCRIPT_DIR}/run-validation.sh" --runtime podman security; then
    printf 'A failed runtime execution unexpectedly succeeded.\n' >&2
    exit 1
else
    status=$?
fi
[[ "${status}" == 42 ]] || {
    printf 'Runtime execution exited with %s instead of 42.\n' "${status}" >&2
    exit 1
}
after_disk_work="$(find "${SCRIPT_DIR}" -maxdepth 1 -type d -name '.t00-work.*' | wc -l)"
readonly after_disk_work
[[ "${before_disk_work}" == "${after_disk_work}" ]] || {
    printf 'Disk-backed work directory was not cleaned up.\n' >&2
    exit 1
}
assert_contains ' <--entrypoint> </usr/bin/find>' "${RUNTIME_LOG}"
assert_contains ' <--network> <none>' "${RUNTIME_LOG}"
assert_contains ' <--cap-drop> <ALL>' "${RUNTIME_LOG}"

assert_contains 'Usage: run-validation.sh [--runtime docker|podman]' \
    <("${SCRIPT_DIR}/run-validation.sh" --help)
assert_contains 'Usage: test-validation.sh [--runtime docker|podman]' \
    <("${SCRIPT_DIR}/test-validation.sh" --help)

printf 'runtime-selection-tests=passed\n'
