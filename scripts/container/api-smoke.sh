#!/usr/bin/env bash
set -euo pipefail

readonly image="${1:-localhost/md-converter:t20}"
readonly container_name=md-converter-t20-api-smoke
readonly clamav_name=md-converter-t20-api-smoke-clamav
readonly network_name=md-converter-t20-api-smoke
readonly runtime_uid="${T20_RUNTIME_UID:-50000}"
seccomp_profile="$(pwd)/spikes/toolchain/chrome-seccomp.json"
readonly seccomp_profile
created=false
clamav_created=false
network_created=false
template_directory="$(mktemp -d)"

cleanup() {
  if [[ "$created" == true ]]; then
    podman rm --force "$container_name" >/dev/null 2>&1 || true
  fi
  if [[ "$clamav_created" == true ]]; then
    podman rm --force "$clamav_name" >/dev/null 2>&1 || true
  fi
  if [[ "$network_created" == true ]]; then
    podman network rm "$network_name" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$template_directory"
}
trap cleanup EXIT

for name in "$container_name" "$clamav_name"; do
  if podman container exists "$name"; then
    echo "Refusing to replace pre-existing container $name." >&2
    exit 1
  fi
done
if podman network exists "$network_name"; then
  echo "Refusing to replace pre-existing network $network_name." >&2
  exit 1
fi
podman network create "$network_name" >/dev/null
network_created=true
podman run --detach --name "$clamav_name" --network "$network_name" \
  --network-alias clamav --read-only --cap-drop=all \
  --security-opt=no-new-privileges --pids-limit=64 --memory=128m \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=8m \
  --volume "$(pwd)/scripts/container/fake-clamav.py:/fake-clamav.py:ro,Z" \
  --entrypoint /opt/md-converter/venv/bin/python \
  "$image" /fake-clamav.py >/dev/null
clamav_created=true

settings=(
  --env MD_CONVERTER_INITIAL_ADMIN_USERNAME=admin
  --env MD_CONVERTER_INITIAL_ADMIN_PASSWORD=t20-test-password
  --env MD_CONVERTER_STORAGE_PROFILE=standalone
  --env MD_CONVERTER_STANDALONE_DATA_DIRECTORY=/data
  --env MD_CONVERTER_CONVERSION_UPLOAD_MAX_BYTES=1000000
  --env MD_CONVERTER_CONVERSION_REQUEST_MAX_BYTES=1100000
  --env MD_CONVERTER_CONVERSION_MAX_DECOMPRESSED_BYTES=2000000
  --env MD_CONVERTER_CONVERSION_MAX_FILES=100
  --env MD_CONVERTER_CONVERSION_MAX_IMAGES=50
  --env MD_CONVERTER_CONVERSION_MAX_DIAGRAMS=20
  --env MD_CONVERTER_CONVERSION_MAX_COMPRESSION_RATIO=200
  --env MD_CONVERTER_CONVERSION_IMAGE_MAX_SOURCE_BYTES=1000000
  --env MD_CONVERTER_CONVERSION_IMAGE_MAX_WIDTH_PIXELS=2000
  --env MD_CONVERTER_CONVERSION_IMAGE_MAX_HEIGHT_PIXELS=2000
  --env MD_CONVERTER_CONVERSION_IMAGE_MAX_PIXELS=4000000
  --env MD_CONVERTER_CONVERSION_IMAGE_MAX_SVG_ELEMENTS=10000
  --env MD_CONVERTER_CONVERSION_IMAGE_MAX_SVG_DEPTH=64
  --env MD_CONVERTER_CONVERSION_MERMAID_MAX_SOURCE_BYTES=100000
  --env MD_CONVERTER_CONVERSION_MERMAID_MAX_TOTAL_SOURCE_BYTES=2000000
  --env MD_CONVERTER_CONVERSION_MERMAID_MAX_OUTPUT_BYTES=1000000
  --env MD_CONVERTER_CONVERSION_MERMAID_MAX_TOTAL_OUTPUT_BYTES=20000000
  --env MD_CONVERTER_CONVERSION_MERMAID_MAX_WIDTH_PIXELS=2000
  --env MD_CONVERTER_CONVERSION_MERMAID_MAX_HEIGHT_PIXELS=2000
  --env MD_CONVERTER_CONVERSION_MERMAID_EXECUTABLE=mmdc
  --env MD_CONVERTER_CONVERSION_CHROMIUM_EXECUTABLE=/usr/bin/google-chrome-stable
  --env MD_CONVERTER_CONVERSION_PDF_CANCELLATION_POLL_SECONDS=0.1
  --env MD_CONVERTER_CONVERSION_PDF_MAX_BYTES=20000000
  --env MD_CONVERTER_CONVERSION_PDF_MAX_DECODED_STREAM_BYTES=20000000
  --env MD_CONVERTER_CONVERSION_PDF_MAX_PAGES=100
  --env MD_CONVERTER_CONVERSION_PDF_MAX_OBJECTS=100000
  --env MD_CONVERTER_CONVERSION_PDF_MAX_OBJECT_DEPTH=100
  --env MD_CONVERTER_CONVERSION_FONT_MANIFEST_PATH=/opt/md-converter/font-manifest.json
  --env MD_CONVERTER_CONVERSION_RETRY_AFTER_SECONDS=1
  --env MD_CONVERTER_READINESS_TIMEOUT_SECONDS=2
  --env MD_CONVERTER_JOB_RESULT_RETENTION_SECONDS=3600
  --env MD_CONVERTER_JOB_ACTIVE_LIMIT_PER_USER=2
  --env MD_CONVERTER_JOB_GLOBAL_QUEUE_CAPACITY=10
  --env MD_CONVERTER_JOB_MAX_DURATION_SECONDS=60
  --env MD_CONVERTER_WORKER_MEMORY_BUDGET_BYTES=805306368
  --env MD_CONVERTER_WORKER_EPHEMERAL_STORAGE_BUDGET_BYTES=268435456
  --env MD_CONVERTER_WORKER_LEASE_SECONDS=30
  --env MD_CONVERTER_WORKER_HEARTBEAT_SECONDS=5
  --env MD_CONVERTER_WORKER_INCOMPLETE_SUBMISSION_SECONDS=60
  --env MD_CONVERTER_WORKER_IDLE_POLL_SECONDS=0.25
  --env MD_CONVERTER_WORKER_ERROR_BACKOFF_SECONDS=1
  --env MD_CONVERTER_WORKER_CLEANUP_INTERVAL_SECONDS=60
  --env MD_CONVERTER_WORKER_CLEANUP_BATCH_SIZE=100
  --env MD_CONVERTER_TEMPLATE_MAX_ARCHIVE_BYTES=1000000
  --env MD_CONVERTER_TEMPLATE_REQUEST_MAX_BYTES=1100000
  --env MD_CONVERTER_TEMPLATE_METADATA_REQUEST_MAX_BYTES=4096
  --env MD_CONVERTER_TEMPLATE_MAX_NAME_CHARACTERS=100
  --env MD_CONVERTER_TEMPLATE_MAX_DESCRIPTION_CHARACTERS=1000
  --env MD_CONVERTER_TEMPLATE_MAX_ENTRIES=2000
  --env MD_CONVERTER_TEMPLATE_MAX_MEMBER_BYTES=1000000
  --env MD_CONVERTER_TEMPLATE_MAX_TOTAL_BYTES=2000000
  --env MD_CONVERTER_TEMPLATE_MAX_COMPRESSION_RATIO=200
  --env MD_CONVERTER_TEMPLATE_MAX_XML_ELEMENTS=250000
  --env MD_CONVERTER_TEMPLATE_MAX_XML_DEPTH=100
  --env MD_CONVERTER_TEMPLATE_MAX_XML_ATTRIBUTES=500000
  --env MD_CONVERTER_TEMPLATE_MAX_DECLARED_FONTS=64
  --env MD_CONVERTER_TEMPLATE_MAX_FONT_NAME_CHARACTERS=128
  --env MD_CONVERTER_TEMPLATE_PANDOC_EXECUTABLE=pandoc
  --env MD_CONVERTER_TEMPLATE_LIBREOFFICE_EXECUTABLE=soffice
  --env MD_CONVERTER_TEMPLATE_ENGINE_TIMEOUT_SECONDS=10
  --env MD_CONVERTER_TEMPLATE_ENGINE_TERMINATION_GRACE_SECONDS=1
  --env MD_CONVERTER_TEMPLATE_PENDING_PUBLICATION_STALE_SECONDS=60
  --env MD_CONVERTER_CLAMAV_HOST=clamav
)

