#!/usr/bin/env bash
set -euo pipefail

readonly base_url="${1:?legacy base URL is required}"

response_headers="$(mktemp)"
response_body="$(mktemp)"
cleanup() {
  rm -f -- "$response_headers" "$response_body"
}
trap cleanup EXIT

assert_route() {
  local path="$1" expected_status="$2" expected_location="${3:-}"
  local status
  status="$(curl --silent --show-error --output "$response_body" \
    --dump-header "$response_headers" --write-out '%{http_code}' \
    "$base_url$path")"
  if [[ "$status" != "$expected_status" ]]; then
    echo "Legacy route $path returned $status; expected $expected_status." >&2
    exit 1
  fi
  if [[ -n "$expected_location" ]]; then
    if ! tr -d '\r' < "$response_headers" \
      | grep -Fiqx "location: $expected_location"; then
      echo "Legacy route $path did not return Location: $expected_location." >&2
      exit 1
    fi
    if [[ -s "$response_body" ]]; then
      echo "Legacy redirect route $path returned a response body." >&2
      exit 1
    fi
  fi
}

assert_route / 303 /convert
assert_route /login 200
grep -Fq '<form' "$response_body"
assert_route /convert 303 /login
assert_route /templates 303 /login
assert_route /static/conversion.css 200
assert_route /static/conversion.js 200
assert_route /static/administration.css 200
assert_route /static/administration.js 200
assert_route /api/v1/session 401
assert_route /health/ready 200

echo "Released legacy route manifest passed at $base_url."
