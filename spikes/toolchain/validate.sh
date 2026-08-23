#!/usr/bin/env bash
set -euo pipefail

readonly FIXTURES=/opt/toolchain/fixtures
readonly EXPECTED_UID="${EXPECTED_UID:-}"
readonly EXPECTED_WORK_STORAGE="${EXPECTED_WORK_STORAGE:-tmpfs}"
readonly VALIDATION_SCOPE="${VALIDATION_SCOPE:-documents}"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

require_writable_directory() {
    local directory="$1"
    mkdir -p "${directory}"
    [[ -w "${directory}" ]] || fail "${directory} is not writable"
}

status_value() {
    local name="$1"
    awk -v name="${name}:" '$1 == name { print $2 }' /proc/self/status
}

assert_mount() {
    local path="$1"
    local filesystem="$2"
    shift 2
    python3 - "${path}" "${filesystem}" "$@" <<'PY'
import sys

path, filesystem, *required = sys.argv[1:]
for line in open("/proc/mounts", encoding="utf-8"):
    fields = line.split()
    if fields[1] != path:
        continue
    options = set(fields[3].split(","))
    exact = {option for option in required if not option.endswith("=")}
    prefixes = {option for option in required if option.endswith("=")}
    if (
        fields[2] != filesystem
        or not exact.issubset(options)
        or any(not any(value.startswith(prefix) for value in options) for prefix in prefixes)
    ):
        raise SystemExit(1)
    break
else:
    raise SystemExit(1)
PY
}

[[ "$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" == "3.14" ]] \
    || fail "Python 3.14 is required"
[[ -z "${EXPECTED_UID}" || "$(id -u)" == "${EXPECTED_UID}" ]] \
    || fail "runtime UID does not match EXPECTED_UID=${EXPECTED_UID}"
[[ "$(id -u)" != 0 ]] || fail "the validation must not run as root"

[[ "$(status_value CapEff)" == "0000000000000000" ]] \
    || fail "effective capabilities are not empty"
[[ "$(status_value CapBnd)" == "0000000000000000" ]] \
    || fail "capability bounding set is not empty"
[[ "$(status_value NoNewPrivs)" == "1" ]] \
    || fail "no-new-privileges is not enabled"
[[ "$(status_value Seccomp)" == "2" ]] \
    || fail "seccomp filter mode is not enabled"

if touch /opt/app-root/src/t00-root-write-probe 2>/tmp/root-write.err; then
    fail "the container root filesystem is writable"
fi
grep -Fq 'Read-only file system' /tmp/root-write.err \
    || fail "root write rejection did not report a read-only filesystem"
assert_mount / overlay ro || fail "the root mount is not a read-only overlay"
assert_mount /tmp tmpfs rw nosuid nodev noexec size= \
    || fail "/tmp does not have the required bounded tmpfs mount options"
case "${EXPECTED_WORK_STORAGE}" in
    tmpfs)
        assert_mount /work tmpfs rw nosuid nodev size= \
            || fail "/work does not have the required bounded tmpfs mount options"
        ;;
    disk)
        python3 - <<'PY' || fail "/work is not a dedicated writable disk-backed mount"
for line in open("/proc/mounts", encoding="utf-8"):
    fields = line.split()
    if fields[1] == "/work":
        if fields[2] == "tmpfs" or "rw" not in fields[3].split(","):
            raise SystemExit(1)
        break
else:
    raise SystemExit(1)
PY
        ;;
    *)
        fail "unknown EXPECTED_WORK_STORAGE=${EXPECTED_WORK_STORAGE}"
        ;;
esac
assert_mount /dev/shm tmpfs rw nosuid nodev noexec size= \
    || fail "/dev/shm does not have the required bounded tmpfs mount options"

interfaces="$(find /sys/class/net -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort)"
readonly interfaces
[[ "${interfaces}" == "lo" ]] || fail "network isolation exposed a non-loopback interface"

[[ -r /sys/fs/cgroup/memory.max && "$(</sys/fs/cgroup/memory.max)" != "max" ]] \
    || fail "memory is not bounded by cgroup"
