#!/usr/bin/env bash
set -euo pipefail

readonly image="${1:-localhost/md-converter:t20}"
readonly runtime_uid="${T20_RUNTIME_UID:-50000}"
readonly application_version="$(uv version --short --locked)"
seccomp_profile="$(pwd)/spikes/toolchain/chrome-seccomp.json"
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
    "import md_converter, os; assert md_converter.__version__ == os.environ[\"EXPECTED_APPLICATION_VERSION\"]"
  test "$(pandoc --version | head -1)" = "pandoc 3.10.2"
  test "$(mmdc --version)" = "11.16.0"
  test "$(google-chrome-stable --version | awk "{\$1=\$1; print}")" = "Google Chrome 151.0.7922.173"
  test "$(soffice --version | awk "{\$1=\$1; print}")" = "LibreOffice 26.2.5.2 cd7284b4cbbfeb507e630c1aac019f4157393acb"
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
  ! touch /opt/md-converter/forbidden
'

if podman run --rm --user 0 --read-only --cap-drop=all \
  --security-opt=no-new-privileges "$image" api >/dev/null 2>&1; then
  echo "Root execution unexpectedly succeeded." >&2
  exit 1
fi

if podman run --rm --user "$runtime_uid:0" --read-only --cap-drop=all \
  "$image" api >/dev/null 2>&1; then
  echo "Execution without no-new-privileges unexpectedly succeeded." >&2
  exit 1
fi

echo "Final-image rootless smoke passed for $image."
