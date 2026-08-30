#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ( "$1" != standalone && "$1" != distributed ) ]]; then
  echo "Usage: scripts/e2e/run.sh {standalone|distributed}" >&2
  exit 2
fi

readonly profile="$1"
readonly repository="$(pwd)"
readonly image="localhost/md-converter:t21-$profile"
readonly base_digest=sha256:194df4e35e0e5467e1b57266f4d61f821e1b1f567135f074d23066d3604ae653
readonly base_image="registry.access.redhat.com/ubi9/python-314@$base_digest"
readonly prefix="md-converter-t21-$profile"
readonly network_name="$prefix"
readonly application_name="$prefix-api"
readonly insecure_application_name="$prefix-insecure-api"
readonly clamav_name="$prefix-clamav"
readonly postgres_name="$prefix-postgres"
readonly rustfs_name="$prefix-rustfs"
readonly worker_one_name="$prefix-worker-1"
readonly worker_two_name="$prefix-worker-2"
readonly runtime_uid="${T21_RUNTIME_UID:-51000}"
readonly artifact_directory="$repository/artifacts/e2e/$profile"
readonly seccomp_profile="$repository/spikes/toolchain/chrome-seccomp.json"

temporary_directory="$(mktemp -d)"
data_directory="$temporary_directory/data"
evidence_directory="$temporary_directory/evidence"
state_file="$temporary_directory/state.json"
recovery_state_file="$temporary_directory/recovery-state.json"
clamav_script="$temporary_directory/fake-clamav.py"
browser_runtime_directory="$temporary_directory/e2e"
node_runtime_directory="$temporary_directory/node_modules"
browser_session_directory="$temporary_directory/browser-session"
provisioning_file="$temporary_directory/users.csv"
provisioned_username="e2e-provisioned-$profile"
provisioned_initial_password="Provisioned-$profile-initial"
provisioned_renewed_password="Provisioned-$profile-browser-renewed"
provisioned_replacement_password="Provisioned-$profile-replacement"
created=()
succeeded=false

# shellcheck source=scripts/e2e/runtime-settings.sh
source "$repository/scripts/e2e/runtime-settings.sh"

remove_artifacts() {
  if [[ "$artifact_directory" != "$repository/artifacts/e2e/$profile" ]]; then
    echo "Refusing to remove an unexpected artifact path." >&2
    return 1
  fi
  rm -rf -- "$artifact_directory"
}

collect_failure_artifacts() {
  local resource
  mkdir -p -- "$artifact_directory"
  for resource in "${created[@]}"; do
    if [[ "$resource" == network:* || "$resource" == volume:* ]]; then
      continue
    fi
    if podman container exists "$resource"; then
      podman logs "$resource" >"$artifact_directory/$resource.log" 2>&1 || true
      podman inspect "$resource" \
        --format 'name={{.Name}} state={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}}' \
        >"$artifact_directory/$resource.state" 2>&1 || true
    fi
  done
  if [[ -d "$temporary_directory/browser-artifacts" ]]; then
    podman unshare chown -R 0:0 -- "$temporary_directory/browser-artifacts" \
      || true
    cp -a -- "$temporary_directory/browser-artifacts/." "$artifact_directory/" \
      || true
  fi
  printf 'profile=%s\nresult=failed\n' "$profile" >"$artifact_directory/summary.txt"
}

cleanup() {
  local exit_code=$?
  local resource
  if [[ "$succeeded" != true ]]; then
    collect_failure_artifacts
  fi
  for resource in "${created[@]}"; do
    if [[ "$resource" == network:* ]]; then
      podman network rm "${resource#network:}" >/dev/null 2>&1 || true
    elif [[ "$resource" == volume:* ]]; then
      podman volume rm "${resource#volume:}" >/dev/null 2>&1 || true
    else
      podman rm --force "$resource" >/dev/null 2>&1 || true
    fi
  done
  if [[ "$temporary_directory" == /tmp/tmp.* ]]; then
    podman unshare rm -rf -- "$temporary_directory" >/dev/null 2>&1 || \
      rm -rf -- "$temporary_directory"
  else
    echo "Refusing to remove unexpected temporary directory $temporary_directory." >&2
  fi
  if [[ "$succeeded" == true ]]; then
    remove_artifacts
  fi
  exit "$exit_code"
}
trap cleanup EXIT