[[ -r /sys/fs/cgroup/pids.max && "$(</sys/fs/cgroup/pids.max)" != "max" ]] \
    || fail "process count is not bounded by cgroup"
read -r cpu_quota _ </sys/fs/cgroup/cpu.max
[[ "${cpu_quota}" != "max" ]] || fail "CPU is not bounded by cgroup"

printf 'security_properties=passed\n'
if [[ "${VALIDATION_SCOPE}" == "security" ]]; then
    exit 0
fi

require_writable_directory /work
require_writable_directory "${HOME}"
require_writable_directory "${XDG_CACHE_HOME}"
require_writable_directory "${XDG_CONFIG_HOME}"
require_writable_directory "${XDG_DATA_HOME}"
require_writable_directory "${XDG_RUNTIME_DIR}"
chmod 0700 "${XDG_RUNTIME_DIR}"

cp -R "${FIXTURES}/." /work/
python3 - <<'PY'
from base64 import b64decode
from pathlib import Path

Path("/work/validation.png").write_bytes(
    b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
)
PY

pandoc \
    --from=commonmark \
    --to=json \
    /work/commonmark-compatibility.md \
    --output=/work/commonmark.json
pandoc \
    --from=commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html \
    --to=json \
    /work/commonmark-compatibility.md \
    --output=/work/commonmark-x-candidate.json
pandoc \
    --from=commonmark_x-yaml_metadata_block \
    --to=json \
    /work/commonmark-compatibility.md \
    --output=/work/commonmark-x-no-metadata.json
if pandoc \
    --from=commonmark_x-raw_tex \
    --to=json \
    /work/commonmark-compatibility.md \
    --output=/work/commonmark-x-no-raw-tex.json \
    2>/work/commonmark-x-no-raw-tex.err; then
    fail "commonmark_x unexpectedly accepted the unsupported raw_tex extension"
fi
grep -Fq "The extension 'raw_tex' is not supported for commonmark_x" \
    /work/commonmark-x-no-raw-tex.err \
    || fail "commonmark_x raw_tex rejection changed unexpectedly"

python3 - <<'PY'
import json
from pathlib import Path


def load(name):
    return json.loads(Path(name).read_text(encoding="utf-8"))


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def inline_text(value):
    parts = []
    for node in walk(value):
        if node.get("t") == "Str":
            parts.append(node["c"])
        elif node.get("t") in {"Space", "SoftBreak", "LineBreak"}:
            parts.append(" ")
    return "".join(parts)


def nodes(document, node_type):
    return [node for node in walk(document) if node.get("t") == node_type]


def assert_core_structures(document, label):
    headers = [node for node in nodes(document, "Header") if node["c"][0] == 1]
    if len(headers) != 1 or inline_text(headers[0]["c"][2]) != "Heading":
        raise SystemExit(f"{label} lost the exact heading fixture")

    bullet_lists = nodes(document, "BulletList")
    if len(bullet_lists) != 1 or inline_text(bullet_lists[0]) != "list item":
        raise SystemExit(f"{label} lost the exact bullet-list fixture")

    block_quotes = nodes(document, "BlockQuote")
    if len(block_quotes) != 1 or inline_text(block_quotes[0]) != "block quote":
        raise SystemExit(f"{label} lost the exact block-quote fixture")

    code_blocks = nodes(document, "CodeBlock")
    if (
        len(code_blocks) != 1
        or code_blocks[0]["c"][0][1] != ["python"]
        or code_blocks[0]["c"][1] != 'print("code block")'
    ):
        raise SystemExit(f"{label} lost the exact fenced-code fixture")

    links = nodes(document, "Link")
    if (
        len(links) != 1
        or inline_text(links[0]["c"][1]) != "link"
        or links[0]["c"][2] != ["https://example.invalid/", ""]
    ):
        raise SystemExit(f"{label} lost the exact link fixture")

    images = nodes(document, "Image")
    if (
        len(images) != 1
        or inline_text(images[0]["c"][1]) != "local image"
        or images[0]["c"][2] != ["validation.png", ""]
    ):
        raise SystemExit(f"{label} lost the exact image fixture")
    return images[0]


