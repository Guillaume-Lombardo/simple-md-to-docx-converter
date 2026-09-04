#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 || "$1" == -* || "$2" == -* ]]; then
  echo "Usage: rehearse-npm-rollback.sh T67_CANDIDATE_GIT_REF NPM_BASELINE_GIT_REF" >&2
  exit 2
fi

readonly candidate_ref="$1"
readonly baseline_ref="$2"
readonly root_lock_sha256="7fc4db9135c474c8fe4f48dc60028a10df9904fb4d918f728f6fe3f19fca1061"
readonly web_lock_sha256="3dbff3f758ee4367dc5e7f70889d269798a4c87092c38dc418a200ae124285b1"
readonly reviewed_integration_merge="276faa38469efc6ee6b72846ad7cf6004d05e947"
repository="$(git rev-parse --show-toplevel)"
readonly repository
candidate="$(git -C "$repository" rev-parse --verify "$candidate_ref^{commit}")"
baseline="$(git -C "$repository" rev-parse --verify "$baseline_ref^{commit}")"
readonly candidate baseline

fail() {
  echo "npm rollback rehearsal failed: $*" >&2
  exit 1
}

baseline_root_lock_sha256="$(
  git -C "$repository" show "$baseline:package-lock.json" 2>/dev/null | sha256sum | cut -d' ' -f1
)" || fail "baseline lacks the audited root npm lock"
baseline_web_lock_sha256="$(
  git -C "$repository" show "$baseline:web/package-lock.json" 2>/dev/null | sha256sum | cut -d' ' -f1
)" || fail "baseline lacks the audited frontend npm lock"
[[ "$baseline_root_lock_sha256" == "$root_lock_sha256" \
  && "$baseline_web_lock_sha256" == "$web_lock_sha256" ]] \
  || fail "baseline is not the audited npm state"

if ! selected_commits="$(
  bash "$repository/scripts/javascript/select-t67-rollback-commits.sh" \
    "$candidate" "$baseline" "$reviewed_integration_merge" 2>&1
)"; then
  fail "$selected_commits"
fi
mapfile -t t67_commits <<< "$selected_commits"

allowed_path() {
  case "$1" in
    .containerignore|.github/dependabot.yml|.github/workflows/ci.yml|\
    .github/workflows/container-release.yml|.gitignore|CONTRIBUTING.md|README.md|\
    docs/administration-ui.md|docs/container-deployment.md|docs/conversion-ui.md|\
    docs/local-development.md|docs/nextjs-migration-architecture.md|\
    docs/package-management.md|docs/product-specification.md|package.json|package-lock.json|\
    pnpm-lock.yaml|pnpm-workspace.yaml|scripts/ci/select_domains.py|\
    scripts/ci/validate_ci.py|scripts/container/publish-release-pair.sh|\
    scripts/container/run-ci.sh|scripts/e2e/run.sh|scripts/javascript/bootstrap-pnpm.sh|\
    scripts/javascript/benchmark-package-managers.sh|\
    scripts/javascript/run-bounded-benchmark-command.sh|\
    scripts/javascript/run_bounded_benchmark_command.py|\
    scripts/javascript/reuse-package-benchmark.sh|\
    scripts/javascript/rehearse-npm-rollback.sh|\
    scripts/javascript/select-t67-rollback-commits.sh|\
    scripts/javascript/validate-workspace.mjs|\
    tests/integration/test_benchmark_timeout.py|tests/test_ci_selection.py|\
    tests/test_ci_validation.py|tests/test_documentation.py|\
    tests/test_pnpm_workspace.py|tests/test_t22_maintenance.py|\
    tickets/T67-migrate-javascript-tooling-pnpm-workspace.md|web/Containerfile|\
    web/README.md|web/next.config.ts|web/package.json|web/package-lock.json|\
    web/scripts/generate-openapi.mjs|web/scripts/run-rootless-smoke.sh|\
    web/tests/structure.test.mjs)
      return 0
      ;;
  esac
  return 1
}

t67_paths=()
while IFS= read -r path; do
  [[ -n "$path" ]] || continue
  allowed_path "$path" || fail "T67 commit changes an unowned path: $path"
  t67_paths+=("$path")
done < <(
  for commit in "${t67_commits[@]}"; do
    git -C "$repository" diff-tree --no-commit-id --name-only -r "$commit"
  done | sort -u
)

for required_path in pnpm-lock.yaml pnpm-workspace.yaml \
  scripts/javascript/bootstrap-pnpm.sh \
  scripts/javascript/select-t67-rollback-commits.sh \
  scripts/javascript/validate-workspace.mjs; do
  git -C "$repository" cat-file -e "$candidate:$required_path" \
    || fail "candidate lacks $required_path"
done
for retired_lock in package-lock.json web/package-lock.json; do
  if git -C "$repository" cat-file -e "$candidate:$retired_lock" 2>/dev/null; then
    fail "candidate still contains $retired_lock"
  fi
done
candidate_manifest="$(git -C "$repository" show "$candidate:package.json")"
[[ "$candidate_manifest" == *'"packageManager": "pnpm@11.25.0+sha224.c69bc375107d8eef668fbe1ebab8b3a34253dc594dff6a0a36d8a16c"'* ]] \
  || fail "candidate is not the reviewed pnpm 11.25.0 graph"

