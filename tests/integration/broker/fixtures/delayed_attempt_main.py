"""Hold the real attempt runner behind a content-free integration barrier."""

from importlib import import_module
from pathlib import Path
from time import sleep

_RELEASE = Path("/work/test.release")

if __name__ == "__main__":
    while not _RELEASE.is_file():
        sleep(0.01)
    import_module("markweave.reversions.attempt_worker").main()