baseline = load("/work/commonmark.json")
candidate = load("/work/commonmark-x-candidate.json")
no_metadata = load("/work/commonmark-x-no-metadata.json")

baseline_image = assert_core_structures(baseline, "plain commonmark")
candidate_image = assert_core_structures(candidate, "commonmark_x candidate")
if baseline_image["c"][0] != ["", [], []]:
    raise SystemExit("plain commonmark unexpectedly parsed image attributes")

if nodes(baseline, "Table") or nodes(baseline, "Note"):
    raise SystemExit("plain commonmark unexpectedly parsed an extension structure")
tables = nodes(candidate, "Table")
if len(tables) != 1 or [
    node["c"] for node in walk(tables[0]) if node.get("t") == "Str"
] != ["left", "right", "one", "two"]:
    raise SystemExit("commonmark_x candidate lost the exact table fixture")
notes = nodes(candidate, "Note")
if len(notes) != 1 or inline_text(notes[0]) != "Footnote body.":
    raise SystemExit("commonmark_x candidate lost the exact footnote fixture")

if "title" not in candidate["meta"] or inline_text(candidate["meta"]["title"]) != "Compatibility matrix":
    raise SystemExit("commonmark_x candidate did not parse YAML metadata")
if no_metadata["meta"]:
    raise SystemExit("disabling yaml_metadata_block unexpectedly retained metadata")

identifier, classes, key_values = candidate_image["c"][0]
if identifier != "probe-image" or ["width", "24px"] not in key_values:
    raise SystemExit("commonmark_x candidate lost image attributes")
if classes or key_values != [["width", "24px"]]:
    raise SystemExit("commonmark_x candidate added unexpected image attributes")

raw_html = [
    node
    for node in walk(candidate)
    if node.get("t") in {"RawBlock", "RawInline"}
    and node.get("c", [None])[0] == "html"
]
if [node["c"] for node in raw_html] != [["html", "<span>"], ["html", "</span>"]]:
    raise SystemExit("commonmark_x raw HTML behavior changed for the exact fixture")
raw_html_block = candidate["blocks"][-2]
if raw_html_block != {
    "t": "Para",
    "c": [
        {"t": "RawInline", "c": ["html", "<span>"]},
        {"t": "Str", "c": "raw"},
        {"t": "Space"},
        {"t": "Str", "c": "HTML"},
        {"t": "Space"},
        {"t": "Str", "c": "probe"},
        {"t": "RawInline", "c": ["html", "</span>"]},
    ],
}:
    raise SystemExit("commonmark_x did not retain the exact raw HTML fixture as raw inline nodes")

tex_nodes = [
    node
    for node in walk(candidate)
    if node.get("t") in {"RawBlock", "RawInline"}
    and node.get("c", [None])[0] == "tex"
]
if tex_nodes:
    raise SystemExit("commonmark_x unexpectedly parsed the TeX-like fixture as raw TeX")
last_block = candidate["blocks"][-1]
if last_block.get("t") != "Para" or inline_text(last_block) != r"\newcommand{\probe}{raw TeX probe}":
    raise SystemExit("commonmark_x did not retain the exact TeX-like fixture as ordinary text")
PY
fc-cache --force
find "${XDG_CACHE_HOME}/fontconfig" -type f -print -quit | grep -q . \
    || fail "Fontconfig did not create a writable per-user cache"
fc-match "DejaVu Sans" | grep -Fq 'DejaVuSans.ttf' \
    || fail "Fontconfig did not resolve DejaVu Sans"

pandoc --print-default-data-file reference.docx > /work/reference.docx
if pandoc \
    --from=commonmark_x+pipe_tables+footnotes \
    --sandbox \
    --resource-path=/work \
    --reference-doc=/work/reference.docx \
    /work/document.md \
    --output=/work/sandboxed.docx \
    2>/work/sandboxed.err; then
    grep -Fq 'PandocResourceNotFound "validation.png"' /work/sandboxed.err \
        || fail "Pandoc sandbox unexpectedly resolved a local image"