refuse_existing_resources() {
  local name
  for name in "$application_name" "$clamav_name" "$postgres_name" "$rustfs_name" \
    "$insecure_application_name" "$worker_one_name" "$worker_two_name"; do
    if podman container exists "$name"; then
      echo "Refusing to replace pre-existing container $name." >&2
      exit 1
    fi
  done
  if podman network exists "$network_name"; then
    echo "Refusing to replace pre-existing network $network_name." >&2
    exit 1
  fi
}

wait_for_url() {
  local url="$1"
  local container="$2"
  local expected="$3"
  local attempt
  for attempt in $(seq 1 120); do
    if [[ -z "$expected" ]]; then
      if curl --fail --silent --show-error "$url" >/dev/null 2>&1; then
        return 0
      fi
    elif curl --fail --silent --show-error "$url" 2>/dev/null | grep -Fq "$expected"; then
      return 0
    fi
    if [[ "$(podman inspect "$container" --format '{{.State.Running}}' 2>/dev/null)" != true ]]; then
      podman logs "$container" >&2 || true
      return 1
    fi
    sleep 0.25
  done
  echo "Timed out waiting for $url." >&2
  return 1
}

require_http_status() {
  local url="$1"
  local expected="$2"
  local actual
  actual="$(curl --silent --output /dev/null --write-out '%{http_code}' "$url")"
  if [[ "$actual" != "$expected" ]]; then
    echo "HTTP $actual from $url, expected $expected." >&2
    return 1
  fi
}

hardened_runtime=(
  --user "$runtime_uid:0"
  --read-only
  --cap-drop=all
  --security-opt=no-new-privileges
  --security-opt="seccomp=$seccomp_profile"
  --memory=768m
  --cpus=2
  --pids-limit=256
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777
  --tmpfs /work:rw,nosuid,nodev,size=256m,mode=0770
  --shm-size=128m
)

remove_artifacts
mkdir -p -- "$data_directory" "$evidence_directory" \
  "$temporary_directory/browser-artifacts" "$browser_session_directory"
chmod 0770 "$data_directory"
chmod 0777 "$evidence_directory" "$temporary_directory/browser-artifacts" \
  "$browser_session_directory"
printf '%s\n%s,%s,user,true,true\n' \
  'username,password,role,active,password_change_required' \
  "$provisioned_username" "$provisioned_initial_password" >"$provisioning_file"
chmod 0444 "$provisioning_file"
install -m 0444 "$repository/scripts/container/fake-clamav.py" "$clamav_script"
cp -a "$repository/tests/e2e" "$browser_runtime_directory"
PUPPETEER_SKIP_DOWNLOAD=true npm ci --ignore-scripts
cp -a "$repository/node_modules" "$node_runtime_directory"
chmod -R a+rX "$browser_runtime_directory" "$node_runtime_directory"
refuse_existing_resources

test "$(podman info --format '{{.Host.Security.Rootless}}')" = true
podman pull --quiet "$base_image"
test "$(podman image inspect "$base_image" --format '{{.Digest}}')" = "$base_digest"
bash scripts/container/build.sh "$image"

podman network create "$network_name" >/dev/null
created+=("network:$network_name")

created=("$clamav_name" "${created[@]}")
podman run --detach --name "$clamav_name" --network "$network_name" \
  --network-alias e2e-clamav --read-only --cap-drop=all \
  --security-opt=no-new-privileges --pids-limit=64 --memory=128m \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=8m \
  --volume "$clamav_script:/fake-clamav.py:ro,Z" \
  --entrypoint /opt/md-converter/venv/bin/python \
  "$image" /fake-clamav.py >/dev/null

e2e_runtime_settings
if [[ "$profile" == standalone ]]; then
  E2E_SETTINGS+=(
    --env MARKWEAVE_STORAGE_PROFILE=standalone
    --env MARKWEAVE_STANDALONE_DATA_DIRECTORY=/data
    --env MARKWEAVE_PUBLIC_ORIGIN=http://127.0.0.1:8080
  )
