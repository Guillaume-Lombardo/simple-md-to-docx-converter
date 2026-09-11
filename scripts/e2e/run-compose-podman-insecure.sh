#!/usr/bin/env bash
set -euo pipefail
umask 0077

repository="$(pwd)"
readonly repository
readonly backend_image="ghcr.io/guillaume-lombardo/md-converter:0.6.2@sha256:30c9fa538e7c4eb56b1b1434cd14251ee82b3a3962ad1a072fe5564a118330ae"
readonly frontend_image="ghcr.io/guillaume-lombardo/md-converter-web:0.6.2@sha256:8908a28dea630ef520eb60828d6717c46fdc445c8878e4bacc6b239ccb32c2e7"
readonly suffix="${GITHUB_RUN_ID:-local}-$$-$RANDOM"
readonly project="markweave-insecure-e2e-${suffix,,}"
readonly work_volume="${project}_markweave-work"
readonly data_volume="${project}_markweave-data"
temporary_directory="$(mktemp -d)"
readonly temporary_directory
readonly state_home="$temporary_directory/state"
readonly state_directory="$state_home/markweave-quickstart-simple"
port="$(uv run python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
readonly port
readonly public_endpoint="http://127.0.0.1:$port"
readonly public_host="localhost:$port"
succeeded=false

quickstart_command=(
  env
  "XDG_STATE_HOME=$state_home"
  "MARKWEAVE_SIMPLE_PROJECT=$project"
  "MARKWEAVE_SIMPLE_PORT=$port"
  MARKWEAVE_SIMPLE_RUNTIME=podman
  "$repository/scripts/quickstart-simple.sh"
)

quickstart() {
  "${quickstart_command[@]}" "$@"
}

container_for_service() {
  local service="$1"
  podman container ls --all --quiet \
    --filter "label=com.docker.compose.project=$project" \
    --filter "label=com.docker.compose.service=$service"
}

file_sha256() {
  sha256sum -- "$1" | awk '{print $1}'
}

assert_container_image_digest() {
  local container_id="$1"
  local expected_image="$2"
  local image_id
  image_id="$(podman inspect --format '{{.Image}}' "$container_id")"
  test "$(podman image inspect "$image_id" --format '{{.Digest}}')" = \
    "${expected_image##*@}"
}

assert_no_port_bindings() {
  local bindings="$1"
  case "$bindings" in
    null | '{}') return 0 ;;
    *)
      echo "The router container unexpectedly declares a host-port binding." >&2
      return 1
      ;;
  esac
}

verify_helper_service_stopped() {
  test ! -e "$state_directory/podman-compose.sock"
  if pgrep -f \
    "[p]odman system service --time=0 unix://$state_directory/podman-compose.sock" \
    >/dev/null; then
    echo "The insecure quickstart retained its private Podman API service." >&2
    return 1
  fi
}

cleanup() {
  local exit_code=$?
  if [[ "$succeeded" != true ]]; then
    quickstart down >/dev/null 2>&1 || true
  fi
  for volume in "$work_volume" "$data_volume"; do
    podman volume rm "$volume" >/dev/null 2>&1 || true
  done
  if [[ "$temporary_directory" == /tmp/tmp.* ]]; then
    rm -rf -- "$temporary_directory"
  else
    echo "Preserving unexpected insecure-E2E directory: $temporary_directory" >&2
    exit_code=1
  fi
  exit "$exit_code"
}
trap cleanup EXIT

quickstart up --insecure
verify_helper_service_stopped

rendered_images="$(
  env \
    MARKWEAVE_INITIAL_ADMIN_PASSWORD=compose-e2e-contract-password \
    "MARKWEAVE_PORT=$port" \
    "MARKWEAVE_PUBLIC_ORIGIN=http://localhost:$port" \
    "MARKWEAVE_ROUTER_PUBLIC_HOST=$public_host" \
    MARKWEAVE_INSECURE_EVALUATION_MODE=true \
    MARKWEAVE_WORK_DEVICE=/dev/null \
    docker compose --project-name "$project" --project-directory "$repository" \
      --file "$repository/compose.yaml" \
      --file "$repository/compose.simple.yaml" \
      --file "$repository/compose.podman.yaml" \
      --file "$repository/compose.trusted-upstream.yaml" \
      --file "$repository/compose.podman-trusted-upstream.yaml" \
      --file "$repository/compose.nextjs.yaml" \
      --file "$repository/compose.nextjs-podman.yaml" \
      --file "$repository/compose.nextjs-podman-trusted-upstream.yaml" \
      config --images
)"
test "$(grep -Fxc "$backend_image" <<<"$rendered_images")" = 1
test "$(grep -Fxc "$frontend_image" <<<"$rendered_images")" = 2

application_id="$(container_for_service markweave)"
frontend_id="$(container_for_service frontend)"
router_id="$(container_for_service router)"
test -n "$application_id"
test -n "$frontend_id"
test -n "$router_id"
test -z "$(container_for_service clamav)"

# Prove the running containers use the exact published release pair. Podman may
# canonicalize ImageName by dropping the tag, so compare the stored image digest.
assert_container_image_digest "$application_id" "$backend_image"
assert_container_image_digest "$frontend_id" "$frontend_image"
assert_container_image_digest "$router_id" "$frontend_image"

# slirp4netns forwards the host-loopback publication to the shared namespace
# interface. The router must therefore bind the namespace, while its own local
# readiness probe can continue to use namespace loopback.
test "$(podman port "$application_id" 3100/tcp)" = "127.0.0.1:$port"
assert_no_port_bindings \
  "$(podman inspect --format '{{json .HostConfig.PortBindings}}' "$router_id")"
podman inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$router_id" \
  | grep -Fx 'ROUTER_HOST=0.0.0.0' >/dev/null

# Exercise both browser and direct FastAPI routing through the real host port.
test "$(curl --connect-timeout 2 --max-time 10 --silent \
  --output "$temporary_directory/login.html" --write-out '%{http_code}' \
  --header "Host: $public_host" "$public_endpoint/login")" = 200
grep -Fq '<title>Markweave</title>' "$temporary_directory/login.html"
test "$(curl --connect-timeout 2 --max-time 10 --silent \
  --output /dev/null --write-out '%{http_code}' \
  --header "Host: $public_host" "$public_endpoint/api/v1/session")" = 401
test "$(curl --connect-timeout 2 --max-time 10 --silent \
  --output /dev/null --write-out '%{http_code}' \
  "$public_endpoint/health/ready")" = 421

password_sha256="$(file_sha256 "$state_directory/password.env")"
template_sha256="$(file_sha256 "$state_directory/quickstart-template.docx")"
quickstart down
verify_helper_service_stopped

test -z "$(podman container ls --all --quiet \
  --filter "label=com.docker.compose.project=$project")"
if podman volume inspect "$work_volume" >/dev/null 2>&1; then
  echo "The insecure quickstart retained its disposable work volume." >&2
  exit 1
fi
podman volume inspect "$data_volume" >/dev/null
test "$(file_sha256 "$state_directory/password.env")" = "$password_sha256"
test "$(file_sha256 "$state_directory/quickstart-template.docx")" = "$template_sha256"

succeeded=true
