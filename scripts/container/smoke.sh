#!/usr/bin/env bash
set -euo pipefail

readonly image="${1:-localhost/md-converter:t20}"
readonly runtime_uid="${T20_RUNTIME_UID:-50000}"
readonly application_version="$(uv version --short --locked)"
seccomp_profile="$(pwd)/toolchain/document-engines/chrome-seccomp.json"
readonly seccomp_profile

run_hardened() {
  podman run --rm \
    --user "$runtime_uid:0" \
    --read-only \
    --cap-drop=all \
    --security-opt=no-new-privileges \
    --security-opt="seccomp=$seccomp_profile" \
    --network=none \
    --memory=768m \
    --cpus=2 \
    --pids-limit=256 \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777 \
    --tmpfs /work:rw,nosuid,nodev,size=256m,mode=0770 \
    --shm-size=128m \
    "$@"
}

run_hardened --env "EXPECTED_APPLICATION_VERSION=$application_version" \
  --entrypoint /bin/bash "$image" -ceu '
  md-converter-preflight
  test "$(id -u)" = "'"$runtime_uid"'"
  /opt/md-converter/venv/bin/python -c \
    "import markweave, os; assert markweave.__version__ == os.environ[\"EXPECTED_APPLICATION_VERSION\"]"
  test "$(pandoc --version | head -1)" = "pandoc 3.10.2"
  test "$(mmdc --version)" = "11.17.0"
  test "$(google-chrome-stable --version | awk "{\$1=\$1; print}")" = "Google Chrome 151.0.7922.173"
  test "$(soffice --version | awk "{\$1=\$1; print}")" = "LibreOffice 26.2.5.2 cd7284b4cbbfeb507e630c1aac019f4157393acb"
  /opt/md-converter/venv/bin/python - <<"PYTHON"
import subprocess
import sys

import markweave
from markweave.conversion.runtime_manifest import COMPONENT_VERSIONS


def reported(*command: str) -> list[str]:
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return completed.stdout.splitlines()[0].split()


recorded = dict(COMPONENT_VERSIONS)
shipped = {
    "chromium": reported("google-chrome-stable", "--version")[2],
    "libreoffice": reported("soffice", "--version")[1],
    "md-converter": markweave.__version__,
    "mermaid-cli": reported("mmdc", "--version")[0],
    "pandoc": reported("pandoc", "--version")[1],
}
if recorded != shipped:
    sys.exit(
        f"Recorded component versions {recorded} differ from the shipped executables {shipped}."
    )
PYTHON
  mkdir -p /work/home /work/xdg/cache /work/xdg/config /work/xdg/data /work/xdg/runtime /work/tmp
  chmod 0700 /work/home /work/xdg/cache /work/xdg/config /work/xdg/data /work/xdg/runtime /work/tmp
  pandoc --print-default-data-file reference.docx > /work/reference.docx
  printf "# Final image smoke\n\nSafe text.\n" > /work/input.md
  pandoc --from=commonmark_x --to=docx --reference-doc=/work/reference.docx --output=/work/output.docx /work/input.md
  printf "flowchart LR\n  A --> B\n" > /work/diagram.mmd
  printf "%s" "{\"executablePath\":\"/usr/bin/google-chrome-stable\",\"headless\":\"shell\"}" > /work/puppeteer.json
  mmdc --quiet --puppeteerConfigFile /work/puppeteer.json --input /work/diagram.mmd --output /work/diagram.png
  soffice --headless --nologo --nodefault --nofirststartwizard \
    -env:UserInstallation=file:///work/libreoffice-profile \
    --convert-to pdf:writer_pdf_Export --outdir /work /work/output.docx >/dev/null
  test -s /work/output.docx
  test -s /work/diagram.png
  test -s /work/output.pdf
  printf "%s\n" "# Math smoke" "" "Inline \$\\alpha^{2} + \\beta\$ text." "" \
    "\$\$\\int_{0}^{1} x \\, dx = \\frac{1}{2}\$\$" > /work/math.md
  pandoc --from=commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html \
    --to=docx --output=/work/math.docx /work/math.md
  soffice --headless --nologo --nodefault --nofirststartwizard \
    -env:UserInstallation=file:///work/libreoffice-profile \
    --convert-to pdf:writer_pdf_Export --outdir /work /work/math.docx >/dev/null
  /opt/md-converter/venv/bin/python - <<"PYTHON"
import sys
import zipfile

from pypdf import PdfReader

with zipfile.ZipFile("/work/math.docx") as archive:
    if b"<m:oMath" not in archive.read("word/document.xml"):
        sys.exit("Pandoc did not write native OMML equations to the DOCX.")
text = "".join(page.extract_text() for page in PdfReader("/work/math.pdf").pages)
if any(symbol not in text for symbol in ("\u03b1", "\u03b2", "\u222b")):
    sys.exit(
        "Equations are missing from the PDF; the LibreOffice Math module is required."
    )
PYTHON
  ! touch /opt/md-converter/forbidden
'

if podman run --rm --user 0 --read-only --cap-drop=all \
  --security-opt=no-new-privileges "$image" serve >/dev/null 2>&1; then
  echo "Root execution unexpectedly succeeded." >&2
  exit 1
fi

if podman run --rm --user "$runtime_uid:0" --read-only --cap-drop=all \
  "$image" serve >/dev/null 2>&1; then
  echo "Execution without no-new-privileges unexpectedly succeeded." >&2
  exit 1
fi

echo "Final-image rootless smoke passed for $image."
