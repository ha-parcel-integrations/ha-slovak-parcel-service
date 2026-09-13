"""Slovak Parcel Service MySPS tracking API client."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import API_KEY, INFO_API_URL, TRACKING_API_URL

_LOGGER = logging.getLogger(__name__)


class SlovakParcelServiceApiError(Exception):
    """Raised when a Slovak Parcel Service API call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"Slovak Parcel Service API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


def _warn_key_rotation() -> None:
    """Log the shared-key-may-have-rotated warning, shared by both endpoints."""
    _LOGGER.warning(
        "Slovak Parcel Service transport authorization failed; the approved "
        "shared key may have rotated. Report this at "
        "https://github.com/ha-parcel-integrations/ha-slovak-parcel-service/issues"
    )


class SlovakParcelServiceApiClient:
    """Client for the public Slovak Parcel Service MySPS JSON endpoints.

    No user authentication: both endpoints are keyed on the shared transport
    key alone. Each answers HTTP 200 with the same JSON envelope::

        {"status": true,  "code": 200, "message": ..., "data": {...}}
        {"status": false, "code": 404, "message": ..., "data": null}
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def _post(
        self, url: str, headers: dict[str, str], tracking_code: str
    ) -> dict[str, Any]:
        """POST the shared ``{"shipmentNr": ...}`` body and return the parsed envelope.

        Handles the 429/401/non-200/malformed-body cases identically for
        every MySPS JSON endpoint. Raises :class:`SlovakParcelServiceApiError`
        on any HTTP or shape failure; network errors propagate as
        ``aiohttp.ClientError``.
        """
        async with self._session.post(
            url,
            json={"shipmentNr": tracking_code},
            headers=headers,
        ) as response:
            if response.status == 429:
                retry_after_header = response.headers.get("Retry-After")
                try:
                    retry_after = float(retry_after_header) if retry_after_header else None
                except ValueError:
                    retry_after = None  # an HTTP-date, not seconds; let the caller's own backoff handle it
                raise SlovakParcelServiceApiError(
                    "HTTP 429", status_code=429, retry_after=retry_after
                )
            if response.status != 200:
                if response.status == 401:
                    _warn_key_rotation()
                raise SlovakParcelServiceApiError(
                    f"HTTP {response.status}", status_code=response.status
                )
            try:
                # content_type=None: consumer endpoints routinely serve JSON as
                # text/plain, and aiohttp would otherwise refuse to parse it.
                payload = await response.json(content_type=None)
            except ValueError as err:
                raise SlovakParcelServiceApiError(f"unparseable body ({err})") from err

        if not isinstance(payload, dict):
            raise SlovakParcelServiceApiError("unexpected body (not a JSON object)")
        return payload

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking details.

        Returns the parcel dict for a known parcel, or ``None`` when the
        endpoint reports the code as unknown — which is also what a
        not-yet-scanned parcel gets. Any other failure envelope or non-2xx
        status raises :class:`SlovakParcelServiceApiError`; network errors
        propagate as ``aiohttp.ClientError``.
        """
        payload = await self._post(
            TRACKING_API_URL, {"Api-Key": API_KEY}, tracking_code
        )

        parcel = payload.get("data")
        if isinstance(parcel, dict) and parcel.get("shipmentNr"):
            return parcel
        # MySPS's exact no-result envelope still needs a redacted capture.  A
        # false status or absent data is the bounded, semantic no-result path.
        if payload.get("status") is False or parcel is None:
            return None
        if payload.get("code") == 401:
            _warn_key_rotation()
        raise SlovakParcelServiceApiError("invalid response envelope")

    async def async_get_shipment_info(
        self, tracking_code: str, *, recipient_zip: str | None = None
    ) -> dict[str, Any] | None:
        """Fetch sender/weight (and, with a matching postcode, receiver) for one parcel.

        Same envelope as :meth:`async_get_parcel`, on
        ``tracking/getShipmentInfo``. Without ``recipient_zip`` — or with one
        that does not match the shipment's actual recipient — the response
        still comes back 200 with ``data.authorized`` set to ``False`` and
        only the sender/weight panel populated; that is not an error, it's
        the endpoint's normal unauthorised shape, so it is returned like any
        other successful call and left to the caller (``parcels.py``'s
        ``extract_shipment_info``) to interpret. Returns the raw ``data``
        dict, or ``None`` when the envelope reports no result at all. Any
        other failure raises :class:`SlovakParcelServiceApiError`; network
        errors propagate as ``aiohttp.ClientError`` — same contract as
        :meth:`async_get_parcel`, so callers can pace/back off identically.
        """
        headers = {"Content-Type": "application/json", "Api-Key": API_KEY}
        if recipient_zip:
            headers["recipientzip"] = recipient_zip
        payload = await self._post(INFO_API_URL, headers, tracking_code)

        data = payload.get("data")
        if isinstance(data, dict) and data.get("shipmentNr"):
            return data
        if payload.get("status") is False or data is None:
            return None
        if payload.get("code") == 401:
            _warn_key_rotation()
        raise SlovakParcelServiceApiError("invalid response envelope")
