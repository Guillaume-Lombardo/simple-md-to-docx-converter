#!/usr/bin/env bash
set -euo pipefail
umask 0077

readonly variant="${1:-}"
readonly version=26.2.5
readonly cache_directory="${MARKWEAVE_TOOLCHAIN_CACHE_DIRECTORY:-${XDG_CACHE_HOME:-$HOME/.cache}/markweave/toolchain}"

case "$variant" in
  rpm)
    readonly filename="LibreOffice_26.2.5_Linux_x86-64_rpm.tar.gz"
    readonly sha256=f62611c441ff1faa5cadb499abdbab119f5a9013eb6c0e32fc9aa65f6ff8b53d
    readonly url="https://download.documentfoundation.org/libreoffice/stable/$version/rpm/x86_64/$filename"
    ;;
  deb)
    readonly filename="LibreOffice_26.2.5_Linux_x86-64_deb.tar.gz"
    readonly sha256=2f03bfb2ac9f33ea7c77331b4b7a23300fb0ed7443566046bf8b5bc51c1bed1e
    readonly url="https://download.documentfoundation.org/libreoffice/stable/$version/deb/x86_64/$filename"
    ;;
  *)
    echo "LibreOffice archive variant must be rpm or deb." >&2
    exit 2
    ;;
esac

readonly archive="$cache_directory/$filename"
temporary=""

cleanup() {
  if [[ -n "$temporary" && -f "$temporary" ]]; then
    rm -f -- "$temporary"
  fi
}
trap cleanup EXIT

mkdir -p -- "$cache_directory"
chmod 0700 -- "$cache_directory"

if [[ ! -f "$archive" || -L "$archive" ]]; then
  [[ ! -e "$archive" && ! -L "$archive" ]] || {
    echo "Refusing an unsafe LibreOffice cache entry: $archive" >&2
    exit 1
  }
  temporary="$(mktemp "$cache_directory/.libreoffice.XXXXXX")"
  curl --fail --location --proto '=https' --tlsv1.2 \
    --connect-timeout 20 --max-time 600 --retry 5 --retry-all-errors \
    --output "$temporary" "$url"
  echo "$sha256  $temporary" | sha256sum --check --strict
  chmod 0600 -- "$temporary"
  mv --no-target-directory -- "$temporary" "$archive"
  temporary=""
fi

echo "$sha256  $archive" | sha256sum --check --strict