else
  created=("$postgres_name" "${created[@]}")
  podman run --detach --name "$postgres_name" --network "$network_name" \
    --network-alias postgres --env POSTGRES_DB=md_converter_e2e \
    --env POSTGRES_PASSWORD=e2e-postgres-password \
    docker.io/library/postgres:18-alpine@sha256:63bdc97d67b5133bf0e5ebd500bec6d046fa851dc81340d838f0347e616107e8 \
    >/dev/null
  created=("$rustfs_name" "${created[@]}")
  podman run --detach --name "$rustfs_name" --network "$network_name" \
    --network-alias rustfs --publish 127.0.0.1::9000 \
    --env RUSTFS_ACCESS_KEY=e2eaccess --env RUSTFS_SECRET_KEY=e2esecret \
    --env RUSTFS_ADDRESS=0.0.0.0:9000 --env RUSTFS_CONSOLE_ENABLE=false \
    ghcr.io/rustfs/rustfs:1.0.0-beta.12-glibc@sha256:6d693c8d0c09a1c5770f1780303a5d58b9e864c313fd2644ecd561e92b79ae04 \
    >/dev/null
  for _ in $(seq 1 120); do
    podman exec "$postgres_name" pg_isready -U postgres -d md_converter_e2e \
      >/dev/null 2>&1 && break
    sleep 0.25
  done
  rustfs_port="$(podman port "$rustfs_name" 9000/tcp | sed 's/.*://')"
  wait_for_url "http://127.0.0.1:$rustfs_port/health" "$rustfs_name" ""
  MARKWEAVE_TEST_S3_ACCESS_KEY_ID=e2eaccess \
  MARKWEAVE_TEST_S3_BUCKET=md-converter-t21 \
  MARKWEAVE_TEST_S3_ENDPOINT_URL="http://127.0.0.1:$rustfs_port" \
  MARKWEAVE_TEST_S3_REGION=us-east-1 \
  MARKWEAVE_TEST_S3_SECRET_ACCESS_KEY=e2esecret \
    uv run python -m scripts.ci.prepare_s3_test_bucket
  E2E_SETTINGS+=(
    --env MARKWEAVE_STORAGE_PROFILE=distributed
    --env MARKWEAVE_DISTRIBUTED_DATABASE_URL=postgresql+psycopg://postgres:e2e-postgres-password@postgres:5432/md_converter_e2e
    --env MARKWEAVE_S3_BUCKET=md-converter-t21
    --env MARKWEAVE_S3_ENDPOINT_URL=http://rustfs:9000
    --env MARKWEAVE_S3_REGION=us-east-1
    --env MARKWEAVE_S3_ACCESS_KEY_ID=e2eaccess
    --env MARKWEAVE_S3_SECRET_ACCESS_KEY=e2esecret
  )
fi

application_volumes=(
  --volume "$browser_runtime_directory:/e2e:ro,Z"
  --volume "$node_runtime_directory:/node_modules:ro,Z"
  --volume "$evidence_directory:/evidence:rw,Z"
  --volume "$temporary_directory/browser-artifacts:/browser-artifacts:rw,Z"
  --volume "$browser_session_directory:/browser-session:rw,Z"
  --volume "$provisioning_file:/run/secrets/users.csv:ro,Z"
)
if [[ "$profile" == standalone ]]; then
  application_volumes+=(--volume "$data_directory:/data:rw,Z")
fi

application_mode=api
if [[ "$profile" == standalone ]]; then
  application_mode=embedded-worker
fi
application_settings=(
  "${E2E_SETTINGS[@]}"
  --env MARKWEAVE_USER_PROVISIONING_FILE=/run/secrets/users.csv
)
created=("$application_name" "${created[@]}")
podman run --detach --name "$application_name" --network "$network_name" \
  --network-alias application --publish 127.0.0.1::8080 \
  "${hardened_runtime[@]}" "${application_volumes[@]}" "${application_settings[@]}" \
  "$image" "$application_mode" >/dev/null

if [[ "$profile" == distributed ]]; then
  for worker in "$worker_one_name" "$worker_two_name"; do
    created=("$worker" "${created[@]}")
    podman run --detach --name "$worker" --network "$network_name" \
      --publish 127.0.0.1::9464 "${hardened_runtime[@]}" "${E2E_SETTINGS[@]}" \
      "$image" external-worker >/dev/null
  done
fi

application_port="$(podman port "$application_name" 8080/tcp | sed 's/.*://')"
base_url="http://127.0.0.1:$application_port"
wait_for_url "$base_url/health/ready" "$application_name" '"status":"ready"'

if [[ "$profile" == standalone ]]; then
  podman exec "$application_name" /opt/md-converter/venv/bin/python -c '
