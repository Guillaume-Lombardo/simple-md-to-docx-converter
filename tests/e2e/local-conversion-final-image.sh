#!/usr/bin/env bash
# Final rootless image E2E for `markweave convert --local` (T106, decision D1).
# Covers the documented container invocation, approved-pin enforcement, Mermaid,
# missing engines, invalid input, global timeout, and SIGTERM cancellation.
set -euo pipefail

readonly image="${1:-localhost/md-converter:t20-ci}"
readonly runtime_uid="${T106_RUNTIME_UID:-54000}"
repository="$(pwd)"
readonly repository
readonly seccomp_profile="$repository/toolchain/document-engines/chrome-seccomp.json"
readonly env_file="$repository/examples/local-conversion.env"
readonly cancel_name="markweave-t106-local-cancel-$$"
readonly locked_name="markweave-t106-local-locked-$$"
lock_holder=""

work="$(mktemp -d "${TMPDIR:-/tmp}/markweave-t106-local.XXXXXX")"
readonly work
cleanup() {
  podman rm --force --ignore "$cancel_name" "$locked_name" >/dev/null 2>&1 || true
  if [[ -n "$lock_holder" ]]; then
    kill "$lock_holder" >/dev/null 2>&1 || true
    wait "$lock_holder" 2>/dev/null || true
  fi
  podman unshare rm -rf -- "$work"
}
trap cleanup EXIT

fail() {
  echo "Local conversion final-image E2E failed: $*" >&2
  exit 1
}

mkdir -- "$work/input"
printf '# Local final image\n\nReal **conversion** without a server.\n' \
  >"$work/input/document.md"
cat >"$work/input/diagram.md" <<'MARKDOWN'
# Diagram

```mermaid
flowchart LR
  A --> B
```
MARKDOWN
printf '<b>raw</b>\n' >"$work/input/invalid.md"
base64 --decode "$repository/examples/quickstart-template.docx.base64" \
  >"$work/input/template.docx"
cat >"$work/input/slow-pandoc" <<'ENGINE'
#!/bin/sh
if [ "$1" = --version ]; then echo "pandoc 3.10.2"; exit 0; fi
exec sleep 300
ENGINE
cat >"$work/input/older-pandoc" <<'ENGINE'
#!/bin/sh
if [ "$1" = --version ]; then echo "pandoc 3.9.0"; exit 0; fi
exec pandoc "$@"
ENGINE
chmod 0755 "$work/input" "$work/input/slow-pandoc" "$work/input/older-pandoc"
chmod 0644 "$work/input/"*.md "$work/input/template.docx"

# One private output directory per scenario, writable by the arbitrary runtime UID.
new_output() {
  local directory="$work/output-$1"
  mkdir -- "$directory"
  chmod 0770 -- "$directory"
  podman unshare chown "$runtime_uid:0" "$directory"
  printf '%s\n' "$directory"
}

# Make an output directory readable by the host user for inspection.
reclaim() {
  podman unshare chown -R 0:0 "$1"
}

local_run() {
  local output="$1"
  shift
  podman run --rm \
    --user "$runtime_uid:0" \
    --read-only \
    --cap-drop=all \
    --security-opt=no-new-privileges \
    --security-opt="seccomp=$seccomp_profile" \
    --network=none \
    --memory=1g \
    --cpus=2 \
    --pids-limit=256 \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777 \
    --tmpfs /work:rw,nosuid,nodev,size=256m,mode=0770 \
    --shm-size=256m \
    --env-file "$env_file" \
    --volume "$work/input:/work/input:ro,Z" \
    --volume "$output:/work/output:Z" \
    "$@"
}

expect_failure() {
  local name="$1" code="$2" output
  shift 2
  output="$(new_output "$name")"
  if local_run "$output" "$@" >"$work/$name.out" 2>"$work/$name.err"; then
    fail "$name unexpectedly succeeded"
  fi
  test ! -s "$work/$name.out" || fail "$name wrote to stdout"
  cat -- "$work/$name.err"
  uv run python - "$work/$name.err" "$code" <<'PY'
import json
import sys
from pathlib import Path

error = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["error"]
assert error["code"] == sys.argv[2], error
PY
  reclaim "$output"
  test -z "$(ls -A -- "$output")" || fail "$name left outputs"
}

# Primary path: documented invocation, template, DOCX and PDF, pinned engines.
success_output="$(new_output success)"
local_run "$success_output" "$image" --json convert /work/input/document.md \
  --local --to docx,pdf --template /work/input/template.docx --require-pinned \
  -o /work/output >"$work/success.json"
reclaim "$success_output"
uv run python - "$work/success.json" "$success_output" <<'PY'
import io
import json
import sys
import zipfile
from pathlib import Path

result = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
output = Path(sys.argv[2])
assert result["status"] == "succeeded", result
assert result["engines_pinned"] is True, result
assert result["warnings"] == [], result
assert sorted(path.name for path in output.iterdir()) == [
    "document.docx",
    "document.pdf",
    "document.traceability.json",
]
with zipfile.ZipFile(io.BytesIO((output / "document.docx").read_bytes())) as docx:
    assert b"without a server" in docx.read("word/document.xml")
assert (output / "document.pdf").read_bytes().startswith(b"%PDF-")
manifest = json.loads((output / "document.traceability.json").read_bytes())
assert manifest["mode"] == "local"
assert manifest["template"]["mode"] == "local-file"
assert [engine["name"] for engine in manifest["engines"]] == ["pandoc", "libreoffice"]
assert all(engine["status"] == "pinned" for engine in manifest["engines"])
assert manifest["pdf"]["libreoffice_version"] == manifest["engines"][1]["actual_version"]
assert manifest["pdf"]["template_mode"] == "local-file"
assert "/work" not in json.dumps(manifest)
PY

# Mermaid path: the diagram engines are required and their versions recorded.
mermaid_output="$(new_output mermaid)"
local_run "$mermaid_output" "$image" --json convert /work/input/diagram.md \
  --local -o /work/output >"$work/mermaid.json"
reclaim "$mermaid_output"
mmdc_version="$(local_run "$mermaid_output" --entrypoint mmdc "$image" --version)"
uv run python - "$mermaid_output" "$mmdc_version" <<'PY'
import json
import sys
import zipfile
from pathlib import Path

output = Path(sys.argv[1])
with zipfile.ZipFile(output / "diagram.docx") as docx:
    assert any(name.startswith("word/media/") for name in docx.namelist())
manifest = json.loads((output / "diagram.traceability.json").read_bytes())
engines = {engine["name"]: engine for engine in manifest["engines"]}
assert set(engines) == {"pandoc", "mermaid-cli", "chromium"}, engines
assert engines["mermaid-cli"]["actual_version"] == sys.argv[2].strip(), engines
assert engines["chromium"]["actual_version"], engines
PY

# Failure paths leave no outputs and keep stable, content-free errors.
expect_failure missing-engine engine_missing \
  --env MARKWEAVE_TEMPLATE_LIBREOFFICE_EXECUTABLE=/nonexistent/soffice \
  "$image" --json convert /work/input/document.md --local --to pdf -o /work/output
expect_failure invalid-input validation \
  "$image" --json convert /work/input/invalid.md --local -o /work/output
expect_failure unpinned engine_version_unpinned \
  --env MARKWEAVE_TEMPLATE_PANDOC_EXECUTABLE=/work/input/older-pandoc \
  "$image" --json convert /work/input/document.md --local --require-pinned \
  -o /work/output
started="$(date +%s)"
expect_failure timeout conversion_timeout \
  --env MARKWEAVE_TEMPLATE_PANDOC_EXECUTABLE=/work/input/slow-pandoc \
  "$image" --json --timeout 5 convert /work/input/document.md --local -o /work/output
test "$(($(date +%s) - started))" -lt 120 || fail "timeout was not bounded"

# A failed overwrite keeps the previous complete output set: the runtime UID can
# inspect but not write this host-owned directory, so staging fails.
mkdir -- "$work/output-overwrite"
printf 'previous docx' >"$work/output-overwrite/document.docx"
printf 'previous manifest' >"$work/output-overwrite/document.traceability.json"
chmod 0755 "$work/output-overwrite"
chmod 0644 "$work/output-overwrite/"*
if local_run "$work/output-overwrite" "$image" --json convert \
  /work/input/document.md --local --overwrite -o /work/output \
  >"$work/overwrite.out" 2>"$work/overwrite.err"; then
  fail "read-only overwrite unexpectedly succeeded"
fi
grep -Fq '"code":"output_write_failed"' "$work/overwrite.err" \
  || fail "read-only overwrite did not report output_write_failed"
test "$(cat "$work/output-overwrite/document.docx")" = "previous docx" \
  || fail "failed overwrite changed the previous DOCX"
test "$(cat "$work/output-overwrite/document.traceability.json")" = "previous manifest" \
  || fail "failed overwrite changed the previous manifest"
test "$(find "$work/output-overwrite" -mindepth 1 | wc -l)" = 2 \
  || fail "failed overwrite left staged files"

# Cancellation: SIGTERM to the container's markweave process stops the engine.
cancel_output="$(new_output cancel)"
podman run --detach --name "$cancel_name" \
  --user "$runtime_uid:0" --read-only --cap-drop=all \
  --security-opt=no-new-privileges --security-opt="seccomp=$seccomp_profile" \
  --network=none --memory=1g --cpus=2 --pids-limit=256 \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777 \
  --tmpfs /work:rw,nosuid,nodev,size=256m,mode=0770 --shm-size=256m \
  --env-file "$env_file" \
  --env MARKWEAVE_TEMPLATE_PANDOC_EXECUTABLE=/work/input/slow-pandoc \
  --volume "$work/input:/work/input:ro,Z" --volume "$cancel_output:/work/output:Z" \
  "$image" --json convert /work/input/document.md --local -o /work/output >/dev/null
