#!/usr/bin/env bash
set -euo pipefail

readonly FIXTURES=/opt/toolchain/fixtures
readonly EXPECTED_UID="${EXPECTED_UID:-}"
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

if touch /opt/app-root/src/t00-root-write-probe 2>/tmp/root-write.err; then
    fail "the container root filesystem is writable"
fi
grep -Fq 'Read-only file system' /tmp/root-write.err \
    || fail "root write rejection did not report a read-only filesystem"
assert_mount / overlay ro || fail "the root mount is not a read-only overlay"
assert_mount /tmp tmpfs rw nosuid nodev noexec size= \
    || fail "/tmp does not have the required bounded tmpfs mount options"
assert_mount /work tmpfs rw nosuid nodev size= \
    || fail "/work does not have the required bounded tmpfs mount options"
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