import http.client
import json

payload = json.dumps({"username": "e2e-admin", "password": "e2e-admin-password"})

def login(origin):
    connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=10)
    connection.request(
        "POST",
        "/api/v1/login",
        body=payload,
        headers={
            "Content-Type": "application/json",
            "Origin": origin,
            "Forwarded": "host=attacker.example;proto=https",
            "X-Forwarded-Host": "attacker.example",
            "X-Forwarded-Proto": "https",
        },
    )
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response.status, body

accepted_status, _ = login("http://127.0.0.1:8080")
hostile_status, hostile_body = login("https://attacker.example")
assert accepted_status == 200
assert hostile_status == 403
assert json.loads(hostile_body)["error"]["code"] == "LOGIN_ORIGIN_INVALID"
'
fi

podman exec "$application_name" /opt/md-converter/venv/bin/python -c \
  'from pathlib import Path; Path("/tmp/e2e-template.md").write_text("# Template\n", encoding="utf-8")'
podman exec "$application_name" pandoc /tmp/e2e-template.md --output=/tmp/e2e-template.docx
podman cp "$application_name:/tmp/e2e-template.docx" "$evidence_directory/template.docx"
cp -- "$evidence_directory/template.docx" "$evidence_directory/browser-template.docx"
uv run python -c \
  'import sys; from pathlib import Path; from scripts.container.api_workflow_smoke import candidate_reference; path = Path(sys.argv[1]); path.write_bytes(candidate_reference(path.read_bytes()))' \
  "$evidence_directory/browser-template.docx"
printf '# Final image E2E\n\nReal **conversion** workflow.\n' >"$evidence_directory/source.md"
chmod 0444 "$evidence_directory/template.docx" \
  "$evidence_directory/browser-template.docx" "$evidence_directory/source.md"

uv run python -m tests.e2e.service_workflow exercise-security-boundaries \
  --base-url "$base_url" --profile "$profile" \
  --template "$evidence_directory/template.docx" \
  --artifact-dir "$temporary_directory/browser-artifacts"

worker_metrics=()
if [[ "$profile" == distributed ]]; then
  worker_metrics+=(--worker-metrics-url "http://127.0.0.1:$(podman port "$worker_one_name" 9464/tcp | sed 's/.*://')/metrics")
  worker_metrics+=(--worker-metrics-url "http://127.0.0.1:$(podman port "$worker_two_name" 9464/tcp | sed 's/.*://')/metrics")
fi

uv run python -m tests.e2e.service_workflow exercise \
  --base-url "$base_url" --profile "$profile" \
  --template "$evidence_directory/template.docx" --state-file "$state_file" \
  --artifact-dir "$temporary_directory/browser-artifacts" \
  --api-metrics-url "$base_url/metrics" "${worker_metrics[@]}"

uv run python -m tests.e2e.cli_workflow --container "$application_name" --profile "$profile"

uv run python -m tests.e2e.conversion_cli_workflow \
  --container "$application_name" --profile "$profile"

podman exec \
  --env MARKWEAVE_E2E_BASE_URL=http://127.0.0.1:8080 \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_TEMPLATE_FIXTURE=/evidence/browser-template.docx \
  --env MARKWEAVE_E2E_SOURCE_FIXTURE=/evidence/source.md \
  --env MARKWEAVE_E2E_ARTIFACT_DIR=/browser-artifacts \
  --env MARKWEAVE_E2E_ADMIN_USERNAME=e2e-admin \
  --env MARKWEAVE_E2E_ADMIN_PASSWORD=e2e-admin-password \
  --env MARKWEAVE_E2E_PROVISIONED_USERNAME="$provisioned_username" \
  --env MARKWEAVE_E2E_PROVISIONED_PASSWORD="$provisioned_initial_password" \
  --env MARKWEAVE_E2E_PROVISIONED_RENEWED_PASSWORD="$provisioned_renewed_password" \
  "$application_name" node --test /e2e/browser-final-image.test.mjs

uv run python -m tests.e2e.template_cli_workflow \
  --container "$application_name" --profile "$profile"

chmod 0644 "$provisioning_file"
printf '%s\n%s,%s,user,true,true\n' \
  'username,password,role,active,password_change_required' \
  "$provisioned_username" "$provisioned_replacement_password" >"$provisioning_file"
