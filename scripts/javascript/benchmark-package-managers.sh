#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 || "$1" == -* || "$2" == -* || "$3" == -* ]]; then
  echo "Usage: benchmark-package-managers.sh NPM_REF PNPM_REF OUTPUT_DIRECTORY" >&2
  exit 2
fi

readonly npm_ref="$1"
readonly pnpm_ref="$2"
readonly output_directory="$3"
repository="$(git rev-parse --show-toplevel)"
readonly repository
readonly npm_lock_sha256="7fc4db9135c474c8fe4f48dc60028a10df9904fb4d918f728f6fe3f19fca1061"
readonly web_lock_sha256="3dbff3f758ee4367dc5e7f70889d269798a4c87092c38dc418a200ae124285b1"
readonly pnpm_lock_sha256="4e74f5211aed6442062fc63be13eeb7e1af6c8248e7da8da6dd8c34174c02288"
workspace="$(mktemp -d /tmp/markweave-package-benchmark.XXXXXX)"
readonly workspace
readonly npm_tree="$workspace/npm"
readonly pnpm_tree="$workspace/pnpm"
readonly npm_cache="$workspace/npm-cache"
readonly pnpm_store="$workspace/pnpm-store"
readonly npm_image="localhost/markweave-benchmark-npm:temporary"
readonly pnpm_image="localhost/markweave-benchmark-pnpm:temporary"

cleanup() {
  podman image rm --force "$npm_image" "$pnpm_image" >/dev/null 2>&1 || true
  [[ "$workspace" == /tmp/markweave-package-benchmark.* ]]
  rm -rf -- "$workspace"
}
trap cleanup EXIT

mkdir -p "$output_directory" "$npm_tree" "$pnpm_tree"
git -C "$repository" archive "$npm_ref" | tar -x -C "$npm_tree"
git -C "$repository" archive "$pnpm_ref" | tar -x -C "$pnpm_tree"
echo "$npm_lock_sha256  $npm_tree/package-lock.json" | sha256sum --check --strict
echo "$web_lock_sha256  $npm_tree/web/package-lock.json" | sha256sum --check --strict
echo "$pnpm_lock_sha256  $pnpm_tree/pnpm-lock.yaml" | sha256sum --check --strict
test "$(node --version)" = v24.19.0
test "$(npm --version)" = 11.17.0
test "$(pnpm --version)" = 11.25.0

readonly timings="$output_directory/timings.tsv"
readonly sizes="$output_directory/sizes.tsv"
readonly raw_log="$output_directory/raw.log"
printf 'manager\tphase\tsample\tseconds\n' > "$timings"
printf 'manager\tsample\tnode_modules_bytes\tcache_bytes\tcache_archive_bytes\n' > "$sizes"
{
  printf 'runner_label=ubuntu-24.04\n'
  printf 'runner_image=%s\n' "${ImageOS:-unknown}-${ImageVersion:-unknown}"
  printf 'runner_arch=%s\n' "${RUNNER_ARCH:-unknown}"
  printf 'node=%s\nnpm=%s\npnpm=%s\n' \
    "$(node --version)" "$(npm --version)" "$(pnpm --version)"
  printf 'npm_ref=%s\npnpm_ref=%s\nsamples=3\n' \
    "$(git -C "$repository" rev-parse "$npm_ref^{commit}")" \
    "$(git -C "$repository" rev-parse "$pnpm_ref^{commit}")"
} > "$output_directory/environment.txt"
printf '%s\n' \
  'npm cold/warm: npm_config_cache=<cache> npm ci --ignore-scripts --prefix <root> && npm_config_cache=<cache> npm ci --ignore-scripts --prefix <root>/web' \
  'pnpm cold/warm: pnpm --dir <root> install --frozen-lockfile --ignore-scripts --store-dir <store>' \
  'npm build: npm run --prefix <root>/web build' \
  'pnpm build: pnpm --dir <root> --filter @markweave/web run build' \
  'npm image: podman build --format oci --file <npm-root>/web/Containerfile <npm-root>/web' \
  'pnpm image: podman build --format oci --file <pnpm-root>/web/Containerfile <pnpm-root>' \
  'cache archive: tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -cf - <cache> | gzip --no-name | wc --bytes' \
  > "$output_directory/commands.txt"

run_timed() {
  local manager="$1" phase="$2" sample="$3"
  shift 3
  local started_ns finished_ns seconds
  {
    printf 'manager=%s phase=%s sample=%s command=' "$manager" "$phase" "$sample"
    printf '%q ' "$@"
    printf '\n'
  } >> "$raw_log"
  started_ns="$(date +%s%N)"
  "$@" >> "$raw_log" 2>&1
  finished_ns="$(date +%s%N)"
  seconds="$(awk -v start="$started_ns" -v finish="$finished_ns" \
    'BEGIN {printf "%.3f", (finish - start) / 1000000000}')"
  printf '%s\t%s\t%s\t%s\n' "$manager" "$phase" "$sample" "$seconds" >> "$timings"
}

