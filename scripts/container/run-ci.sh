#!/usr/bin/env bash
set -euo pipefail

# Validate the final backend and reverse-attempt images that `CI / build images`
# built once for this run. The workflow loads them only after their archive,
# loaded, and tagged image IDs equal the build job's outputs.
readonly final_image=localhost/md-converter:ci
readonly reverse_attempt_image=localhost/md-converter-reverse-attempt:ci
readonly evidence_directory=artifacts/container-diagnostics

mkdir -p "$evidence_directory"
printf 'Final-image validation started.\n' > "$evidence_directory/ci-status.txt"
export MARKWEAVE_CONTAINER_EVIDENCE_DIRECTORY="$evidence_directory"
record_ci_status() {
  local exit_code="$?"
  if [[ "$exit_code" -eq 0 ]]; then
    printf 'Final-image validation passed.\n' > "$evidence_directory/ci-status.txt"
  else
    printf 'Final-image validation failed with exit code %s.\n' "$exit_code" \
      > "$evidence_directory/ci-status.txt"
  fi
}
trap record_ci_status EXIT

test "$(podman info --format '{{.Host.Security.Rootless}}')" = true
podman image exists "$final_image"
podman image exists "$reverse_attempt_image"
bash scripts/container/smoke-reverse-attempt.sh "$reverse_attempt_image"
MARKWEAVE_T70_PODMAN_TEST_IMAGE="$reverse_attempt_image" \
  uv run pytest tests/integration/broker \
    -m integration --no-cov
bash scripts/container/smoke.sh "$final_image"
bash tests/e2e/runtime-operations-final-image.sh "$final_image"
bash tests/e2e/local-conversion-final-image.sh "$final_image"
bash scripts/container/api-smoke.sh "$final_image"
bash scripts/container/distributed-api-smoke.sh "$final_image"
bash scripts/container/recovery-cli-smoke.sh "$final_image"
