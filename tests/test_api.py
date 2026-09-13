"""Tests for the MySPS JSON transport."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.slovak_parcel_service.api import (
    SlovakParcelServiceApiClient,
    SlovakParcelServiceApiError,
)

from .payloads import delivered_sample

CODE = "SK1234567890"


def _session_returning(status: int, body: object = None, headers: dict | None = None) -> MagicMock:
    response = AsyncMock(status=status, headers=headers or {})
    if isinstance(body, str):
        response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
    else:
        response.json = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post = MagicMock(return_value=ctx)
    return session


async def test_get_parcel_posts_contract_without_exposing_key():
    session = _session_returning(200, {"status": True, "code": 200, "data": delivered_sample(CODE)})
    parcel = await SlovakParcelServiceApiClient(session).async_get_parcel(CODE)
    assert parcel["shipmentNr"] == CODE
    _, kwargs = session.post.call_args
    assert kwargs["json"] == {"shipmentNr": CODE}
    assert kwargs["headers"].keys() == {"Api-Key"}
    assert len(kwargs["headers"]["Api-Key"]) > 20


@pytest.mark.parametrize("body", [{"status": False, "code": 404, "data": None}, {"status": True, "data": None}])
async def test_get_parcel_returns_none_for_semantic_no_result(body):
    assert await SlovakParcelServiceApiClient(_session_returning(200, body)).async_get_parcel(CODE) is None


async def test_get_parcel_handles_rate_limit():
    with pytest.raises(SlovakParcelServiceApiError) as err:
        await SlovakParcelServiceApiClient(_session_returning(429, {}, {"Retry-After": "12"})).async_get_parcel(CODE)
    assert err.value.status_code == 429 and err.value.retry_after == 12


async def test_get_parcel_warns_on_key_rotation(caplog):
    with pytest.raises(SlovakParcelServiceApiError):
        await SlovakParcelServiceApiClient(_session_returning(401, {})).async_get_parcel(CODE)
    assert "may have rotated" in caplog.text


async def test_get_parcel_falls_back_to_own_backoff_on_unparseable_retry_after():
    session = _session_returning(429, {}, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    with pytest.raises(SlovakParcelServiceApiError) as err:
        await SlovakParcelServiceApiClient(session).async_get_parcel(CODE)
    assert err.value.status_code == 429 and err.value.retry_after is None


async def test_get_parcel_warns_on_semantic_401_in_200_envelope(caplog):
    # status isn't False and data isn't None, so this reaches the malformed-envelope
    # path rather than the semantic no-result path — the code:401 branch fires first.
    body = {"status": True, "code": 401, "message": "Unauthorized", "data": {}}
    with pytest.raises(SlovakParcelServiceApiError):
        await SlovakParcelServiceApiClient(_session_returning(200, body)).async_get_parcel(CODE)
    assert "may have rotated" in caplog.text


@pytest.mark.parametrize("body", ["not json", ["not", "object"]])
async def test_get_parcel_rejects_invalid_body(body):
    with pytest.raises(SlovakParcelServiceApiError):
        await SlovakParcelServiceApiClient(_session_returning(200, body)).async_get_parcel(CODE)


async def test_get_parcel_propagates_network_error():
    session = MagicMock()
    session.post = MagicMock(side_effect=aiohttp.ClientError("boom"))
    with pytest.raises(aiohttp.ClientError):
        await SlovakParcelServiceApiClient(session).async_get_parcel(CODE)


# ---------------------------------------------------------------------------
# async_get_shipment_info (getShipmentInfo)
# ---------------------------------------------------------------------------

_UNAUTHORIZED_INFO = {
    "shipmentNr": CODE,
    "authorized": False,
    "panels": {
        "panel_0": {
            "fields": {
                "sender": {"name": "sender", "label": "LBL_SENDER", "type": "string", "value": "Example Sender s.r.o."},
                "weight": {"name": "weight", "label": "LBL_WEIGHT", "type": "float", "value": 4.55},
            }
        }
    },
}

_AUTHORIZED_INFO = {
    "shipmentNr": CODE,
    "authorized": True,
    "panels": {
        "panel_0": {
            "fields": {
                "sender": {"name": "sender", "label": "LBL_SENDER", "type": "string", "value": "Example Sender s.r.o."},
                "weight": {"name": "weight", "label": "LBL_WEIGHT", "type": "float", "value": 7.7},
            }
        },
        "panel_1": {
            "fields": {
                "recipient": {"name": "recipient", "label": "LBL_RECIPIENT", "type": "string", "value": "Jana Vzorova"},
            }
        },
    },
    "rawData": [{"senderZIP": "84105", "recipientZIP": "04001"}],
}


async def test_get_shipment_info_sends_recipientzip_header_when_given():
    session = _session_returning(200, {"status": True, "code": 200, "data": _AUTHORIZED_INFO})
    data = await SlovakParcelServiceApiClient(session).async_get_shipment_info(
        CODE, recipient_zip="04001"
    )
    assert data["authorized"] is True
    _, kwargs = session.post.call_args
    assert kwargs["json"] == {"shipmentNr": CODE}
    assert kwargs["headers"]["recipientzip"] == "04001"
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert len(kwargs["headers"]["Api-Key"]) > 20


async def test_get_shipment_info_omits_recipientzip_header_when_not_given():
    session = _session_returning(200, {"status": True, "code": 200, "data": _UNAUTHORIZED_INFO})
    data = await SlovakParcelServiceApiClient(session).async_get_shipment_info(CODE)
    assert data["authorized"] is False
    _, kwargs = session.post.call_args
    assert "recipientzip" not in kwargs["headers"]


async def test_get_shipment_info_returns_unauthorized_shape_without_error():
    """A wrong/absent postcode is not an error — just the unauthorised shape."""
    session = _session_returning(
        200, {"status": True, "code": 200, "data": _UNAUTHORIZED_INFO}
    )
    data = await SlovakParcelServiceApiClient(session).async_get_shipment_info(
        CODE, recipient_zip="00000"
    )
    assert data["authorized"] is False
    assert "panel_1" not in data["panels"]


@pytest.mark.parametrize("body", [{"status": False, "code": 404, "data": None}, {"status": True, "data": None}])
async def test_get_shipment_info_returns_none_for_semantic_no_result(body):
    session = _session_returning(200, body)
    assert await SlovakParcelServiceApiClient(session).async_get_shipment_info(CODE) is None


async def test_get_shipment_info_handles_rate_limit():
    session = _session_returning(429, {}, {"Retry-After": "5"})
    with pytest.raises(SlovakParcelServiceApiError) as err:
        await SlovakParcelServiceApiClient(session).async_get_shipment_info(CODE)
    assert err.value.status_code == 429 and err.value.retry_after == 5


async def test_get_shipment_info_warns_on_key_rotation(caplog):
    session = _session_returning(401, {})
    with pytest.raises(SlovakParcelServiceApiError):
        await SlovakParcelServiceApiClient(session).async_get_shipment_info(CODE)
    assert "may have rotated" in caplog.text


async def test_get_shipment_info_rejects_invalid_body():
    session = _session_returning(200, "not json")
    with pytest.raises(SlovakParcelServiceApiError):
        await SlovakParcelServiceApiClient(session).async_get_shipment_info(CODE)


async def test_get_shipment_info_warns_on_semantic_401_in_200_envelope(caplog):
    body = {"status": True, "code": 401, "message": "Unauthorized", "data": {}}
    with pytest.raises(SlovakParcelServiceApiError):
        await SlovakParcelServiceApiClient(_session_returning(200, body)).async_get_shipment_info(CODE)
    assert "may have rotated" in caplog.text


async def test_get_shipment_info_propagates_network_error():
    session = MagicMock()
    session.post = MagicMock(side_effect=aiohttp.ClientError("boom"))
    with pytest.raises(aiohttp.ClientError):
        await SlovakParcelServiceApiClient(session).async_get_shipment_info(CODE)