npm_install_graph() {
  local tree="$1" cache="$2"
  printf 'executing: npm_config_cache=%q npm ci --ignore-scripts --prefix %q\n' \
    "$cache" "$tree" >> "$raw_log"
  npm_config_cache="$cache" npm ci --ignore-scripts --prefix "$tree"
  printf 'executing: npm_config_cache=%q npm ci --ignore-scripts --prefix %q\n' \
    "$cache" "$tree/web" >> "$raw_log"
  npm_config_cache="$cache" npm ci --ignore-scripts --prefix "$tree/web"
}

tree_bytes() {
  du --bytes --summarize "$@" 2>/dev/null | awk '{total += $1} END {print total + 0}'
}

archive_bytes() {
  local directory="$1"
  tar --sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner \
    -C "$(dirname "$directory")" -cf - "$(basename "$directory")" \
    | gzip --no-name | wc --bytes
}

for sample in 1 2 3; do
  rm -rf -- "$npm_tree/node_modules" "$npm_tree/web/node_modules" \
    "$npm_tree/web/.next" "$npm_cache"
  mkdir -p "$npm_cache"
  run_timed npm cold-install "$sample" npm_install_graph "$npm_tree" "$npm_cache"
  rm -rf -- "$npm_tree/node_modules" "$npm_tree/web/node_modules"
  run_timed npm warm-install "$sample" npm_install_graph "$npm_tree" "$npm_cache"
  rm -rf -- "$npm_tree/web/.next"
  run_timed npm frontend-build "$sample" npm run --prefix "$npm_tree/web" build
  printf 'npm\t%s\t%s\t%s\t%s\n' "$sample" \
    "$(tree_bytes "$npm_tree/node_modules" "$npm_tree/web/node_modules")" \
    "$(tree_bytes "$npm_cache")" "$(archive_bytes "$npm_cache")" >> "$sizes"

  rm -rf -- "$pnpm_tree/node_modules" "$pnpm_tree/web/node_modules" \
    "$pnpm_tree/web/.next" "$pnpm_store"
  mkdir -p "$pnpm_store"
  run_timed pnpm cold-install "$sample" pnpm --dir "$pnpm_tree" install \
    --frozen-lockfile --ignore-scripts --store-dir "$pnpm_store"
  rm -rf -- "$pnpm_tree/node_modules" "$pnpm_tree/web/node_modules"
  run_timed pnpm warm-install "$sample" pnpm --dir "$pnpm_tree" install \
    --frozen-lockfile --ignore-scripts --store-dir "$pnpm_store"
  rm -rf -- "$pnpm_tree/web/.next"
  run_timed pnpm frontend-build "$sample" pnpm --dir "$pnpm_tree" \
    --filter @markweave/web run build
  printf 'pnpm\t%s\t%s\t%s\t%s\n' "$sample" \
    "$(tree_bytes "$pnpm_tree/node_modules" "$pnpm_tree/web/node_modules")" \
    "$(tree_bytes "$pnpm_store")" "$(archive_bytes "$pnpm_store")" >> "$sizes"
done

rm -rf -- "$npm_tree/node_modules" "$npm_tree/web/node_modules" "$npm_tree/web/.next" \
  "$pnpm_tree/node_modules" "$pnpm_tree/web/node_modules" "$pnpm_tree/web/.next"
run_timed npm final-image 1 podman build --format oci --tag "$npm_image" \
  --file "$npm_tree/web/Containerfile" "$npm_tree/web"
run_timed pnpm final-image 1 podman build --format oci --tag "$pnpm_image" \
  --file "$pnpm_tree/web/Containerfile" "$pnpm_tree"
{
  printf 'manager\timage_bytes\n'
  printf 'npm\t%s\n' "$(podman image inspect "$npm_image" --format '{{.Size}}')"
  printf 'pnpm\t%s\n' "$(podman image inspect "$pnpm_image" --format '{{.Size}}')"
} > "$output_directory/images.tsv"

sha256sum "$npm_tree/package.json" "$npm_tree/package-lock.json" \
  "$npm_tree/web/package.json" "$npm_tree/web/package-lock.json" \
  "$pnpm_tree/package.json" "$pnpm_tree/pnpm-lock.yaml" \
  "$pnpm_tree/web/package.json" > "$output_directory/manifest-lock-sha256.txt"
