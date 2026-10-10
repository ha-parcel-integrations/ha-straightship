"""Straightship public tracking client.

The contract the coordinator relies on:

* ``async_get_parcel`` returns the raw per-parcel dict on success,
* returns ``None`` when the carrier says the tracking code is unknown (a
  normal, expected state — never an error),
* raises :class:`StraightshipApiError` for anything else, with
  ``status_code`` set on a non-2xx response and ``retry_after`` set when the
  carrier's own ``Retry-After`` header on a 429 could be parsed as seconds,
* lets ``aiohttp.ClientError`` propagate untouched — ``DataUpdateCoordinator``
  already wraps those into ``UpdateFailed``.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import aiohttp

from .const import TRACKING_API_URL
from .parser import StraightshipParseError, parse_page


class StraightshipApiError(Exception):
    """Raised when a Straightship call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"Straightship request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class StraightshipApiClient:
    """Client for the public Straightship result page.

    No authentication: the page is keyed on the tracking code alone. An unknown
    code is still HTTP 200, with a "No results found!" body.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking page.

        Returns the raw parcel dict for a known parcel, or ``None`` when the
        carrier reports the code as unknown. A changed page or a mismatching
        tracking ID raises :class:`StraightshipApiError`; network errors
        propagate as ``aiohttp.ClientError``.
        """
        # Lookup ignores case but the page echoes what it was given, so
        # normalising up front keeps the echoed ID comparable.
        code = tracking_code.upper()
        url = TRACKING_API_URL.format(tracking_code=quote(code, safe=""))
        async with self._session.get(url) as response:
            if response.status == 429:
                retry_after_header = response.headers.get("Retry-After")
                try:
                    retry_after = (
                        float(retry_after_header) if retry_after_header else None
                    )
                except ValueError:
                    retry_after = None  # an HTTP-date, not seconds
                raise StraightshipApiError(
                    "HTTP 429", status_code=429, retry_after=retry_after
                )
            if response.status != 200:
                raise StraightshipApiError(
                    f"HTTP {response.status}", status_code=response.status
                )
            html = await response.text()

        try:
            page = parse_page(html)
        except StraightshipParseError as err:
            raise StraightshipApiError(f"unexpected page ({err})") from err
        if page is None:
            return None
        if page["tracking_id"].upper() != code:
            raise StraightshipApiError("tracking ID does not match the request")
        return {"tracking_code": code, **page}
