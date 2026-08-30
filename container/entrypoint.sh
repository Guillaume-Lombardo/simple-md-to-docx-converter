#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
  api|embedded-worker|external-worker)
    mode="$1"
    shift
    ;;
  *)
    echo "Usage: md-converter-entrypoint {api|embedded-worker|external-worker}" >&2
    exit 64
    ;;
esac

md-converter-preflight

umask 0077
export PATH="/opt/md-converter/venv/bin:/usr/local/bin:/usr/bin"
for directory in \
  "$HOME" "$TMPDIR" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" \
  "$XDG_DATA_HOME" "$XDG_RUNTIME_DIR"; do
  mkdir -p -- "$directory"
  chmod 0700 -- "$directory"
done

if [[ "$mode" == api ]]; then
  exec uvicorn markweave:create_app \
    --factory \
    --host "${MARKWEAVE_HOST:-${MD_CONVERTER_HOST:-0.0.0.0}}" \
    --port "${MARKWEAVE_PORT:-${MD_CONVERTER_PORT:-8080}}" \
    --no-server-header \
    --no-proxy-headers \
    "$@"
fi

exec /opt/md-converter/venv/bin/python \
  -m markweave.runtime "$mode" "$@"