chmod 0444 "$provisioning_file"
podman restart --time 15 "$application_name" >/dev/null
wait_for_url "$base_url/health/ready" "$application_name" '"status":"ready"'
podman exec \
  --env MARKWEAVE_E2E_BASE_URL=http://127.0.0.1:8080 \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_PROVISIONED_USERNAME="$provisioned_username" \
  --env MARKWEAVE_E2E_PROVISIONED_OLD_PASSWORD="$provisioned_renewed_password" \
  --env MARKWEAVE_E2E_PROVISIONED_PASSWORD="$provisioned_replacement_password" \
  "$application_name" node --test /e2e/browser-provisioning-restart.test.mjs

uv run python -m tests.e2e.service_workflow submit-recovery \
  --base-url "$base_url" --profile "$profile" --output both \
  --template "$evidence_directory/template.docx" \
  --state-file "$recovery_state_file" \
  --artifact-dir "$temporary_directory/browser-artifacts"
podman exec \
  --env MARKWEAVE_E2E_BASE_URL=http://127.0.0.1:8080 \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_RECOVERY_STATE=/browser-session/admin.json \
  --env MARKWEAVE_E2E_ARTIFACT_DIR=/browser-artifacts \
  --env MARKWEAVE_E2E_ADMIN_USERNAME=e2e-admin \
  --env MARKWEAVE_E2E_ADMIN_PASSWORD=e2e-admin-password \
  "$application_name" node --test /e2e/browser-recovery-checkpoint.test.mjs
if [[ "$profile" == standalone ]]; then
  podman kill --signal KILL "$application_name" >/dev/null
  test "$(podman inspect "$application_name" --format '{{.State.ExitCode}}')" = 137
  podman start "$application_name" >/dev/null
else
  podman kill --signal KILL "$application_name" "$worker_one_name" \
    "$worker_two_name" >/dev/null
  test "$(podman inspect "$application_name" --format '{{.State.ExitCode}}')" = 137
  test "$(podman inspect "$worker_one_name" --format '{{.State.ExitCode}}')" = 137
  test "$(podman inspect "$worker_two_name" --format '{{.State.ExitCode}}')" = 137
  podman start "$application_name" "$worker_one_name" "$worker_two_name" >/dev/null
fi
wait_for_url "$base_url/health/ready" "$application_name" '"status":"ready"'
uv run python -m tests.e2e.service_workflow verify-recovery \
  --base-url "$base_url" --profile "$profile" \
  --state-file "$recovery_state_file" \
  --artifact-dir "$temporary_directory/browser-artifacts"

podman exec \
  --env MARKWEAVE_E2E_BASE_URL=http://127.0.0.1:8080 \
  --env MARKWEAVE_E2E_PROFILE="$profile" \
  --env MARKWEAVE_E2E_RECOVERY_STATE=/browser-session/admin.json \
  --env MARKWEAVE_E2E_ARTIFACT_DIR=/browser-artifacts \
  "$application_name" node --test /e2e/browser-recovery.test.mjs

require_http_status "$base_url/health/live" 200
if [[ "$profile" == standalone ]]; then
  chmod 000 "$data_directory"
  require_http_status "$base_url/health/ready" 503
  require_http_status "$base_url/health/live" 200
  chmod 0770 "$data_directory"
else
  podman stop --time 10 "$rustfs_name" >/dev/null
  require_http_status "$base_url/health/ready" 503
  require_http_status "$base_url/health/live" 200
  podman start "$rustfs_name" >/dev/null
  wait_for_url "http://127.0.0.1:$rustfs_port/health" "$rustfs_name" ""
  wait_for_url "$base_url/health/ready" "$application_name" '"status":"ready"'
  podman stop --time 10 "$postgres_name" >/dev/null
  require_http_status "$base_url/health/ready" 503
  require_http_status "$base_url/health/live" 200
  podman start "$postgres_name" >/dev/null
  for _ in $(seq 1 120); do
    podman exec "$postgres_name" pg_isready -U postgres -d md_converter_e2e \
      >/dev/null 2>&1 && break
    sleep 0.25
  done
fi
wait_for_url "$base_url/health/ready" "$application_name" '"status":"ready"'

uv run python -m tests.e2e.service_workflow checkpoint \
  --base-url "$base_url" --profile "$profile" \
  --template "$evidence_directory/template.docx" --state-file "$state_file" \
  --artifact-dir "$temporary_directory/browser-artifacts"

