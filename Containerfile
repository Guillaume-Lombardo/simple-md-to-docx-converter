ARG BASE_IMAGE=registry.access.redhat.com/ubi9/python-314@sha256:194df4e35e0e5467e1b57266f4d61f821e1b1f567135f074d23066d3604ae653
FROM ${BASE_IMAGE}

USER 0

ARG PANDOC_VERSION=3.10.2
ARG PANDOC_SHA256=c7edd535941c48be6a362081a748272837de81ae11777202d9c341d3d8261c9a
ARG CHROME_VERSION=151.0.7922.173-1
ARG CHROME_SHA256=2899353cad3732b8e3a88e76996c340e047d8729ea1b881fdfdd21e0e3baefa5
ARG LIBREOFFICE_VERSION=26.2.5
ARG LIBREOFFICE_SHA256=f62611c441ff1faa5cadb499abdbab119f5a9013eb6c0e32fc9aa65f6ff8b53d
ARG UV_VERSION=0.12.1
ARG UV_SHA256=90b2f223fb69d19db49e117da601f64978593417988530aa733d456141b4bcbb
ARG GOOGLE_RPM_KEY_SHA256=54dea5f6c2a26091578cf52a999cebc6b64df478d37ad4dce96376b711e3b27c
ARG APPLICATION_VERSION

RUN dnf install -y \
        alsa-lib at-spi2-atk at-spi2-core atk cups-libs curl-minimal findutils \
        fontconfig libXcomposite libXdamage libXfixes libXinerama libXrandr \
        libxkbcommon mesa-libgbm nspr nss pango tar unzip \
    && dnf clean all \
    && rm -rf /var/cache/dnf

COPY --chmod=0555 spikes/toolchain/fonts/install-fonts.sh /usr/local/bin/install-md-converter-fonts
COPY --chmod=0444 spikes/toolchain/fonts/fonts.conf /opt/md-converter/fontconfig/fonts.conf
RUN install-md-converter-fonts \
    && mkdir -p /opt/md-converter/fontconfig/cache \
    && FONTCONFIG_FILE=/opt/md-converter/fontconfig/fonts.conf fc-cache --force \
    && test "$(find /opt/md-converter/fonts -type f -name '*.ttf' | wc -l)" -eq 32 \
    && rm -rf /tmp/md-converter-fonts

RUN curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
        "https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-linux-amd64.tar.gz" \
        --output /tmp/pandoc.tar.gz \
    && echo "${PANDOC_SHA256}  /tmp/pandoc.tar.gz" | sha256sum --check --strict \
    && tar --extract --gzip --file /tmp/pandoc.tar.gz --strip-components=1 --directory /usr/local \
    && rm /tmp/pandoc.tar.gz

RUN curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
        "https://dl.google.com/linux/linux_signing_key.pub" \
        --output /tmp/google-linux-signing-key.pub \
    && echo "${GOOGLE_RPM_KEY_SHA256}  /tmp/google-linux-signing-key.pub" \
        | sha256sum --check --strict \
    && rpm --import /tmp/google-linux-signing-key.pub \
    && rm /tmp/google-linux-signing-key.pub \
    && curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
        "https://dl.google.com/linux/chrome/rpm/stable/x86_64/google-chrome-stable-${CHROME_VERSION}.x86_64.rpm" \
        --output /tmp/google-chrome.rpm \
    && echo "${CHROME_SHA256}  /tmp/google-chrome.rpm" | sha256sum --check --strict \
    && rpm --checksig /tmp/google-chrome.rpm | grep -Fq 'digests signatures OK' \
    && rpm --install --nodeps --noscripts /tmp/google-chrome.rpm \
    && ! ldd /opt/google/chrome/chrome | grep -Fq 'not found' \
    && rm /tmp/google-chrome.rpm

