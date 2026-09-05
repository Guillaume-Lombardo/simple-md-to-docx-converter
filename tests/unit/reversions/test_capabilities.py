"""Unit coverage for the immutable reverse capabilities snapshot."""

from typing import cast

import pytest

from markweave.reversions.capabilities import (
    ExecutionCapabilities,
    PdfCapabilities,
    ReversionCapabilitiesUnavailableError,
    build_reversion_capabilities,
)
from markweave.reversions.formats import REVERSE_ADMISSION_POLICY
from markweave.reversions.models import ReverseOutputMode

pytestmark = pytest.mark.unit


def test_capabilities_reuse_policy_output_modes_and_honest_execution_contract() -> None:
    capabilities = build_reversion_capabilities(4_194_304)

    assert capabilities.schema_version == 1
    assert capabilities.admission is REVERSE_ADMISSION_POLICY
    assert capabilities.maximum_upload_bytes == 4_194_304
    assert capabilities.result_package_modes == tuple(ReverseOutputMode)
    assert capabilities.pdf == PdfCapabilities()
    assert capabilities.execution == ExecutionCapabilities()


@pytest.mark.parametrize("maximum_upload_bytes", (None, 0, -1, True, 1.5, "4096"))
def test_capabilities_fail_closed_without_a_positive_integer_upload_limit(
    maximum_upload_bytes: object,
) -> None:
    with pytest.raises(ReversionCapabilitiesUnavailableError):
        build_reversion_capabilities(cast("int | None", maximum_upload_bytes))