for _ in $(seq 1 120); do
  if podman top "$cancel_name" args 2>/dev/null | grep -Fq 'sleep 300'; then
    break
  fi
  sleep 0.5
done
podman top "$cancel_name" args | grep -Fq 'sleep 300' || fail "engine never started"
podman stop --time 30 "$cancel_name" >/dev/null
test "$(podman inspect "$cancel_name" --format '{{.State.ExitCode}}')" = 1 \
  || fail "cancellation did not exit with status 1"
podman logs "$cancel_name" 2>&1 | grep -Fq '"code":"conversion_cancelled"' \
  || fail "cancellation error was not reported"
reclaim "$cancel_output"
test -z "$(ls -A -- "$cancel_output")" || fail "cancellation left outputs"

# Overlapping publication: while another publisher holds the output directory
# lock, an --overwrite run waits with the previous set intact; SIGTERM cancels it
# without publishing, and releasing the lock lets a run publish its complete set.
locked_run() {
  local output="$1"
  podman run --detach --name "$locked_name" \
    --user "$runtime_uid:0" --read-only --cap-drop=all \
    --security-opt=no-new-privileges --security-opt="seccomp=$seccomp_profile" \
    --network=none --memory=1g --cpus=2 --pids-limit=256 \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777 \
    --tmpfs /work:rw,nosuid,nodev,size=256m,mode=0770 --shm-size=256m \
    --env-file "$env_file" \
    --volume "$work/input:/work/input:ro,Z" --volume "$output:/work/output:Z" \
    "$image" --json convert /work/input/document.md --local --overwrite \
    -o /work/output >/dev/null
}
# Hold the lock for a fixed period: releasing means waiting for the holder.
hold_lock() {
  podman unshare flock --exclusive "$1" sleep 25 &
  lock_holder=$!
  for _ in $(seq 1 100); do
    if ! podman unshare flock --nonblock "$1" true 2>/dev/null; then
      return 0
    fi
    sleep 0.1
  done
  fail "the publication lock was not acquired"
}
release_lock() {
  wait "$lock_holder" || fail "the publication lock holder failed"
  lock_holder=""
}
previous_set() {
  printf 'previous docx' | podman unshare tee "$1/document.docx" >/dev/null
  printf 'previous manifest' \
    | podman unshare tee "$1/document.traceability.json" >/dev/null
  podman unshare chown "$runtime_uid:0" \
    "$1/document.docx" "$1/document.traceability.json"
}
expect_previous_set() {
  test "$(podman unshare cat "$1/document.docx")" = "previous docx" \
    || fail "$2 changed the previous DOCX"
  test "$(podman unshare cat "$1/document.traceability.json")" = "previous manifest" \
    || fail "$2 changed the previous manifest"
  test "$(podman unshare find "$1" -mindepth 1 | wc -l)" = 2 \
    || fail "$2 left extra files"
}
for outcome in cancelled released; do
  locked_output="$(new_output "locked-$outcome")"
  previous_set "$locked_output"
  hold_lock "$locked_output"
  locked_run "$locked_output"
  sleep 12
  test "$(podman inspect "$locked_name" --format '{{.State.Running}}')" = true \
    || fail "$outcome publication did not wait for the output lock"
  expect_previous_set "$locked_output" "waiting $outcome publication"
  if [[ "$outcome" == cancelled ]]; then
    podman stop --time 30 "$locked_name" >/dev/null
    release_lock
    test "$(podman inspect "$locked_name" --format '{{.State.ExitCode}}')" = 1 \
      || fail "cancelled locked publication did not exit with status 1"
    podman logs "$locked_name" 2>&1 | grep -Fq '"code":"conversion_cancelled"' \
      || fail "cancelled locked publication did not report cancellation"
    expect_previous_set "$locked_output" "cancelled locked publication"
  else
    release_lock
    test "$(podman wait "$locked_name")" = 0 \
      || fail "released locked publication failed"
    reclaim "$locked_output"
    uv run python - "$locked_output" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

output = Path(sys.argv[1])
manifest = json.loads((output / "document.traceability.json").read_bytes())
docx = (output / "document.docx").read_bytes()
assert manifest["mode"] == "local", manifest
assert manifest["outputs"][0]["sha256"] == hashlib.sha256(docx).hexdigest()
PY
  fi
  podman rm --force "$locked_name" >/dev/null
done

echo "Final-image local conversion E2E passed for $image."
