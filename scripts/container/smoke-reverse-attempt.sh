#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 2 ]]; then
  echo "Usage: smoke-reverse-attempt.sh [IMAGE] [MEASUREMENT_RECEIPT]" >&2
  exit 2
fi
readonly requested_image="${1:-localhost/markweave-reverse-attempt:t70}"
readonly measurement_receipt="${2:-}"
image="$(podman image inspect "$requested_image" --format '{{.Id}}')"
if [[ "$image" =~ ^[0-9a-f]{64}$ ]]; then image="sha256:$image"; fi
readonly image
[[ "$image" =~ ^sha256:[0-9a-f]{64}$ ]]
if [[ -n "$measurement_receipt" ]]; then
  uv run python - "$measurement_receipt" <<'PY_OUTPUT'
from pathlib import Path
import sys
path = Path(sys.argv[1])
if not path.is_absolute() or path.resolve().is_relative_to(Path.cwd().resolve()):
    raise SystemExit("measurement receipt must be an absolute path outside the repository")
if path.exists() or path.is_symlink() or not path.parent.is_dir():
    raise SystemExit("measurement receipt destination must be new with an existing parent")
PY_OUTPUT
fi
readonly expected_entrypoint='["python","-m","markweave.reversions.attempt_main"]'

test "$(podman image inspect "$image" --format '{{json .Config.Entrypoint}}')" = \
  "$expected_entrypoint"
test "$(podman image inspect "$image" --format '{{json .Config.Cmd}}')" = 'null'
test "$(podman image inspect "$image" --format '{{json .Config.ExposedPorts}}')" = 'null'

# These are bounded smoke-harness ceilings, not the T71-owned production budgets.
podman run --rm \
  --network none \
  --read-only \
  --cap-drop all \
  --security-opt no-new-privileges \
  --pids-limit 16 \
  --memory 256m \
  --cpus 1 \
  --user 12345:0 \
  --tmpfs /work:rw,noexec,nosuid,nodev,size=32m,mode=0770 \
  --entrypoint python \
  "$image" \
  -c 'import anydoc, hashlib, importlib.util, shutil
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from markweave.conversion.images import ImageLimits, normalize_image
assert version("firecrawl-anydoc") == "0.2.4"
assert hashlib.sha256(Path("/opt/markweave/licenses/markweave/LICENSE").read_bytes()).hexdigest() == "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"
assert importlib.util.find_spec("fastapi") is None
assert importlib.util.find_spec("uvicorn") is None
for executable in ("mmdc", "pandoc", "soffice", "google-chrome", "chromium"):
    assert shutil.which(executable) is None, executable
svg = b"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"2\" height=\"2\"><rect width=\"2\" height=\"2\" fill=\"red\"/></svg>"
normalized = normalize_image(
    PurePosixPath("safe.svg"),
    svg,
    ImageLimits(10000, 100, 100, 10000, 100, 16),
)
assert normalized.startswith(b"\x89PNG\r\n\x1a\n")
'

run_root="$(mktemp -d)"
readonly run_root
readonly workspace="$run_root/work"
readonly stdout_file="$run_root/stdout"
readonly stderr_file="$run_root/stderr"
cleanup() {
  if test -n "${container_id:-}"; then
    podman rm --force "$container_id" >/dev/null 2>&1 || true
  fi
  podman unshare rm -rf -- "$run_root"
}
trap cleanup EXIT

uv run python - "$workspace" <<'PY'
import io
from pathlib import Path
import sys
from uuid import UUID
import zipfile

from markweave.reversions.attempt_channel import (
    encode_channel_state,
    encode_request_metadata,
)
from markweave.reversions.models import ReverseAttemptRequest, ReverseContentLimits

workspace = Path(sys.argv[1])
workspace.mkdir(mode=0o770)
fixture = Path("spikes/anydoc/corpus/docx/text.docx").read_bytes()
rebuilt = io.BytesIO()
with zipfile.ZipFile(io.BytesIO(fixture)) as source_archive, zipfile.ZipFile(
    rebuilt, "w"
) as output_archive:
    for info in source_archive.infolist():
        content = source_archive.read(info)
        if info.filename == "word/_rels/document.xml.rels":
            content = content.replace(
                b'Target="../../fixture-src/sibling.odt"',
                b'Target="https://example.test/sibling"',
            )
        output_archive.writestr(info, content)
