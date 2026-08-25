#!/usr/bin/env bash
set -euo pipefail

readonly image="${1:-localhost/md-converter:t20}"
readonly output_directory="${2:-artifacts/container}"
readonly purpose="${3:-ci}"
readonly syft_version=1.50.0
readonly syft_sha256=bf7b29ff57f06da30918266a0e1c2885a8f99784798d1bdb1628886aa015d788
readonly grype_version=0.116.1
readonly grype_sha256=0122df7b655981abe547ad3d2190d65551dac6a2bfc80b4dc2a989b5d0587458
if [[ "$purpose" != "ci" && "$purpose" != "release" ]]; then
  echo "Usage: scripts/container/supply-chain.sh IMAGE OUTPUT_DIRECTORY {ci|release}" >&2
  exit 2
fi
if [[ -e "$output_directory" || -L "$output_directory" ]]; then
  echo "Output directory must not already exist: $output_directory" >&2
  exit 2
fi
umask 077
output_parent="$(dirname -- "$output_directory")"
readonly output_parent
mkdir -p -- "$output_parent"
if [[ -L "$output_parent" ]]; then
  echo "Output parent must not be a symlink: $output_parent" >&2
  exit 2
fi
tool_directory="$(mktemp -d)"
staging_directory="$(mktemp -d "$output_parent/.supply-chain.XXXXXX")"
cleanup() {
  rm -rf -- "$tool_directory"
  if [[ -n "${staging_directory:-}" && -d "$staging_directory" ]]; then
    rm -rf -- "$staging_directory"
  fi
}
trap cleanup EXIT

install_tool() {
  local name="$1" version="$2" expected="$3"
  local archive="$tool_directory/$name.tar.gz"
  curl --fail --location --proto '=https' --tlsv1.2 \
    --connect-timeout 20 --max-time 300 --retry 3 --retry-all-errors \
    --output "$archive" \
    "https://github.com/anchore/$name/releases/download/v$version/${name}_${version}_linux_amd64.tar.gz"
  echo "$expected  $archive" | sha256sum --check --strict
  tar --extract --gzip --file "$archive" --directory "$tool_directory" "$name"
}

install_tool syft "$syft_version" "$syft_sha256"
install_tool grype "$grype_version" "$grype_sha256"
image_id="$(podman image inspect "$image" --format '{{.Id}}')"
readonly image_id
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]
readonly image_archive="$staging_directory/image.oci.tar"
podman save --format oci-archive --output "$image_archive" "$image_id"
"$tool_directory/syft" "oci-archive:$image_archive" \
  --output "cyclonedx-json=$staging_directory/sbom.cdx.json" \
  --output "spdx-json=$staging_directory/sbom.spdx.json"
GRYPE_DB_AUTO_UPDATE=true "$tool_directory/grype" \
  "sbom:$staging_directory/sbom.cdx.json" \
  --output json > "$staging_directory/vulnerabilities.json"
summary_arguments=(
  --image "$image"
  --artifacts "$staging_directory"
  --expected-image-id "$image_id"
)
if [[ "$purpose" == "release" ]]; then
  summary_arguments+=(--release)
fi
uv run python -m scripts.container.summarize_supply_chain "${summary_arguments[@]}"
manifest_digest="$(
  uv run python -m scripts.container.verify_supply_chain create \
    --artifacts "$staging_directory"
)"
readonly manifest_digest
uv run python -m scripts.container.verify_supply_chain \
  verify \
  --artifacts "$staging_directory" \
  --expected-manifest-sha256 "$manifest_digest"
mv --no-target-directory -- "$staging_directory" "$output_directory"
staging_directory=""
printf 'release_bundle_manifest_sha256=%s\n' "$manifest_digest"