RUN curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
        "https://download.documentfoundation.org/libreoffice/stable/${LIBREOFFICE_VERSION}/rpm/x86_64/LibreOffice_${LIBREOFFICE_VERSION}_Linux_x86-64_rpm.tar.gz" \
        --output /tmp/libreoffice.tar.gz \
    && echo "${LIBREOFFICE_SHA256}  /tmp/libreoffice.tar.gz" | sha256sum --check --strict \
    && mkdir /tmp/libreoffice \
    && tar --extract --gzip --file /tmp/libreoffice.tar.gz --strip-components=1 --directory /tmp/libreoffice \
    && dnf install -y /tmp/libreoffice/RPMS/*.rpm \
    && dnf clean all \
    && rm -rf /var/cache/dnf /tmp/libreoffice /tmp/libreoffice.tar.gz \
    && ln -s "/opt/libreoffice${LIBREOFFICE_VERSION%.*}/program/soffice" /usr/local/bin/soffice \
    && ! ldd "/opt/libreoffice${LIBREOFFICE_VERSION%.*}/program/oosplash" | grep -Fq 'not found'

ENV PUPPETEER_SKIP_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/google-chrome-stable \
    FONTCONFIG_FILE=/opt/md-converter/fontconfig/fonts.conf

WORKDIR /opt/md-converter/node
COPY spikes/toolchain/package.json spikes/toolchain/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts \
    && npm cache clean --force \
    && ln -s /opt/md-converter/node/node_modules/.bin/mmdc /usr/local/bin/mmdc \
    && rm -rf /usr/lib/node_modules/npm \
    && rm -f /usr/bin/npm /usr/bin/npx

RUN curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
        "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" \
        --output /tmp/uv.tar.gz \
    && echo "${UV_SHA256}  /tmp/uv.tar.gz" | sha256sum --check --strict \
    && tar --extract --gzip --file /tmp/uv.tar.gz --strip-components=1 --directory /usr/local/bin \
        uv-x86_64-unknown-linux-gnu/uv uv-x86_64-unknown-linux-gnu/uvx \
    && rm /tmp/uv.tar.gz \
    && test "$(uv --version)" = "uv ${UV_VERSION} (x86_64-unknown-linux-gnu)"

WORKDIR /opt/md-converter/app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
ENV UV_PROJECT_ENVIRONMENT=/opt/md-converter/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
RUN uv sync --locked --no-dev --no-editable \
    && /opt/md-converter/venv/bin/python -c \
        'import md_converter, sys; assert md_converter.__version__ == sys.argv[1]' \
        "${APPLICATION_VERSION}" \
    && rm -rf /root/.cache/uv /opt/md-converter/app/src \
    && rm -f /opt/md-converter/app/pyproject.toml /opt/md-converter/app/uv.lock \
        /opt/md-converter/app/README.md /usr/local/bin/uv /usr/local/bin/uvx

COPY --chmod=0555 container/entrypoint.sh /usr/local/bin/md-converter-entrypoint
COPY --chmod=0555 container/preflight.sh /usr/local/bin/md-converter-preflight
COPY --chmod=0444 spikes/toolchain/chrome-seccomp.json /opt/md-converter/chrome-seccomp.json
COPY --chmod=0444 spikes/toolchain/fonts/manifest.json /opt/md-converter/font-manifest.json
COPY --chmod=0444 spikes/toolchain/THIRD_PARTY_NOTICES.md /opt/md-converter/THIRD_PARTY_NOTICES.md
COPY --chmod=0444 spikes/toolchain/LICENSE.containers-common /opt/md-converter/LICENSE.chrome-seccomp

ARG RPM_INVENTORY_SHA256=7d6f97daffef4581775cefd422aa7ba355d7fb8c705ae499c7846a81c4ffc1ee
RUN mkdir -p /data /work /tmp/md-converter \
    && chgrp -R 0 /data /work /tmp/md-converter \
    && chmod -R g=u /data /work /tmp/md-converter \
    && rpm -qa --qf '%{NAME}|%{EPOCHNUM}:%{VERSION}-%{RELEASE}|%{ARCH}|%{LICENSE}\n' \
        | LC_ALL=C sort > /opt/md-converter/rpm-inventory.txt \
    && sha256sum /opt/md-converter/rpm-inventory.txt \
    && echo "${RPM_INVENTORY_SHA256}  /opt/md-converter/rpm-inventory.txt" \
        | sha256sum --check --strict \
    && rm -f /usr/bin/curl /usr/bin/openssl /usr/sbin/httpd \
    && find /opt/md-converter -xdev -type d -exec chmod g=u {} + \
    && find /opt/md-converter -xdev -type f -exec chmod g=u {} +

ENV PATH=/opt/md-converter/venv/bin:/usr/local/bin:/usr/bin \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/work/home \
    TMPDIR=/work/tmp \
    XDG_CACHE_HOME=/work/xdg/cache \
    XDG_CONFIG_HOME=/work/xdg/config \
    XDG_DATA_HOME=/work/xdg/data \
    XDG_RUNTIME_DIR=/work/xdg/runtime \
    MD_CONVERTER_HOST=0.0.0.0 \
    MD_CONVERTER_PORT=8080

ARG BASE_IMAGE
LABEL org.opencontainers.image.title="Markdown to DOCX and PDF Converter" \
      org.opencontainers.image.version="${APPLICATION_VERSION}" \
      org.opencontainers.image.source="https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter" \
      org.opencontainers.image.base.name="${BASE_IMAGE}"

WORKDIR /work
USER 1001:0
EXPOSE 8080
ENTRYPOINT ["md-converter-entrypoint"]
CMD ["api"]
