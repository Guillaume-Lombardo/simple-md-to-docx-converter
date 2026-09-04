#!/usr/bin/env bash
set -euo pipefail

readonly base_digest=sha256:194df4e35e0e5467e1b57266f4d61f821e1b1f567135f074d23066d3604ae653
readonly base_image="registry.access.redhat.com/ubi9/python-314@$base_digest"
readonly final_image=localhost/md-converter:t20-ci
readonly reverse_attempt_image=localhost/markweave-reverse-attempt:t70-ci
readonly frontend_image=localhost/md-converter-web:t64-ci
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
podman pull --quiet "$base_image"
test "$(podman image inspect "$base_image" --format '{{.Digest}}')" = "$base_digest"
bash scripts/container/build.sh "$final_image"
bash scripts/container/build-reverse-attempt.sh "$reverse_attempt_image"
bash scripts/container/smoke-reverse-attempt.sh "$reverse_attempt_image"
MARKWEAVE_T70_PODMAN_TEST_IMAGE="$reverse_attempt_image" \
  uv run pytest tests/integration/broker/test_podman_runtime_integration.py \
    -m integration --no-cov
source_date_epoch="$(git show -s --format=%ct HEAD)"
readonly source_date_epoch
podman build --format oci --timestamp "$source_date_epoch" \
  --tag "$frontend_image" --file web/Containerfile .
frontend_image_id="$(podman image inspect "$frontend_image" --format '{{.Id}}')"
readonly frontend_image_id
[[ "$frontend_image_id" =~ ^[0-9a-f]{64}$ ]]
readonly frontend_archive="$evidence_directory/frontend-image.oci.tar"
podman save --format oci-archive --output "$frontend_archive" "$frontend_image_id"
uv run python -m scripts.container.verify_oci_export \
  --archive "$frontend_archive" --expected-image-id "sha256:$frontend_image_id" \
  | tee "$evidence_directory/frontend-oci-identity.txt"
bash scripts/container/smoke.sh "$final_image"
bash tests/e2e/runtime-operations-final-image.sh "$final_image"
bash scripts/container/api-smoke.sh "$final_image"
bash scripts/container/distributed-api-smoke.sh "$final_image"
bash scripts/container/recovery-cli-smoke.sh "$final_image"
bash scripts/container/supply-chain.sh "$final_image" artifacts/container
bash scripts/container/supply-chain.sh \
  "$reverse_attempt_image" artifacts/reverse-attempt ci reverse-attempt
