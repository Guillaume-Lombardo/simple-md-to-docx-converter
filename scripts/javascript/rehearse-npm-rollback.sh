#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || "$1" == -* ]]; then
  echo "Usage: rehearse-npm-rollback.sh NPM_BASELINE_GIT_REF" >&2
  exit 2
fi
readonly baseline_ref="$1"
repository="$(git rev-parse --show-toplevel)"
readonly repository
rehearsal="$(mktemp -d /tmp/markweave-npm-rollback.XXXXXX)"
readonly rehearsal
cleanup() {
  [[ "$rehearsal" == /tmp/markweave-npm-rollback.* ]]
  rm -rf -- "$rehearsal"
}
trap cleanup EXIT

git -C "$repository" cat-file -e "$baseline_ref^{commit}"
git -C "$repository" archive "$baseline_ref" | tar -x -C "$rehearsal"
[[ -f "$rehearsal/package-lock.json" && -f "$rehearsal/web/package-lock.json" ]]
[[ ! -e "$rehearsal/pnpm-lock.yaml" && ! -e "$rehearsal/pnpm-workspace.yaml" ]]
[[ ! -e "$rehearsal/.npmrc" ]]
test "$(node --version)" = v24.19.0
test "$(npm --version)" = 11.17.0
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
