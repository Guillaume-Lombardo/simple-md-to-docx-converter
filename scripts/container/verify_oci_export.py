"""Verify that an OCI export preserves one resolved local image identity."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from scripts.container.integrity import OCI_DIGEST, IntegrityError, oci_identity

if TYPE_CHECKING:
    from collections.abc import Sequence


def _arguments(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-image-id", required=True)
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    """Fail unless the archive config digest is the resolved local image ID."""

    options = _arguments(arguments)
    if OCI_DIGEST.fullmatch(options.expected_image_id) is None:
        print("error: expected image ID is not a sha256 digest")
        return 1
    try:
        _manifest_digest, config_digest = oci_identity(options.archive)
    except IntegrityError as error:
        print(f"error: {error}")
        return 1
    if config_digest != options.expected_image_id:
        print("error: exported OCI config does not match the resolved image ID")
        return 1
    print(f"verified_oci_config_digest={config_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
