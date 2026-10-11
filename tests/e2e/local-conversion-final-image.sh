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
readonly first_name="markweave-t106-local-first-$$"
readonly second_name="markweave-t106-local-second-$$"
readonly sync_hook="$repository/tests/e2e/fixtures/local-publication-sync"
lock_holder=""

work="$(mktemp -d "${TMPDIR:-/tmp}/markweave-t106-local.XXXXXX")"
readonly work
cleanup() {
  podman rm --force --ignore "$cancel_name" "$locked_name" "$first_name" \
    "$second_name" >/dev/null 2>&1 || true
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

# Interrupted partial commits. Each previous set has a DOCX, PDF, and manifest
# owned by the runtime UID, so a run can hard-link originals before replacing them.
full_previous_set() {
  local name
  for name in document.docx document.pdf document.traceability.json; do
    printf 'previous %s' "$name" | podman unshare tee "$1/$name" >/dev/null
  done
  podman unshare chown "$runtime_uid:0" \
    "$1/document.docx" "$1/document.pdf" "$1/document.traceability.json"
}
set_digests() {
  podman unshare sh -c 'cd "$1" && find . -mindepth 1 | LC_ALL=C sort | \
    while read -r name; do printf "%s " "$name"; sha256sum <"$name"; done' \
    sh "$1"
}
inode_of() {
  podman unshare stat -c '%i' "$1"
}
ctime_of() {
  podman unshare stat -c '%z' "$1"
}
wait_for_marker() {
  local _
  for _ in $(seq 1 600); do
    if podman unshare test -e "$1"; then
      return 0
    fi
    sleep 0.2
  done
  fail "synchronization marker $(basename "$1") never appeared"
}
new_sync() {
  local directory="$work/sync-$1"
  mkdir -- "$directory"
  chmod 0770 -- "$directory"
  podman unshare chown "$runtime_uid:0" "$directory"
  printf '%s\n' "$directory"
}
publication_run() {
  local name="$1" output="$2" sync="$3" modes="$4"
  shift 4
  podman run --detach --name "$name" \
    --user "$runtime_uid:0" --read-only --cap-drop=all \
    --security-opt=no-new-privileges --security-opt="seccomp=$seccomp_profile" \
    --network=none --memory=1g --cpus=2 --pids-limit=256 \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777 \
    --tmpfs /work:rw,nosuid,nodev,size=256m,mode=0770 --shm-size=256m \
    --env-file "$env_file" \
    --env PYTHONPATH=/work/hooks \
    --env "MARKWEAVE_E2E_PUBLICATION_SYNC=$modes" \
    --volume "$sync_hook:/work/hooks:ro,Z" \
    --volume "$sync:/work/sync:Z" \
    --volume "$work/input:/work/input:ro,Z" --volume "$output:/work/output:Z" \
    "$@" \
    "$image" --json convert /work/input/document.md --local --overwrite \
    --to docx,pdf -o /work/output >/dev/null
}

# 1. Failure after a real first replacement, without any hook: the PDF
# destination is a read-only single-file bind mount, so its commit fails after
# the DOCX was replaced. The original DOCX inode must be restored (its ctime
# proves it was relinked and renamed) together with the complete previous set.
failed_output="$(new_output partial-failure)"
full_previous_set "$failed_output"
failed_before="$(set_digests "$failed_output")"
failed_inode="$(inode_of "$failed_output/document.docx")"
failed_ctime="$(ctime_of "$failed_output/document.docx")"
if local_run "$failed_output" \
  --volume "$failed_output/document.pdf:/work/output/document.pdf:ro,Z" \
  "$image" --json convert /work/input/document.md --local --overwrite \
  --to docx,pdf -o /work/output >"$work/partial-failure.out" \
  2>"$work/partial-failure.err"; then
  fail "partial publication failure unexpectedly succeeded"
fi
grep -Fq '"code":"output_write_failed"' "$work/partial-failure.err" \
  || fail "partial publication failure was not reported"
test "$(set_digests "$failed_output")" = "$failed_before" \
  || fail "failed partial publication did not restore the previous set"
test "$(inode_of "$failed_output/document.docx")" = "$failed_inode" \
  || fail "failed partial publication did not restore the original DOCX inode"
test "$(ctime_of "$failed_output/document.docx")" != "$failed_ctime" \
  || fail "the DOCX was never replaced before the PDF failure"

# 2. Cancellation after a real first replacement: the test-only hook pauses
# right after the DOCX replacement, the host checks the new DOCX is published,
# sends SIGTERM to the container, and expects the previous set back.
cancel_output="$(new_output partial-cancel)"
cancel_sync="$(new_sync partial-cancel)"
full_previous_set "$cancel_output"
cancel_before="$(set_digests "$cancel_output")"
cancel_inode="$(inode_of "$cancel_output/document.docx")"
publication_run "$first_name" "$cancel_output" "$cancel_sync" \
  pause-after-first-replacement
wait_for_marker "$cancel_sync/replaced"
test "$(inode_of "$cancel_output/document.docx")" != "$cancel_inode" \
  || fail "the DOCX was not replaced before cancellation"
podman kill --signal TERM "$first_name" >/dev/null
podman unshare touch "$cancel_sync/continue"
test "$(podman wait "$first_name")" = 1 \
  || fail "cancelled partial publication did not exit with status 1"
podman logs "$first_name" 2>&1 | grep -Fq '"code":"conversion_cancelled"' \
  || fail "cancelled partial publication did not report cancellation"
test "$(set_digests "$cancel_output")" = "$cancel_before" \
  || fail "cancelled partial publication did not restore the previous set"
test "$(inode_of "$cancel_output/document.docx")" = "$cancel_inode" \
  || fail "cancelled partial publication did not restore the original DOCX"
podman rm --force "$first_name" >/dev/null

# 3. Competing publisher: the first run replaces the DOCX, fails on its
# read-only PDF, and pauses before restoring while holding the directory lock.
# The second run must report that it is waiting for that lock, and may publish
# only after the rollback finished: its complete set must be digest-consistent.
race_output="$(new_output competing)"
first_sync="$(new_sync competing-first)"
second_sync="$(new_sync competing-second)"
full_previous_set "$race_output"
race_inode="$(inode_of "$race_output/document.docx")"
publication_run "$first_name" "$race_output" "$first_sync" pause-before-restore \
  --volume "$race_output/document.pdf:/work/output/document.pdf:ro,Z"
wait_for_marker "$first_sync/restore-started"
test "$(inode_of "$race_output/document.docx")" != "$race_inode" \
  || fail "the first publisher did not replace the DOCX before rollback"
publication_run "$second_name" "$race_output" "$second_sync" report-lock-wait
wait_for_marker "$second_sync/lock-wait"
test "$(podman inspect "$second_name" --format '{{.State.Running}}')" = true \
  || fail "the competing publisher did not wait for the rollback"
podman unshare touch "$first_sync/continue"
test "$(podman wait "$first_name")" = 1 || fail "first publisher did not fail"
podman logs "$first_name" 2>&1 | grep -Fq '"code":"output_write_failed"' \
  || fail "first publisher did not report its write failure"
test "$(podman wait "$second_name")" = 0 \
  || fail "competing publisher failed: $(podman logs "$second_name" 2>&1)"
reclaim "$race_output"
uv run python - "$race_output" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

output = Path(sys.argv[1])
assert sorted(path.name for path in output.iterdir()) == [
    "document.docx",
    "document.pdf",
    "document.traceability.json",
]
manifest = json.loads((output / "document.traceability.json").read_bytes())
digests = {item["format"]: item["sha256"] for item in manifest["outputs"]}
for output_format in ("docx", "pdf"):
    content = (output / f"document.{output_format}").read_bytes()
    assert digests[output_format] == hashlib.sha256(content).hexdigest(), output_format
assert (output / "document.pdf").read_bytes().startswith(b"%PDF-")
PY
podman rm --force "$first_name" "$second_name" >/dev/null

echo "Final-image local conversion E2E passed for $image."
