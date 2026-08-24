#!/usr/bin/env bash
set -euo pipefail

readonly image="${1:-localhost/md-converter:t20}"
readonly network_name=md-converter-t20-distributed-smoke
readonly postgres_name=md-converter-t20-postgres-smoke
readonly rustfs_name=md-converter-t20-rustfs-smoke
readonly application_name=md-converter-t20-distributed-api-smoke
readonly worker_name=md-converter-t20-distributed-worker-smoke
readonly clamav_name=md-converter-t20-distributed-clamav-smoke
readonly runtime_uid="${T20_RUNTIME_UID:-50000}"
seccomp_profile="$(pwd)/spikes/toolchain/chrome-seccomp.json"
readonly seccomp_profile
created=()
template_directory="$(mktemp -d)"

cleanup() {
  local resource
  for resource in "${created[@]}"; do
    if [[ "$resource" == network:* ]]; then
      podman network rm "${resource#network:}" >/dev/null 2>&1 || true
    else
      podman rm --force "$resource" >/dev/null 2>&1 || true
    fi
  done
  rm -rf -- "$template_directory"
}
trap cleanup EXIT

for name in "$postgres_name" "$rustfs_name" "$application_name" "$worker_name" "$clamav_name"; do
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
created+=("network:$network_name")
podman run --detach --name "$clamav_name" --network "$network_name" \
  --network-alias clamav --read-only --cap-drop=all \
  --security-opt=no-new-privileges --pids-limit=64 --memory=128m \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=8m \
  --volume "$(pwd)/scripts/container/fake-clamav.py:/fake-clamav.py:ro,Z" \
  --entrypoint /opt/md-converter/venv/bin/python \
  "$image" /fake-clamav.py >/dev/null
created=("$clamav_name" "${created[@]}")
podman run --detach --name "$postgres_name" --network "$network_name" \
  --network-alias postgres \
  --env POSTGRES_DB=md_converter_test \
  --env POSTGRES_PASSWORD=t20-ci-password \
  docker.io/library/postgres:18-alpine@sha256:63bdc97d67b5133bf0e5ebd500bec6d046fa851dc81340d838f0347e616107e8 \
  >/dev/null
created=("$postgres_name" "${created[@]}")
podman run --detach --name "$rustfs_name" --network "$network_name" \
  --network-alias rustfs \
  --publish 127.0.0.1::9000 \
  --env RUSTFS_ACCESS_KEY=t20integration \
  --env RUSTFS_SECRET_KEY=t20integrationsecret \
  --env RUSTFS_ADDRESS=0.0.0.0:9000 \
  --env RUSTFS_CONSOLE_ENABLE=false \
  ghcr.io/rustfs/rustfs:1.0.0-beta.12-glibc@sha256:6d693c8d0c09a1c5770f1780303a5d58b9e864c313fd2644ecd561e92b79ae04 \
  >/dev/null
created=("$rustfs_name" "${created[@]}")

for _ in $(seq 1 60); do
  if podman exec "$postgres_name" pg_isready -U postgres -d md_converter_test \
      >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
rustfs_port="$(podman port "$rustfs_name" 9000/tcp | sed 's/.*://')"
for _ in $(seq 1 60); do
  if curl --fail --silent "http://127.0.0.1:$rustfs_port/health" >/dev/null; then
    break
  fi
  sleep 0.25
done
MD_CONVERTER_TEST_S3_ACCESS_KEY_ID=t20integration \
MD_CONVERTER_TEST_S3_BUCKET=md-converter-t20 \
MD_CONVERTER_TEST_S3_ENDPOINT_URL="http://127.0.0.1:$rustfs_port" \
MD_CONVERTER_TEST_S3_REGION=us-east-1 \
MD_CONVERTER_TEST_S3_SECRET_ACCESS_KEY=t20integrationsecret \
  uv run python -m scripts.ci.prepare_s3_test_bucket

settings=(
  --env MD_CONVERTER_INITIAL_ADMIN_USERNAME=admin
  --env MD_CONVERTER_INITIAL_ADMIN_PASSWORD=t20-test-password
  --env MD_CONVERTER_STORAGE_PROFILE=distributed
  --env MD_CONVERTER_DISTRIBUTED_DATABASE_URL=postgresql+psycopg://postgres:t20-ci-password@postgres:5432/md_converter_test
  --env MD_CONVERTER_S3_BUCKET=md-converter-t20
  --env MD_CONVERTER_S3_ENDPOINT_URL=http://rustfs:9000
  --env MD_CONVERTER_S3_REGION=us-east-1
  --env MD_CONVERTER_S3_ACCESS_KEY_ID=t20integration
  --env MD_CONVERTER_S3_SECRET_ACCESS_KEY=t20integrationsecret
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
  --env MD_CONVERTER_WORKER_METRICS_BIND_HOST=0.0.0.0
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

podman run --detach --name "$application_name" --network "$network_name" \
  --user "$runtime_uid:0" --read-only --cap-drop=all \
  --security-opt=no-new-privileges --security-opt="seccomp=$seccomp_profile" \
  --memory=768m --cpus=2 --pids-limit=256 \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777 \
  --tmpfs /work:rw,nosuid,nodev,size=256m,mode=0770 \
  --shm-size=128m --publish 127.0.0.1::8080 \
  "${settings[@]}" "$image" api >/dev/null
created=("$application_name" "${created[@]}")
podman run --detach --name "$worker_name" --network "$network_name" \
  --user "$runtime_uid:0" --read-only --cap-drop=all \
  --security-opt=no-new-privileges --security-opt="seccomp=$seccomp_profile" \
  --memory=768m --cpus=2 --pids-limit=256 \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777 \
  --tmpfs /work:rw,nosuid,nodev,size=256m,mode=0770 \
  --shm-size=128m \
  "${settings[@]}" "$image" external-worker >/dev/null
created=("$worker_name" "${created[@]}")
application_port="$(podman port "$application_name" 8080/tcp | sed 's/.*://')"
for _ in $(seq 1 80); do
  if curl --fail --silent "http://127.0.0.1:$application_port/health/ready" \
      | grep -Fq '"status":"ready"'; then
    break
  fi
  if [[ "$(podman inspect "$application_name" --format '{{.State.Running}}')" != true ]] || \
     [[ "$(podman inspect "$worker_name" --format '{{.State.Running}}')" != true ]]; then
    podman logs "$application_name" >&2
    podman logs "$worker_name" >&2
    exit 1
  fi
  sleep 0.25
done
curl --fail --silent "http://127.0.0.1:$application_port/health/ready" \
  | grep -Fq '"status":"ready"'
podman exec "$application_name" /opt/md-converter/venv/bin/python -c \
  'from pathlib import Path; Path("/tmp/t20-template.md").write_text("# Template\n", encoding="utf-8")'
podman exec "$application_name" pandoc /tmp/t20-template.md \
  --output=/tmp/t20-template.docx
podman cp "$application_name:/tmp/t20-template.docx" \
  "$template_directory/template.docx"
uv run python -m scripts.container.api_workflow_smoke \
  --base-url "http://127.0.0.1:$application_port" \
  --template "$template_directory/template.docx"
podman exec "$worker_name" /opt/md-converter/venv/bin/python -c \
  'from urllib.request import urlopen; print(urlopen("http://127.0.0.1:9464/metrics", timeout=2).read().decode())' \
  | grep -Fq 'md_converter_'
podman stop --time 15 "$worker_name" >/dev/null
test "$(podman inspect "$worker_name" --format '{{.State.ExitCode}}')" = 0
echo "Final-image distributed conversion workflow and external-worker smoke passed for $image."
