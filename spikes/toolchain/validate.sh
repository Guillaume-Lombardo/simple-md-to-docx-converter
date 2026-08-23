#!/usr/bin/env bash
set -euo pipefail

readonly FIXTURES=/opt/toolchain/fixtures
readonly EXPECTED_UID="${EXPECTED_UID:-}"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

require_writable_directory() {
    local directory="$1"
    mkdir -p "${directory}"
    [[ -w "${directory}" ]] || fail "${directory} is not writable"
}

[[ "$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" == "3.14" ]] \
    || fail "Python 3.14 is required"
[[ -z "${EXPECTED_UID}" || "$(id -u)" == "${EXPECTED_UID}" ]] \
    || fail "runtime UID does not match EXPECTED_UID=${EXPECTED_UID}"
[[ "$(id -u)" != 0 ]] || fail "the validation must not run as root"

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
PY

if [[ "${CHROME_SANDBOX_MODE:-target}" == "namespace-lab" ]]; then
    cat > /work/puppeteer.json <<'JSON'
{
  "executablePath": "/usr/bin/google-chrome-stable",
  "headless": "shell",
  "args": ["--disable-setuid-sandbox"]
}
JSON
else
    cat > /work/puppeteer.json <<'JSON'
{
  "executablePath": "/usr/bin/google-chrome-stable",
  "headless": "shell"
}
JSON
fi
mmdc \
    --puppeteerConfigFile /work/puppeteer.json \
    --input /work/diagram.mmd \
    --output /work/diagram.svg \
    --backgroundColor transparent
[[ -s /work/diagram.svg ]] || fail "Mermaid did not produce an SVG"

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
printf 'chrome_sandbox_mode=%s\n' "${CHROME_SANDBOX_MODE:-target}"
for metric in memory.max memory.peak pids.max pids.peak; do
    if [[ -r "/sys/fs/cgroup/${metric}" ]]; then
        printf 'cgroup_%s=%s\n' "${metric//./_}" "$(<"/sys/fs/cgroup/${metric}")"
    fi
done
printf 'work_kib=%s\n' "$(du -sk /work | cut -f1)"
printf 'validation=passed\n'