source = rebuilt.getvalue()
limits = ReverseContentLimits(
    max_input_bytes=1_000_000,
    max_output_bytes=2_000_000,
    max_image_source_bytes=100_000,
    max_image_width_pixels=1_000,
    max_image_height_pixels=1_000,
    max_image_pixels=1_000_000,
    max_svg_elements=1_000,
    max_svg_depth=32,
    max_asset_count=16,
    max_total_asset_source_bytes=500_000,
    max_total_asset_output_bytes=1_000_000,
    max_markdown_bytes=1_000_000,
    max_package_bytes=2_000_000,
)
request = ReverseAttemptRequest(
    UUID("11111111-1111-4111-8111-111111111111"), ".docx", limits, source
)
(workspace / "source.bin").write_bytes(source)
(workspace / "request.json").write_bytes(encode_request_metadata(request))
(workspace / "response.state").write_bytes(
    encode_channel_state(request.attempt_id, "pending")
)
(workspace / "request.commit").write_bytes(b"committed\n")
PY

# Rootless Podman maps the arbitrary container UID through its subordinate-ID
# namespace. Make the bind-mounted broker workspace writable by that mapped UID.
podman unshare chown -R 12345:0 -- "$workspace"

container_id="$(podman run --detach \
  --network none \
  --read-only \
  --cap-drop all \
  --security-opt no-new-privileges \
  --pids-limit 16 \
  --memory 256m \
  --cpus 1 \
  --user 12345:0 \
  --volume "$workspace:/work:rw" \
  --env MARKWEAVE_REVERSE_MAX_INPUT_BYTES=1000000 \
  --env MARKWEAVE_REVERSE_MAX_OUTPUT_BYTES=2000000 \
  "$image")"

deadline="$((SECONDS + 60))"
until podman unshare grep -q '"state":"complete"' "$workspace/response.state"; do
  test "$SECONDS" -lt "$deadline"
  sleep 0.1
done
podman logs "$container_id" >"$stdout_file" 2>"$stderr_file"
podman kill --signal KILL "$container_id" >/dev/null
test "$(podman wait "$container_id")" = "137"
podman rm "$container_id" >/dev/null
container_id=""

test ! -s "$stdout_file"
test ! -s "$stderr_file"
podman run --rm \
  --network none \
  --read-only \
  --cap-drop all \
  --security-opt no-new-privileges \
  --user 12345:0 \
  --volume "$workspace:/work:ro" \
  --entrypoint python \
  "$image" -c '
import io
import json
import zipfile
from uuid import UUID

from markweave.reversions.attempt_channel import decode_response_metadata
from markweave.reversions.models import ReverseAttemptSuccess, ReverseOutputMode

with open("/work/result.bin", "rb") as stream:
    result = stream.read()
with open("/work/response.json", "rb") as stream:
    metadata = stream.read()
response = decode_response_metadata(
    metadata, result
)
assert response == ReverseAttemptSuccess(
    UUID("11111111-1111-4111-8111-111111111111"),
    ReverseOutputMode.MARKDOWN_WITH_ASSETS,
    result,
)
with zipfile.ZipFile(io.BytesIO(result)) as archive:
    assert archive.namelist() == [
        "document.md",
        "assets/image-0001.png",
        "manifest.json",
    ]
    markdown = archive.read("document.md")
    assert b"![tiny red dot](assets/image-0001.png)" in markdown
    assert archive.read("assets/image-0001.png").startswith(b"\x89PNG\r\n\x1a\n")
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["source"] == {
        "family": "word",
        "detected_format": "docx",
    }
    assert manifest["result"]["mode"] == "markdown_with_assets"
    assert manifest["result"]["asset_count"] == 1
'

if [[ -n "$measurement_receipt" ]]; then
  # Reuse T69 instrumentation inside the exact final image. These are smoke
  # containment ceilings, not approved production performance thresholds.
  podman run --rm --timeout 60 \
    --network none --read-only --cap-drop all \
    --security-opt no-new-privileges \
    --pids-limit 16 --memory 256m --cpus 1 --user 12345:0 \
    --tmpfs /work:rw,noexec,nosuid,nodev,size=32m,mode=0770 \
    --volume "$PWD/spikes/anydoc/probe.py:/probe.py:ro" \
    --volume "$PWD/spikes/anydoc/corpus:/corpus:ro" \
    --entrypoint python "$image" -c '
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import sys

