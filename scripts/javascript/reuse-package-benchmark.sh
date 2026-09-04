#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 || "$1" == -* || "$2" == -* || "$3" == -* ]]; then
  echo "Usage: reuse-package-benchmark.sh CANDIDATE_REF ARTIFACT_DIRECTORY METADATA_RECEIPT" >&2
  exit 2
fi

readonly candidate_ref="$1"
readonly artifact_directory="$2"
readonly metadata_receipt="$3"
readonly accepted_ref="da26ad780ac11d099e764aa82a0430e684bbf4c3"
readonly accepted_run="33799673333"
readonly accepted_artifact_id="9911803951"
readonly accepted_artifact_digest="sha256:90311dccb8db14a017050120f84379ba61b96ba69a6dccf5a379c2a2a4e48a0c"
readonly accepted_surface_digest="f8368503367543660e8f3e75db9652b92379c524e0dc56562f0a7a00cc2bc3f6"
repository="$(git rev-parse --show-toplevel)"
readonly repository
readonly -a surfaces=(
  .containerignore
  package.json
  pnpm-lock.yaml
  pnpm-workspace.yaml
  scripts/javascript/bootstrap-pnpm.sh
  web
)

git -C "$repository" cat-file -e "$accepted_ref^{commit}"
candidate_commit="$(git -C "$repository" rev-parse --verify "$candidate_ref^{commit}")"
readonly candidate_commit
git -C "$repository" merge-base --is-ancestor "$accepted_ref" "$candidate_commit"
if ! git -C "$repository" diff --quiet "$accepted_ref..$candidate_commit" -- \
  "${surfaces[@]}"; then
  echo "Accepted T67 benchmark cannot be reused: a performance-sensitive surface changed:" >&2
  git -C "$repository" diff --name-only "$accepted_ref..$candidate_commit" -- \
    "${surfaces[@]}" >&2
  exit 1
fi

surface_digest="$({
  git -C "$repository" ls-tree -r "$candidate_commit" -- "${surfaces[@]}"
} | LC_ALL=C sort | sha256sum | awk '{print $1}')"
readonly surface_digest
test "$surface_digest" = "$accepted_surface_digest"

expected_files="$({
  printf '%s:f\n' commands.txt environment.txt images.tsv manifest-lock-sha256.txt \
    raw.log sizes.tsv timings.tsv
} | LC_ALL=C sort)"
actual_files="$({
  find "$artifact_directory" -mindepth 1 -maxdepth 1 -printf '%f:%y\n'
} | LC_ALL=C sort)"
readonly expected_files actual_files
if [[ "$actual_files" != "$expected_files" ]]; then
  echo "Accepted T67 benchmark artifact has an unexpected regular-file set." >&2
  diff -u <(printf '%s\n' "$expected_files") <(printf '%s\n' "$actual_files") >&2 || true
  exit 1
fi
(
  cd "$artifact_directory"
  sha256sum --check --strict <<'EOF'
4dff98e1d3226bcbee705b31f9547cc6f43c4a4c3fa5579d9afc7e919d1996a7  commands.txt
77d0997040ab2f1961d06d53e20190a60bf85a493647a9bc2bfaf0f685d95853  environment.txt
7a90d41f0924b5a3f6923974dca67ed0775ce6ff1f7bdc172e3fa0e1c533b157  images.tsv
56aac69def9c9ae2934ce94dd6c0d885f296094eaf14c8637ae9ff9abaf8fd69  manifest-lock-sha256.txt
d762973cc16b41e94a3e33763fe129c0a3c3f5ad83d47e7bd29bf02b9f4262f1  raw.log
012e280f86df07395fac20f89d1aacd1f25afebaa2ca4d43e07d0d3570c2e185  sizes.tsv
8f6c0876ea3abdac234880553de1a36087ed800453d999de2ba6a8d6898556ff  timings.tsv
EOF
)
grep -Fx "npm_ref=1594128bc84290df3699390643c729ef9d5d6d30" \
  "$artifact_directory/environment.txt"
grep -Fx "pnpm_ref=$accepted_ref" "$artifact_directory/environment.txt"
grep -Fx "node=v24.19.0" "$artifact_directory/environment.txt"
grep -Fx "npm=11.17.0" "$artifact_directory/environment.txt"
grep -Fx "pnpm=11.25.0" "$artifact_directory/environment.txt"
test "$(node --version)" = v24.19.0
test "$(npm --version)" = 11.17.0
test "$(pnpm --version)" = 11.25.0

expected_metadata="$({
  printf '%s\n' \
    "artifact_id=$accepted_artifact_id" \
    "artifact_name=package-manager-benchmark-33799673333-1" \
    "artifact_digest=$accepted_artifact_digest" \
    "run_id=$accepted_run" \
    "run_attempt=1" \
    "run_status=completed" \
    "run_conclusion=success" \
    "run_head_sha=$accepted_ref" \
    "repository_id=1343515292" \
    "repository=Guillaume-Lombardo/simple-md-to-docx-converter" \
    "head_repository_id=1343515292" \
    "head_repository=Guillaume-Lombardo/simple-md-to-docx-converter"
})"
readonly expected_metadata
if [[ "$(cat "$metadata_receipt")" != "$expected_metadata" ]]; then
  echo "Accepted T67 benchmark metadata receipt does not match." >&2
  exit 1
fi

cat > "$artifact_directory/reuse-attestation.txt" <<EOF
$(cat "$metadata_receipt")
accepted_head=$accepted_ref
candidate_head=$candidate_commit
performance_surface_digest=$surface_digest
performance_surfaces=.containerignore,package.json,pnpm-lock.yaml,pnpm-workspace.yaml,scripts/javascript/bootstrap-pnpm.sh,web/**
EOF
