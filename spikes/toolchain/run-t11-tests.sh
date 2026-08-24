#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPOSITORY="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
readonly IMAGE="${TOOLCHAIN_IMAGE:-localhost/simple-md-toolchain:t11}"
readonly RUNTIME_UID="${TOOLCHAIN_UID:-1000710000}"
readonly UV_BIN="$(command -v uv)"
readonly UV_VERSION="uv 0.12.1 (x86_64-unknown-linux-gnu)"
readonly UV_SHA256="92face6b1f0462ad911857957bd168cd4ae45515e2a2cb3fcc3ecbda3d4d82b1"
readonly RUN_ID="${UID}-$$"
readonly SOURCE_VOLUME="md-converter-t11-source-${RUN_ID}"
readonly DEPS_VOLUME="md-converter-t11-deps-${RUN_ID}"

cleanup() {
    for volume in "${SOURCE_VOLUME}" "${DEPS_VOLUME}"; do
        podman volume rm --force "${volume}" >/dev/null 2>&1 || true
    done
}
trap cleanup EXIT INT TERM

test "$("${UV_BIN}" --version)" = "${UV_VERSION}"
printf '%s  %s\n' "${UV_SHA256}" "${UV_BIN}" | sha256sum --check --strict >/dev/null
podman image exists "${IMAGE}"
podman volume create "${SOURCE_VOLUME}" >/dev/null
podman volume create "${DEPS_VOLUME}" >/dev/null

podman run --rm \
    --userns=keep-id \
    --user "$(id -u):$(id -g)" \
    --cap-drop=all \
    --security-opt=no-new-privileges \
    --mount "type=bind,source=${REPOSITORY},target=/input,ro=true" \
    --mount "type=volume,source=${SOURCE_VOLUME},target=/src,U=true" \
    --entrypoint /usr/bin/cp \
    "${IMAGE}" -a /input/. /src/

podman run --rm \
    --userns=keep-id \
    --user "$(id -u):$(id -g)" \
    --cap-drop=all \
    --security-opt=no-new-privileges \
    --mount "type=volume,source=${SOURCE_VOLUME},target=/src" \
    --entrypoint /usr/bin/chmod \
    "${IMAGE}" -R a=rX /src

podman run --rm \
    --read-only-tmpfs=false \
    --user "${RUNTIME_UID}:0" \
    --uidmap 0:0:1 \
    --uidmap "${RUNTIME_UID}:1:1" \
    --gidmap 0:0:1 \
    --read-only \
    --cap-drop=all \
    --security-opt=no-new-privileges \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=512m,mode=1777 \
    --mount "type=volume,source=${SOURCE_VOLUME},target=/src,ro=true" \
    --mount "type=bind,source=${UV_BIN},target=/usr/local/bin/uv,ro=true" \
    --mount "type=volume,source=${DEPS_VOLUME},target=/deps,U=true" \
    --env HOME=/tmp/home \
    --env UV_CACHE_DIR=/tmp/uv-cache \
    --env UV_PROJECT_ENVIRONMENT=/deps \
    --entrypoint /usr/local/bin/uv \
    "${IMAGE}" sync --project /src --all-groups --locked --link-mode=copy

podman run --rm \
    --read-only-tmpfs=false \
    --user "${RUNTIME_UID}:0" \
    --uidmap 0:0:1 \
    --uidmap "${RUNTIME_UID}:1:1" \
    --gidmap 0:0:1 \
    --read-only \
    --network none \
    --cap-drop=all \
    --security-opt=no-new-privileges \
    --memory 2g \
    --cpus 2 \
    --pids-limit 512 \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=512m,mode=1777 \
    --tmpfs /dev/shm:rw,nosuid,nodev,noexec,size=128m,mode=1777 \
    --tmpfs /work:rw,nosuid,nodev,exec,size=512m,mode=1777 \
    --mount "type=volume,source=${SOURCE_VOLUME},target=/src,ro=true" \
    --mount "type=volume,source=${DEPS_VOLUME},target=/deps,ro=true" \
    --workdir /src \
    --env ENGINE_FIXTURE_ROOT=/work \
    --env PYTHONDONTWRITEBYTECODE=1 \
    --entrypoint /deps/bin/python \
    "${IMAGE}" -m pytest \
        tests/integration/document_engines/test_libreoffice_pdf.py \
        tests/integration/document_engines/test_pdf_rasterizer.py \
        -q --no-cov -p no:cacheprovider
