ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER 0
RUN test -d /opt/markweave/venv/lib/python3.14/site-packages/anydoc \
    && mv /opt/markweave/venv/lib/python3.14/site-packages/anydoc \
        /opt/markweave/venv/lib/python3.14/site-packages/anydoc.removed
USER 1001:0
