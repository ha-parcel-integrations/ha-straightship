"""Tests for the Straightship API client."""
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.straightship.api import (
    StraightshipApiClient,
    StraightshipApiError,
)

from .payloads import (
    ACTIVE_CODE,
    NO_RESULT_PAGE,
    active_html,
    page,
)


def _session_returning(status: int, body: str = "", headers: dict | None = None):
    response = AsyncMock()
    response.status = status
    response.headers = headers or {}
    response.text = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    return session


async def test_get_parcel_returns_the_full_payload():
    html = active_html()
    client = StraightshipApiClient(_session_returning(200, html))

    parcel = await client.async_get_parcel(ACTIVE_CODE)

    assert parcel["tracking_code"] == ACTIVE_CODE
    assert parcel["tracking_id"] == ACTIVE_CODE
    assert parcel["package_status"] == "Delivered"
    assert len(parcel["history"]) == 4
    # raw is the parsed record; the surrounding page is not part of it
    assert "html" not in parcel


async def test_get_parcel_uppercases_and_encodes_the_code():
    session = _session_returning(200, page("STRTD00A/1"))
    client = StraightshipApiClient(session)

    parcel = await client.async_get_parcel("strtd00a/1")

    url = session.get.call_args[0][0]
    assert url == (
        "https://straightship.com/track-your-shipment/tracking-id/STRTD00A%2F1/"
    )
    assert parcel["tracking_code"] == "STRTD00A/1"


async def test_echoed_id_is_compared_case_insensitively():
    client = StraightshipApiClient(_session_returning(200, page(ACTIVE_CODE.lower())))
    parcel = await client.async_get_parcel(ACTIVE_CODE)
    assert parcel["tracking_code"] == ACTIVE_CODE


async def test_mismatching_echoed_id_is_an_error():
    client = StraightshipApiClient(_session_returning(200, page("STRTD0000000000099")))
    with pytest.raises(StraightshipApiError) as err:
        await client.async_get_parcel(ACTIVE_CODE)
    assert ACTIVE_CODE not in str(err.value)


async def test_no_results_is_none():
    """An unknown code is a normal, non-retryable state."""
    client = StraightshipApiClient(_session_returning(200, NO_RESULT_PAGE))
    assert await client.async_get_parcel(ACTIVE_CODE) is None


async def test_page_without_summary_is_a_changed_template_error():
    client = StraightshipApiClient(_session_returning(200, "<html><p>Welcome</p></html>"))
    with pytest.raises(StraightshipApiError) as err:
        await client.async_get_parcel(ACTIVE_CODE)
    assert "unexpected page" in str(err.value)


async def test_page_without_history_is_a_changed_template_error():
    html = page().split("<h5>History</h5>")[0] + "</div></div></body></html>"
    client = StraightshipApiClient(_session_returning(200, html))
    with pytest.raises(StraightshipApiError):
        await client.async_get_parcel(ACTIVE_CODE)


async def test_raises_on_error_status():
    client = StraightshipApiClient(_session_returning(503))
    with pytest.raises(StraightshipApiError) as err:
        await client.async_get_parcel(ACTIVE_CODE)
    assert err.value.status_code == 503


async def test_429_carries_retry_after_seconds():
    client = StraightshipApiClient(_session_returning(429, headers={"Retry-After": "120"}))
    with pytest.raises(StraightshipApiError) as err:
        await client.async_get_parcel(ACTIVE_CODE)
    assert err.value.status_code == 429
    assert err.value.retry_after == 120.0


@pytest.mark.parametrize("header", [{}, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}])
async def test_429_without_usable_retry_after(header):
    client = StraightshipApiClient(_session_returning(429, headers=header))
    with pytest.raises(StraightshipApiError) as err:
        await client.async_get_parcel(ACTIVE_CODE)
    assert err.value.retry_after is None


async def test_propagates_network_error():
    """ClientError is left alone — DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = StraightshipApiClient(session)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcel(ACTIVE_CODE)