rehearsal="$(mktemp -d /tmp/markweave-npm-rollback.XXXXXX)"
readonly rehearsal
cleanup() {
  [[ "$rehearsal" == /tmp/markweave-npm-rollback.* ]]
  rm -rf -- "$rehearsal"
}
trap cleanup EXIT
git -C "$repository" archive "$candidate" | tar -x -C "$rehearsal"

for (( index=${#t67_commits[@]} - 1; index >= 0; index-- )); do
  git -C "$repository" show --format= --binary "${t67_commits[index]}" \
    | git -C "$rehearsal" apply --reverse --binary --whitespace=nowarn
done

# Every T67-owned path must be byte-for-byte identical to the npm baseline after
# reversal. The product specification is the sole exception because a merged T69
# change is intentionally retained while the T67 patch is removed.
for path in "${t67_paths[@]}"; do
  [[ "$path" == docs/product-specification.md ]] && continue
  if git -C "$repository" cat-file -e "$baseline:$path" 2>/dev/null; then
    [[ -f "$rehearsal/$path" ]] || fail "rollback did not restore $path"
    cmp --silent "$rehearsal/$path" <(git -C "$repository" show "$baseline:$path") \
      || fail "rollback did not restore exact baseline bytes for $path"
  elif [[ -e "$rehearsal/$path" ]]; then
    fail "rollback left candidate-only path $path"
  fi
done

[[ -f "$rehearsal/package-lock.json" && -f "$rehearsal/web/package-lock.json" ]] \
  || fail "both npm application locks were not restored"
[[ ! -e "$rehearsal/pnpm-lock.yaml" && ! -e "$rehearsal/pnpm-workspace.yaml" ]] \
  || fail "pnpm workspace state remains"
[[ ! -e "$rehearsal/.npmrc" && ! -e "$rehearsal/docs/package-management.md" ]] \
  || fail "package-manager selection or documentation state remains"
[[ ! -e "$rehearsal/scripts/javascript/bootstrap-pnpm.sh" \
  && ! -e "$rehearsal/scripts/javascript/select-t67-rollback-commits.sh" \
  && ! -e "$rehearsal/scripts/javascript/validate-workspace.mjs" \
  && ! -e "$rehearsal/scripts/javascript/rehearse-npm-rollback.sh" ]] \
  || fail "pnpm/Corepack scripts remain"
echo "$root_lock_sha256  $rehearsal/package-lock.json" | sha256sum --check --strict
echo "$web_lock_sha256  $rehearsal/web/package-lock.json" | sha256sum --check --strict

grep -Fq '"packageManager": "npm@11.17.0"' "$rehearsal/web/package.json" \
  || fail "npm package-manager metadata was not restored"
grep -Fq '"npm": "11.17.0"' "$rehearsal/web/package.json" \
  || fail "npm engine metadata was not restored"
grep -Fq 'cache: npm' "$rehearsal/.github/workflows/ci.yml" \
  || fail "npm CI cache was not restored"
grep -Fq 'package-lock.json' "$rehearsal/.github/workflows/ci.yml" \
  || fail "npm CI lock cache binding was not restored"
grep -Fq 'COPY package.json package-lock.json ./' "$rehearsal/web/Containerfile" \
  || fail "npm frontend build manifest copy was not restored"
grep -Fq 'npm ci --ignore-scripts' "$rehearsal/web/Containerfile" \
  || fail "npm frontend install was not restored"
grep -Fq 'npm run build && npm prune --omit=dev --ignore-scripts' \
  "$rehearsal/web/Containerfile" || fail "npm production prune was not restored"
grep -Fq -- '--file web/Containerfile web' "$rehearsal/scripts/container/run-ci.sh" \
  || fail "isolated web build context was not restored"
grep -Fq 'PUPPETEER_SKIP_DOWNLOAD=true npm ci --ignore-scripts' \
  "$rehearsal/scripts/e2e/run.sh" || fail "npm E2E setup was not restored"
grep -Fq 'npm ci --prefix spikes/toolchain --omit=dev --ignore-scripts' \
  "$rehearsal/.github/workflows/ci.yml" || fail "isolated Mermaid npm install changed"

if grep -RIE '(pnpm|corepack)' \
  "$rehearsal/.github" "$rehearsal/CONTRIBUTING.md" "$rehearsal/README.md" \
  "$rehearsal/docs/administration-ui.md" "$rehearsal/docs/container-deployment.md" \
  "$rehearsal/docs/conversion-ui.md" "$rehearsal/docs/local-development.md" \
  "$rehearsal/docs/nextjs-migration-architecture.md" "$rehearsal/package.json" \
  "$rehearsal/scripts" "$rehearsal/web" >/dev/null; then
  fail "pnpm/Corepack command or configuration remains in the rollback state"
fi

test "$(node --version)" = v24.19.0 || fail "Node 24.19.0 is required"
test "$(npm --version)" = 11.17.0 || fail "npm 11.17.0 is required"
(
  cd "$rehearsal"
  sha256sum package.json package-lock.json web/package.json web/package-lock.json \
    > .npm-baseline.sha256
  npm ci --ignore-scripts
  npm run test:web
  npm ci --prefix web --ignore-scripts
  npm run --prefix web bindings:check
  npm run --prefix web build
  npm run --prefix web test:production
  sha256sum --check --strict .npm-baseline.sha256
)

echo "Rehearsed exact T67 candidate $candidate to npm baseline $baseline"