# Inspect the actual runtime namespace; do not infer these observations from flags.
status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())
assert os.getuid() == 12345
assert int(status["CapEff"].strip(), 16) == 0
assert status["NoNewPrivs"].strip() == "1"
assert sorted(path.name for path in Path("/sys/class/net").iterdir()) == ["lo"]
accelerators = [str(path) for pattern in ("nvidia*", "dri", "kfd", "accel*") for path in Path("/dev").glob(pattern)]
assert not accelerators
for module in ("torch", "tensorflow", "onnxruntime", "jax", "cupy", "tensorrt"):
    assert importlib.util.find_spec(module) is None
cgroup = Path("/sys/fs/cgroup")
observed_limits = {name: (cgroup / name).read_text().strip() for name in ("cpu.max", "memory.max", "pids.max")}
quota, period = map(int, observed_limits["cpu.max"].split())
assert quota == period
assert int(observed_limits["memory.max"]) == 256 * 1024 * 1024
assert int(observed_limits["pids.max"]) == 16
workspace = os.statvfs("/work")
workspace_bytes = workspace.f_blocks * workspace.f_frsize
assert 0 < workspace_bytes <= 32 * 1024 * 1024

sys.argv = ["/probe.py", "--corpus", "/corpus", "--iterations", "5"]
output = io.StringIO()
with contextlib.redirect_stdout(output):
    runpy.run_path("/probe.py", run_name="__main__")
raw = output.getvalue()
assert len(raw.encode()) <= 131072
report = json.loads(raw)
assert report["environment"]["rayon_num_threads"] == "1"
assert all(value is None for value in report["environment"]["visible_accelerators"].values())
assert report["process_inventory"]["loaded_module_names"] == []
assert all(value is None for value in report["process_inventory"]["engine_executables_on_path"].values())
assert all(not sample["child_processes_observed"] for section in ("cases", "concurrency") for sample in report[section])
assert not report["cancellation"]["child_processes_observed"]
assert report["offline_no_ocr"]["result"] == "needs_ocr"
assert [sample["workers"] for sample in report["concurrency"]] == [1, 2, 4]
report["runtime_observations"] = {
    "accelerator_devices": accelerators,
    "network_interfaces": ["lo"],
    "effective_capabilities": 0,
    "no_new_privileges": True,
    "uid": os.getuid(),
    "cgroup_limits": observed_limits,
    "workspace_capacity_bytes": workspace_bytes,
}
print(json.dumps(report, sort_keys=True))
' 2>/dev/null | head -c 131073 > "$run_root/measurements.json"

  uv run python - "$run_root/measurements.json" "$measurement_receipt" "$image" <<'PY_RECEIPT'
import json
import os
from pathlib import Path
import sys

source, destination, image_id = sys.argv[1:]
raw = Path(source).read_bytes()
if not 0 < len(raw) <= 131072:
    raise SystemExit("final-image measurement output exceeds the bounded receipt")
measurements = json.loads(raw)
receipt = {
    "schema_version": 1,
    "image_id": image_id,
    "measurement_scope": "T69 engine probe inside the final reverse-attempt image; fixed entrypoint smoke passed separately",
    "harness_containment": {
        "cpu_cores": 1, "memory_bytes": 268435456, "pids": 16,
        "workspace_bytes": 33554432, "runtime_deadline_seconds": 60,
        "network": "none", "read_only_root": True,
    },
    "qualification": {
        "production_numeric_thresholds_approved": False,
        "child_process_observation": "sampled; zero observations do not exclude transient processes",
        "concurrency_scope": "T69 in-process comparison at 1, 2, and 4 workers; not production worker admission",
    },
    "measurements": measurements,
}
payload = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
if len(payload) > 131072:
    raise SystemExit("final-image measurement receipt exceeds its byte ceiling")
fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "wb") as output:
    output.write(payload)
PY_RECEIPT
fi
