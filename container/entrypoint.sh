#!/usr/bin/env bash
set -euo pipefail

md-converter-preflight

umask 0077
export PATH="/opt/md-converter/venv/bin:/usr/local/bin:/usr/bin"
for directory in \
  "$HOME" "$TMPDIR" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" \
  "$XDG_DATA_HOME" "$XDG_RUNTIME_DIR"; do
  mkdir -p -- "$directory"
  chmod 0700 -- "$directory"
done

exec /opt/md-converter/venv/bin/markweave "$@"
