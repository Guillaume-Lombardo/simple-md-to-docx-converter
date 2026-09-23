"""Composer uploads are bounded before request parsing or persistence."""

import asyncio

import pytest
from pytest_mock import MockerFixture

from markweave.http.middleware import BoundedRequestBody


@pytest.mark.unit
def test_unconfigured_composer_rejects_mutation_without_reading_body(
    mocker: MockerFixture,
) -> None:
    downstream = mocker.AsyncMock()
    receive = mocker.AsyncMock(side_effect=AssertionError("body must not be read"))
    send = mocker.AsyncMock()
    middleware = BoundedRequestBody(
        downstream,
        conversion_maximum_bytes=2000,
        reversion_maximum_bytes=None,
        template_maximum_bytes=2000,
        template_metadata_maximum_bytes=1000,
    )

    asyncio.run(
        middleware(
            {"type": "http", "method": "POST", "path": "/api/v1/composer/drafts"},
            receive,
            send,
        )
    )

    downstream.assert_not_called()
    receive.assert_not_called()
    assert send.await_args_list[0].args[0]["status"] == 503


@pytest.mark.unit
def test_composer_mutation_exceeding_limit_never_reaches_parser(
    mocker: MockerFixture,
) -> None:
    downstream = mocker.AsyncMock()
    receive = mocker.AsyncMock(
        return_value={"type": "http.request", "body": b"x" * 17, "more_body": False}
    )
    send = mocker.AsyncMock()
    middleware = BoundedRequestBody(
        downstream,
        conversion_maximum_bytes=2000,
        reversion_maximum_bytes=None,
        template_maximum_bytes=2000,
        template_metadata_maximum_bytes=1000,
        composer_maximum_bytes=16,
    )

    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "PUT",
                "path": "/api/v1/composer/connections/1/credentials",
            },
            receive,
            send,
        )
    )

    downstream.assert_not_called()
    assert send.await_args_list[0].args[0]["status"] == 413
