#!/usr/bin/env bash
set -euo pipefail

readonly corepack_version=0.36.0
readonly corepack_integrity='sha512-SiiJsBhZqdBiPHTEl6OT3sASrRrKIcYTQMsVGXx6EE/gM8WFMwYjeIX8Tt8RiU4Iv2J6LbT8KpGfCOsBpRWB/w=='
readonly corepack_url="https://registry.npmjs.org/corepack/-/corepack-${corepack_version}.tgz"
readonly pnpm_spec='pnpm@11.25.0+sha224.c69bc375107d8eef668fbe1ebab8b3a34253dc594dff6a0a36d8a16c'

if [[ $# -ne 1 || "$1" != /* ]]; then
  echo "Usage: bootstrap-pnpm.sh ABSOLUTE_INSTALL_DIRECTORY" >&2
  exit 2
fi
readonly install_directory="$1"
readonly archive="$install_directory/corepack-${corepack_version}.tgz"
readonly corepack_home="$install_directory/corepack-home"

mkdir -p -- "$install_directory" "$corepack_home"
curl --fail --location --proto '=https' --tlsv1.2 \
  --connect-timeout 20 --max-time 180 --retry 3 --retry-all-errors \
  --output "$archive" "$corepack_url"
actual_integrity="sha512-$(openssl dgst -sha512 -binary "$archive" | openssl base64 -A)"
test "$actual_integrity" = "$corepack_integrity"
npm install --global --prefix "$install_directory" --ignore-scripts --no-audit --no-fund \
  "$archive"
test "$("$install_directory/bin/corepack" --version)" = "$corepack_version"
COREPACK_HOME="$corepack_home" COREPACK_ENABLE_NETWORK=1 COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
  "$install_directory/bin/corepack" prepare "$pnpm_spec" --activate
test "$(COREPACK_HOME="$corepack_home" COREPACK_ENABLE_NETWORK=0 \
  "$install_directory/bin/pnpm" --version)" = 11.25.0
rm -f -- "$archive"
