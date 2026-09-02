#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 || $# -gt 4 ]]; then
  echo "Usage: publish-release-pair.sh ARTIFACTS VERSION SOURCE_SHA [FRONTEND_LOCK]" >&2
  exit 2
fi
readonly artifacts="$1" version="$2" source_sha="$3"
readonly frontend_lock="${4:-web/package-lock.json}"
[[ "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]
[[ "$source_sha" =~ ^[0-9a-f]{40}$ && "$source_sha" != 0000000000000000000000000000000000000000 ]]
[[ -n "${GHCR_TOKEN:-}" && -n "${GITHUB_ACTOR:-}" && -n "${RUNNER_TEMP:-}" ]]
[[ -d "$artifacts/backend" && -d "$artifacts/frontend" ]]

staging_root="$(mktemp -d "$RUNNER_TEMP/registry-pair.XXXXXX")"
readonly staging_root
cleanup() {
  rm -rf -- "$staging_root"
}
trap cleanup EXIT

declare -A repositories=(
  [backend]="ghcr.io/guillaume-lombardo/md-converter"
  [frontend]="ghcr.io/guillaume-lombardo/md-converter-web"
)
declare -A registry_paths=(
  [backend]="guillaume-lombardo/md-converter"
  [frontend]="guillaume-lombardo/md-converter-web"
)
declare -A intended_digests=()
declare -A registry_tokens=()

inspect_remote_tag() {
  local role="$1" tag="$2"
  local headers="$staging_root/$role-$tag.headers"
  local status digest
  status="$(curl --silent --show-error --output /dev/null \
    --dump-header "$headers" --write-out '%{http_code}' \
    --header "Authorization: Bearer ${registry_tokens[$role]}" \
    --header 'Accept: application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json' \
    "https://ghcr.io/v2/${registry_paths[$role]}/manifests/$tag")" || return 5
  [[ "$status" != 404 ]] || return 4
  [[ "$status" = 200 ]] || return 5
  digest="$(awk -F ': ' 'tolower($1) == "docker-content-digest" {gsub("\\r", "", $2); print $2}' "$headers")"
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || return 5
  printf '%s\n' "$digest"
}

copy_staged_tag() {
  local role="$1" tag="$2" copy_status copied_digest inspect_status
  if skopeo copy --preserve-digests --retry-times 3 \
    "dir:$staging_root/$role" "docker://${repositories[$role]}:$tag"; then
    copy_status=0
  else
    copy_status="$?"
  fi
  if copied_digest="$(inspect_remote_tag "$role" "$tag")"; then
    if [[ "$copied_digest" = "${intended_digests[$role]}" ]]; then
      if [[ "$copy_status" != 0 ]]; then
        echo "Skopeo returned $copy_status after GHCR stored the expected $role manifest for $tag." >&2
      fi
      return 0
    fi
    echo "GHCR ${repositories[$role]}:$tag resolves to unexpected digest $copied_digest." >&2
    return 1
  fi
  inspect_status="$?"
  echo "GHCR $role tag $tag could not be verified after copy status $copy_status (inspection $inspect_status)." >&2
  return 1
}

# Serialize both images and reject every observable conflict before publishing either one.
for role in backend frontend; do
  mkdir "$staging_root/$role"
  bundle_manifest_sha256="$(sha256sum "$artifacts/$role/release-bundle.sha256" | cut -d' ' -f1)"
  uv run python -m scripts.container.verify_supply_chain verify \
    --artifacts "$artifacts/$role" \
    --expected-manifest-sha256 "$bundle_manifest_sha256"
  intended_digests[$role]="$(jq --exit-status --raw-output \
    '.image.oci_manifest_digest' "$artifacts/$role/image-metadata.json")"
  [[ "${intended_digests[$role]}" =~ ^sha256:[0-9a-f]{64}$ ]]
  skopeo copy --preserve-digests \
    "oci-archive:$artifacts/$role/image.oci.tar" "dir:$staging_root/$role"
  test "sha256:$(sha256sum "$staging_root/$role/manifest.json" | cut -d' ' -f1)" = \
    "${intended_digests[$role]}"
  registry_tokens[$role]="$(curl --fail --silent --show-error \
    --user "$GITHUB_ACTOR:$GHCR_TOKEN" \
    "https://ghcr.io/token?service=ghcr.io&scope=repository:${registry_paths[$role]}:pull,push" \
    | jq --exit-status --raw-output '.token // .access_token')"
  for tag in "source-$source_sha" "$version"; do
    if remote_digest="$(inspect_remote_tag "$role" "$tag")"; then
      test "$remote_digest" = "${intended_digests[$role]}"
    else
      test "$?" = 4
    fi
  done
done

for role in backend frontend; do
  for tag in "source-$source_sha" "$version"; do
    if remote_digest="$(inspect_remote_tag "$role" "$tag")"; then
      test "$remote_digest" = "${intended_digests[$role]}"
    else
      test "$?" = 4
      copy_staged_tag "$role" "$tag"
      test "$(inspect_remote_tag "$role" "$tag")" = "${intended_digests[$role]}"
    fi
  done
  archive_digest="$(jq --exit-status --raw-output \
    '.image.oci_manifest_digest' "$artifacts/$role/image-metadata.json")"
  [[ "$archive_digest" =~ ^sha256:[0-9a-f]{64}$ ]]
  jq --null-input \
    --arg archive_digest "$archive_digest" \
    --arg registry_digest "${intended_digests[$role]}" \
    --arg source_sha "$source_sha" \
    --arg version "$version" \
    '{oci_archive_manifest_digest: $archive_digest, registry_manifest_digest: $registry_digest, source_sha: $source_sha, version: $version}' \
    > "$artifacts/$role/registry-publication.json"
done

uv run python -m scripts.container.release_pair create \
  --artifacts "$artifacts" --version "$version" --source-sha "$source_sha" \
  --frontend-lock "$frontend_lock"
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  printf 'backend-digest=%s\nfrontend-digest=%s\n' \
    "${intended_digests[backend]}" "${intended_digests[frontend]}" >> "$GITHUB_OUTPUT"
fi
