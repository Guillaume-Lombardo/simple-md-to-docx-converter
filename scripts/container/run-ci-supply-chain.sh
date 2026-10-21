#!/usr/bin/env bash
set -euo pipefail

# Produce the SBOM and vulnerability evidence for the final backend and
# reverse-attempt images that `CI / build images` built once for this run. The
# workflow loads them only after their archive, loaded, and tagged image IDs
# equal the build job's outputs, and `supply-chain.sh` binds every report to
# that exact image ID. The gate and the fresh Grype database are unchanged.
readonly final_image=localhost/md-converter:ci
readonly reverse_attempt_image=localhost/md-converter-reverse-attempt:ci

test "$(podman info --format '{{.Host.Security.Rootless}}')" = true
podman image exists "$final_image"
podman image exists "$reverse_attempt_image"
# Scan both images even when the first is rejected, so the retained evidence
# covers every image, then fail with the first rejection.
status=0
bash scripts/container/supply-chain.sh "$final_image" artifacts/container ||
  status=$?
bash scripts/container/supply-chain.sh \
  "$reverse_attempt_image" artifacts/reverse-attempt ci reverse-attempt ||
  status=$((status == 0 ? $? : status))
exit "$status"
