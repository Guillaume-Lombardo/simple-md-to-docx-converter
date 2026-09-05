ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER 0
RUN ln -sfn /usr/bin/python3.9 /opt/markweave/venv/bin/python \
    && mkdir -p /opt/markweave/venv/lib/python3.14/site-packages/markweave/reversions \
    && printf '%s\n' '# decoy module' \
        > /opt/markweave/venv/lib/python3.14/site-packages/markweave/reversions/attempt_main.py
ENV PYTHONOPTIMIZE=1 \
    PYTHONPATH=/opt/markweave/venv/lib/python3.14/site-packages
USER 1001:0
