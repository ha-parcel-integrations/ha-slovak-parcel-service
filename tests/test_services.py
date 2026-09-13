"""Tests for the Slovak Parcel Service services (track_parcel / untrack_parcel)."""
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.slovak_parcel_service.api import SlovakParcelServiceApiClient
from custom_components.slovak_parcel_service.const import (
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DOMAIN,
)
from custom_components.slovak_parcel_service.services import async_setup_services

from .payloads import active_sample

_SAMPLE = active_sample()


def _mock_api():
    """Patch both API calls the coordinator makes per parcel.

    The shipment-info call defaults to ``None`` (no enrichment) — these tests
    exercise track/untrack bookkeeping, not sender/weight/receiver.
    """
    return patch.multiple(
        SlovakParcelServiceApiClient,
        async_get_parcel=AsyncMock(return_value=_SAMPLE),
        async_get_shipment_info=AsyncMock(return_value=None),
    )


async def _setup(
    hass, parcels: list[dict] | None = None, postal_code: str = "84105"
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=postal_code,
        options={CONF_PARCELS: parcels or [], CONF_POSTAL_CODE: postal_code},
    )
    entry.add_to_hass(hass)
    with _mock_api():
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_track_parcel_adds_to_options(hass):
    entry = await _setup(hass)
    with _mock_api():
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: "EXAMPLE999999"},
            blocking=True,
        )
        await hass.async_block_till_done()

    parcels = entry.options[CONF_PARCELS]
    assert parcels == [{CONF_TRACKING_CODE: "EXAMPLE999999"}]


async def test_track_parcel_normalizes_code(hass):
    entry = await _setup(hass)
    with _mock_api():
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: "example-999 999"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert entry.options[CONF_PARCELS] == [
        {CONF_TRACKING_CODE: "EXAMPLE999999"}
    ]


async def test_track_parcel_accepts_any_non_empty_code(hass):
    """A short/odd-shaped code is accepted — formats vary too much to gate on."""
    entry = await _setup(hass)
    with _mock_api():
        await hass.services.async_call(
            DOMAIN, "track_parcel", {CONF_TRACKING_CODE: "abc"}, blocking=True
        )
        await hass.async_block_till_done()

    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: "ABC"}]


async def test_track_parcel_rejects_empty_code(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "track_parcel", {CONF_TRACKING_CODE: ""}, blocking=True
        )


async def test_track_parcel_duplicate_is_noop(hass):
    entry = await _setup(hass)
    with _mock_api():
        for _ in range(2):
            await hass.services.async_call(
                DOMAIN,
                "track_parcel",
                {CONF_TRACKING_CODE: "EXAMPLE999999"},
                blocking=True,
            )
            await hass.async_block_till_done()

    assert len(entry.options[CONF_PARCELS]) == 1


async def test_untrack_parcel_removes_from_options(hass):
    entry = await _setup(
        hass, parcels=[{CONF_TRACKING_CODE: "EXAMPLE999999"}]
    )
    with _mock_api():
        await hass.services.async_call(
            DOMAIN,
            "untrack_parcel",
            {CONF_TRACKING_CODE: "EXAMPLE999999"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert entry.options[CONF_PARCELS] == []


async def test_untrack_unknown_code_is_noop(hass):
    entry = await _setup(
        hass, parcels=[{CONF_TRACKING_CODE: "EXAMPLE999999"}]
    )
    with _mock_api():
        await hass.services.async_call(
            DOMAIN,
            "untrack_parcel",
            {CONF_TRACKING_CODE: "EXAMPLE000000"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert len(entry.options[CONF_PARCELS]) == 1


async def test_track_parcel_routes_to_hub_by_postal_code(hass):
    """With two hubs set up, the postal_code field picks the target one."""
    home = await _setup(hass, postal_code="84105")
    work = await _setup(hass, postal_code="04001")

    with _mock_api():
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: "EXAMPLE999999", CONF_POSTAL_CODE: "04001"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert home.options[CONF_PARCELS] == []
    assert work.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: "EXAMPLE999999"}]


async def test_track_parcel_ambiguous_without_postal_code(hass):
    """Two hubs and no postal_code argument must raise, not guess."""
    await _setup(hass, postal_code="84105")
    await _setup(hass, postal_code="04001")

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: "EXAMPLE999999"},
            blocking=True,
        )


async def test_track_parcel_without_a_hub_raises(hass):
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: "EXAMPLE999999"},
            blocking=True,
        )


async def test_track_parcel_unknown_postal_code_raises(hass):
    await _setup(hass, postal_code="84105")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: "EXAMPLE999999", CONF_POSTAL_CODE: "99999"},
            blocking=True,
        )


async def test_untrack_parcel_without_a_hub_raises(hass):
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "untrack_parcel",
            {CONF_TRACKING_CODE: "EXAMPLE999999"},
            blocking=True,
        )


async def test_untrack_parcel_removes_from_whichever_hub_tracks_it(hass):
    """untrack_parcel needs no postal_code — it searches every hub."""
    home = await _setup(
        hass, parcels=[{CONF_TRACKING_CODE: "EXAMPLE999999"}], postal_code="84105"
    )
    work = await _setup(
        hass, parcels=[{CONF_TRACKING_CODE: "EXAMPLE888888"}], postal_code="04001"
    )

    with _mock_api():
        await hass.services.async_call(
            DOMAIN,
            "untrack_parcel",
            {CONF_TRACKING_CODE: "EXAMPLE888888"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert home.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: "EXAMPLE999999"}]
    assert work.options[CONF_PARCELS] == []