podman run --detach \
  --name "$container_name" \
  --network "$network_name" \
  --user "$runtime_uid:0" \
  --read-only \
  --cap-drop=all \
  --security-opt=no-new-privileges \
  --security-opt="seccomp=$seccomp_profile" \
  --memory=768m \
  --cpus=2 \
  --pids-limit=256 \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777 \
  --tmpfs /work:rw,nosuid,nodev,size=256m,mode=0770 \
  --tmpfs /data:rw,nosuid,nodev,noexec,size=64m,mode=0770 \
  --shm-size=128m \
  --publish 127.0.0.1::8080 \
  "${settings[@]}" \
  "$image" embedded-worker >/dev/null
created=true

port="$(podman port "$container_name" 8080/tcp | sed 's/.*://')"
for _ in $(seq 1 60); do
  if curl --fail --silent --show-error "http://127.0.0.1:$port/health/live" \
      | grep -Fq '"status":"ok"'; then
    break
  fi
  if ! podman container exists "$container_name" || \
     [[ "$(podman inspect "$container_name" --format '{{.State.Running}}')" != true ]]; then
    podman logs "$container_name" >&2
    exit 1
  fi
  sleep 0.25
done
curl --fail --silent --show-error "http://127.0.0.1:$port/health/ready" \
  | grep -Fq '"status":"ready"'
podman exec "$container_name" /opt/md-converter/venv/bin/python -c \
  'from pathlib import Path; Path("/tmp/t20-template.md").write_text("# Template\n", encoding="utf-8")'
podman exec "$container_name" pandoc /tmp/t20-template.md \
  --output=/tmp/t20-template.docx
podman cp "$container_name:/tmp/t20-template.docx" \
  "$template_directory/template.docx"
uv run python -m scripts.container.api_workflow_smoke \
  --base-url "http://127.0.0.1:$port" \
  --template "$template_directory/template.docx"

echo "Final-image standalone conversion workflow smoke passed for $image."