podman restart --time 15 "$application_name" >/dev/null
wait_for_url "$base_url/health/ready" "$application_name" '"status":"ready"'
uv run python -m tests.e2e.service_workflow verify-checkpoint \
  --base-url "$base_url" --profile "$profile" \
  --template "$evidence_directory/template.docx" --state-file "$state_file" \
  --artifact-dir "$temporary_directory/browser-artifacts"

# Prove that an isolated snapshot restores the durable identities, jobs,
# results, templates, and merged audit history. All snapshot bytes remain in
# the private temporary directory and are never retained as failure artifacts.
podman stop --time 15 "$application_name" >/dev/null
if [[ "$profile" == distributed ]]; then
  podman stop --time 15 "$worker_one_name" "$worker_two_name" >/dev/null
  podman exec "$postgres_name" pg_dump --username postgres --dbname md_converter_e2e \
    --format=custom --file=/tmp/md-converter-e2e.dump
  podman cp "$postgres_name:/tmp/md-converter-e2e.dump" \
    "$temporary_directory/postgres.dump"
  uv run python -m scripts.e2e.s3_backup backup \
    --endpoint-url "http://127.0.0.1:$rustfs_port" --region us-east-1 \
    --access-key-id e2eaccess --secret-access-key e2esecret \
    --bucket md-converter-t21 --directory "$temporary_directory/s3-backup"
  podman exec "$postgres_name" dropdb --username postgres md_converter_e2e
  podman exec "$postgres_name" createdb --username postgres md_converter_e2e
  podman cp "$temporary_directory/postgres.dump" \
    "$postgres_name:/tmp/md-converter-e2e.dump"
  podman exec "$postgres_name" pg_restore --username postgres \
    --dbname md_converter_e2e --exit-on-error /tmp/md-converter-e2e.dump
  uv run python -m scripts.e2e.s3_backup restore \
    --endpoint-url "http://127.0.0.1:$rustfs_port" --region us-east-1 \
    --access-key-id e2eaccess --secret-access-key e2esecret \
    --bucket md-converter-t21 --directory "$temporary_directory/s3-backup"
  podman start "$worker_one_name" "$worker_two_name" >/dev/null
else
  mkdir -m 0700 "$temporary_directory/standalone-backup"
  podman unshare cp -a -- "$data_directory/." \
    "$temporary_directory/standalone-backup/"
  podman unshare find "$data_directory" -mindepth 1 -delete
  podman unshare cp -a -- "$temporary_directory/standalone-backup/." \
    "$data_directory/"
fi
podman start "$application_name" >/dev/null
wait_for_url "$base_url/health/ready" "$application_name" '"status":"ready"'
uv run python -m tests.e2e.service_workflow verify-checkpoint \
  --base-url "$base_url" --profile "$profile" \
  --template "$evidence_directory/template.docx" --state-file "$state_file" \
  --artifact-dir "$temporary_directory/browser-artifacts"

# Prove the final image's explicit insecure exception without a scanner. The
# published port remains loopback-only even though login origins are ignored.
podman rm --force "$application_name" "$clamav_name" >/dev/null
created=("$insecure_application_name" "${created[@]}")
podman run --detach --name "$insecure_application_name" --network "$network_name" \
  --network-alias application --publish 127.0.0.1::8080 \
  --env MARKWEAVE_INSECURE_EVALUATION_MODE=true \
  "${hardened_runtime[@]}" "${application_volumes[@]}" "${application_settings[@]}" \
  "$image" "$application_mode" >/dev/null
insecure_application_port="$(podman port "$insecure_application_name" 8080/tcp | sed 's/.*://')"
insecure_base_url="http://127.0.0.1:$insecure_application_port"
test "$(podman port "$insecure_application_name" 8080/tcp)" = \
  "127.0.0.1:$insecure_application_port"
wait_for_url "$insecure_base_url/health/ready" "$insecure_application_name" \
  '"status":"ready"'
uv run python -m tests.e2e.service_workflow verify-disabled-login-origin \
  --base-url "$insecure_base_url" --profile "$profile" \
  --artifact-dir "$temporary_directory/browser-artifacts"
podman logs "$insecure_application_name" 2>&1 | \
  grep '"event":"insecure_evaluation_mode_enabled"' >/dev/null

succeeded=true
echo "Final-image $profile E2E workflow passed for $image."
