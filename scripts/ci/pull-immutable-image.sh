#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: scripts/ci/pull-immutable-image.sh IMAGE EXPECTED_DIGEST" >&2
  exit 2
fi

readonly image="$1"
readonly expected_digest="$2"
readonly attempts=3
readonly retry_delay_seconds=2

if [[ ! "$image" =~ @sha256:[0-9a-f]{64}$ ]] || \
  [[ ! "$expected_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || \
  [[ "${image##*@}" != "$expected_digest" ]]; then
  echo "Image acquisition requires an immutable image reference matching EXPECTED_DIGEST." >&2
  exit 2
fi
temporary_output="$(mktemp)"
cleanup() {
  rm -f -- "$temporary_output"
}
trap cleanup EXIT

is_transient_transport_failure() {
  grep --quiet --extended-regexp \
    'unexpected EOF|connection reset by peer|TLS handshake timeout|i/o timeout|connection timed out|temporary failure in name resolution|received unexpected HTTP status: 5[0-9]{2}' \
    "$temporary_output"
}

is_permanent_or_integrity_failure() {
  grep --quiet --ignore-case --extended-regexp \
    'digest verification failed|digest mismatch|checksum mismatch|signature verification failed|invalid signature|manifest unknown|not found|unauthorized|denied' \
    "$temporary_output"
}

for attempt in $(seq 1 "$attempts"); do
  if podman pull --quiet "$image" >"$temporary_output" 2>&1; then
    if actual_digest="$(podman image inspect "$image" --format '{{.Digest}}')" && \
      [[ "$actual_digest" == "$expected_digest" ]]; then
      printf 'Acquired immutable image %s with verified digest %s.\n' \
        "$image" "$expected_digest"
      exit 0
    fi
    echo "Immutable image digest verification failed for $image; expected $expected_digest." >&2
    exit 1
  fi

  if ! is_permanent_or_integrity_failure && is_transient_transport_failure && \
    ((attempt < attempts)); then
    printf 'Image acquisition attempt %s/%s failed with a transient transport error; retrying in %ss.\n' \
      "$attempt" "$attempts" "$retry_delay_seconds" >&2
    sleep "$retry_delay_seconds"
    continue
  fi

  if ! is_permanent_or_integrity_failure && is_transient_transport_failure; then
    printf 'Image acquisition exhausted %s transient transport attempts for %s.\n' \
      "$attempts" "$image" >&2
  else
    printf 'Image acquisition failed without retry for %s.\n' "$image" >&2
  fi
  exit 1
done