else
    fail "Pandoc sandbox probe did not complete deterministically"
fi

pandoc \
    --from=commonmark_x+pipe_tables+footnotes \
    --resource-path=/work \
    --reference-doc=/work/reference.docx \
    /work/document.md \
    --output=/work/document.docx

python3 - <<'PY'
from pathlib import Path
from zipfile import ZipFile

document = Path("/work/document.docx")
if not document.is_file() or document.stat().st_size == 0:
    raise SystemExit("Pandoc did not produce a DOCX file")
with ZipFile(document) as archive:
    names = archive.namelist()
    if "word/document.xml" not in names:
        raise SystemExit("DOCX has no Word document part")
    if not any(name.startswith("word/media/") for name in names):
        raise SystemExit("DOCX has no embedded local image")

sandboxed = Path("/work/sandboxed.docx")
if not sandboxed.is_file() or sandboxed.stat().st_size == 0:
    raise SystemExit("Pandoc sandbox did not produce a DOCX file")
with ZipFile(sandboxed) as archive:
    names = archive.namelist()
    if "word/document.xml" not in names:
        raise SystemExit("Sandboxed DOCX has no Word document part")
    if any(name.startswith("word/media/") for name in names):
        raise SystemExit("Sandboxed DOCX unexpectedly contains the local image")
PY

if [[ "${VALIDATION_SCOPE}" == "target" ]]; then
    cat > /work/puppeteer.json <<'JSON'
{
  "executablePath": "/usr/bin/google-chrome-stable",
  "headless": "shell"
}
JSON
    mmdc \
        --puppeteerConfigFile /work/puppeteer.json \
        --input /work/diagram.mmd \
        --output /work/diagram.svg \
        --backgroundColor transparent
    [[ -s /work/diagram.svg ]] || fail "Mermaid did not produce an SVG"
    node /opt/toolchain/node/check-chrome-sandbox.mjs
fi

mkdir /work/libreoffice-profile /work/pdf
soffice \
    --headless \
    --nologo \
    --nodefault \
    --nofirststartwizard \
    "-env:UserInstallation=file:///work/libreoffice-profile" \
    --convert-to pdf \
    --outdir /work/pdf \
    /work/document.docx
[[ -s /work/pdf/document.pdf ]] || fail "LibreOffice did not produce a PDF"

python3 - <<'PY'
from pathlib import Path

pdf = Path("/work/pdf/document.pdf").read_bytes()
if not pdf.startswith(b"%PDF-"):
    raise SystemExit("LibreOffice output is not a PDF")
PY

printf 'python=%s\n' "$(python3 --version 2>&1)"
printf 'pandoc=%s\n' "$(pandoc --version | head -n 1)"
printf 'mermaid=%s\n' "$(mmdc --version)"
printf 'chrome=%s\n' "$(google-chrome-stable --version)"
printf 'libreoffice=%s\n' "$(soffice --version)"
printf 'font=%s\n' "$(fc-match 'DejaVu Sans' | head -n 1)"
printf 'uid=%s gid=%s\n' "$(id -u)" "$(id -g)"
printf 'validation_scope=%s\n' "${VALIDATION_SCOPE}"
printf 'work_storage=%s\n' "${EXPECTED_WORK_STORAGE}"
printf 'commonmark_compatibility=passed\n'
printf 'rpm_inventory_count=%s\n' "$(wc -l < /opt/toolchain/evidence/rpm-inventory.txt)"
printf 'rpm_inventory_sha256=%s\n' \
    "$(sha256sum /opt/toolchain/evidence/rpm-inventory.txt | cut -d' ' -f1)"
for metric in memory.max memory.peak pids.max pids.peak; do
    if [[ -r "/sys/fs/cgroup/${metric}" ]]; then
        printf 'cgroup_%s=%s\n' "${metric//./_}" "$(<"/sys/fs/cgroup/${metric}")"
    fi
done
printf 'work_kib=%s\n' "$(du -sk /work | cut -f1)"
printf 'validation=passed\n'
