#!/usr/bin/env bash
set -euo pipefail

readonly image="${1:-localhost/md-converter:t20}"
readonly output_directory="${2:-artifacts/container}"
readonly syft_version=1.50.0
readonly syft_sha256=bf7b29ff57f06da30918266a0e1c2885a8f99784798d1bdb1628886aa015d788
readonly grype_version=0.116.1
readonly grype_sha256=0122df7b655981abe547ad3d2190d65551dac6a2bfc80b4dc2a989b5d0587458
tool_directory="$(mktemp -d)"
trap 'rm -rf -- "$tool_directory"' EXIT

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

mkdir -p -- "$output_directory"
install_tool syft "$syft_version" "$syft_sha256"
install_tool grype "$grype_version" "$grype_sha256"
podman save --format oci-archive --output "$tool_directory/image.tar" "$image"
"$tool_directory/syft" "oci-archive:$tool_directory/image.tar" \
  --output "cyclonedx-json=$output_directory/sbom.cdx.json" \
  --output "spdx-json=$output_directory/sbom.spdx.json"
GRYPE_DB_AUTO_UPDATE=true "$tool_directory/grype" \
  "sbom:$output_directory/sbom.cdx.json" \
  --output json > "$output_directory/vulnerabilities.json"
uv run python -m scripts.container.summarize_supply_chain \
  --image "$image" \
  --artifacts "$output_directory"
