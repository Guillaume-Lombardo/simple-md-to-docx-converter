"""Exercise parent egress and child no-IP isolation in the final rootless image."""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import textwrap
import zipfile

from markweave.conversion.engine_launcher import isolated_engine_command


def main() -> int:
    with socket.create_connection(("e2e-llm", 8443), timeout=3):
        pass
    parent_namespace = os.readlink("/proc/self/ns/net")
    engine_environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/"),
    }
    child = textwrap.dedent(
        """
        import errno
        import json
        import os
        import socket
        import subprocess
        import sys

        for family in (socket.AF_INET, socket.AF_INET6, socket.AF_NETLINK):
            try:
                socket.socket(family, socket.SOCK_STREAM)
            except OSError as error:
                assert error.errno == errno.EPERM, (family, error.errno)
            else:
                raise AssertionError(f"Engine opened network family {family}")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM):
            pass
        descendant = subprocess.run(
            [sys.executable, "-c", "import errno, socket;\\ntry: socket.socket(socket.AF_INET, socket.SOCK_STREAM)\\nexcept OSError as error: assert error.errno == errno.EPERM\\nelse: raise AssertionError('descendant opened IP socket')"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert descendant.returncode == 0, descendant.stderr
        print(json.dumps({"uid": os.geteuid(), "netns": os.readlink('/proc/self/ns/net')}))
        """
    )
    probe = subprocess.run(
        isolated_engine_command([sys.executable, "-c", child]),
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
        close_fds=True,
        env=engine_environment,
    )
    result = json.loads(probe.stdout)
    assert result["uid"] == os.geteuid()
    assert result["netns"] != parent_namespace

    pandoc = subprocess.run(
        isolated_engine_command(
            ["pandoc", "--from=markdown", "--to=docx", "--output=-"]
        ),
        input=b"# Final image isolation proof\n",
        check=True,
        capture_output=True,
        timeout=20,
        close_fds=True,
        env=engine_environment,
    )
    with zipfile.ZipFile(io.BytesIO(pandoc.stdout)) as archive:
        assert "word/document.xml" in archive.namelist()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
