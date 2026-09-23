"""Public Composer connection contracts keep credentials write-only."""

import pytest
from pydantic import ValidationError

from markweave.http.composer_schemas import (
    ComposerModelStepCreateRequest,
    CredentialWriteRequest,
)


@pytest.mark.unit
def test_credential_request_rejects_ambiguous_actions_without_disclosing_values() -> (
    None
):
    secret = "private-" + "material"
    request = CredentialWriteRequest(api_key=secret)
    assert secret not in repr(request)
    assert secret not in str(request)

    with pytest.raises(ValidationError) as ambiguous:
        CredentialWriteRequest(api_key=secret, revoke=True)
    assert secret not in str(ambiguous.value)

    with pytest.raises(ValidationError):
        CredentialWriteRequest()


@pytest.mark.unit
def test_model_step_request_does_not_echo_approved_text_on_validation_error() -> None:
    sensitive = "private-" + "document-excerpt"
    with pytest.raises(ValidationError) as invalid:
        ComposerModelStepCreateRequest.model_validate(
            {
                "connection_id": "invalid",
                "approved_endpoint": "https://llm.example/v1",
                "approved_model": "small",
                "content": sensitive,
                "max_output_tokens": 1,
            }
        )
    assert sensitive not in str(invalid.value)
