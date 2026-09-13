"""Tests for Slovak Parcel Service setup and unload."""
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.slovak_parcel_service.api import (
    SlovakParcelServiceApiClient,
    SlovakParcelServiceApiError,
)
from custom_components.slovak_parcel_service.const import (
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    DOMAIN,
)

from .payloads import ACTIVE_CODE
from .payloads import active_sample as _sample

OTHER_CODE = "SK2222222222"


def _no_info_patch():
    """Shipment-info enrichment defaults to unavailable — these tests exercise
    setup/unload/entity lifecycle, not sender/weight/receiver."""
    return patch.object(
        SlovakParcelServiceApiClient,
        "async_get_shipment_info",
        new=AsyncMock(return_value=None),
    )


async def test_setup_and_unload(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="84105",
        options={CONF_PARCELS: [{CONF_TRACKING_CODE: ACTIVE_CODE}]},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.slovak_parcel_service.api.SlovakParcelServiceApiClient.async_get_parcel",
        new=AsyncMock(return_value=_sample()),
    ), _no_info_patch():
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED

    # The active parcel produced a per-parcel sensor and the summary sensor.
    incoming = hass.states.get("sensor.slovak_parcel_service_incoming_parcels")
    assert incoming is not None
    assert incoming.state == "1"

    # Services registered on setup...
    assert hass.services.has_service(DOMAIN, "track_parcel")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED

    # ...and removed once the last hub unloads.
    assert not hass.services.has_service(DOMAIN, "track_parcel")


async def test_services_survive_unload_while_another_hub_stays_loaded(hass):
    """Unloading one hub must not break the services for a remaining hub."""
    home = MockConfigEntry(
        domain=DOMAIN,
        unique_id="84105",
        options={CONF_PARCELS: []},
    )
    work = MockConfigEntry(
        domain=DOMAIN,
        unique_id="04001",
        options={CONF_PARCELS: []},
    )
    home.add_to_hass(hass)

    with patch(
        "custom_components.slovak_parcel_service.api.SlovakParcelServiceApiClient.async_get_parcel",
        new=AsyncMock(return_value=None),
    ), _no_info_patch():
        # Added and set up one at a time — adding both before the first
        # setup would let HA's own first-ever-setup-of-this-domain bulk
        # path load both together, which is not what this test means to
        # exercise.
        assert await hass.config_entries.async_setup(home.entry_id)
        await hass.async_block_till_done()
        work.add_to_hass(hass)
        assert await hass.config_entries.async_setup(work.entry_id)
        await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, "track_parcel")

    assert await hass.config_entries.async_unload(home.entry_id)
    await hass.async_block_till_done()

    # The work hub is still loaded, so the shared services must stay.
    assert work.state is ConfigEntryState.LOADED
    assert hass.services.has_service(DOMAIN, "track_parcel")

    assert await hass.config_entries.async_unload(work.entry_id)
    await hass.async_block_till_done()

    # Now both hubs are gone — the services go too.
    assert not hass.services.has_service(DOMAIN, "track_parcel")


async def test_setup_retries_when_first_refresh_fails(hass):
    """When the first data fetch fails, setup retries from the entry itself.

    The first refresh runs in __init__.py before platforms are forwarded, so a
    failure raises ConfigEntryNotReady from the entry setup (SETUP_RETRY) rather
    than — too late — from a forwarded platform.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="84105",
        options={CONF_PARCELS: [{CONF_TRACKING_CODE: ACTIVE_CODE}]},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.slovak_parcel_service.api.SlovakParcelServiceApiClient.async_get_parcel",
        new=AsyncMock(side_effect=SlovakParcelServiceApiError("Slovak Parcel Service unreachable")),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_per_parcel_sensor_spawn_and_remove(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="84105",
        options={CONF_PARCELS: [{CONF_TRACKING_CODE: ACTIVE_CODE}]},
    )
    entry.add_to_hass(hass)

    mock = AsyncMock(return_value=_sample())
    with patch(
        "custom_components.slovak_parcel_service.api.SlovakParcelServiceApiClient.async_get_parcel",
        new=mock,
    ), _no_info_patch():
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        registry = er.async_get(hass)
        assert registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_{ACTIVE_CODE}"
        )

        # The next poll returns a different tracking code: the summary sensor
        # spawns a new per-parcel sensor and removes the stale one.
        mock.return_value = _sample(OTHER_CODE)
        await entry.runtime_data.coordinator.async_request_refresh()
        await hass.async_block_till_done()

        assert registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_{OTHER_CODE}"
        )
        assert (
            registry.async_get_entity_id(
                "sensor", DOMAIN, f"{entry.entry_id}_{ACTIVE_CODE}"
            )
            is None
        )


async def test_two_hubs_get_distinct_devices_and_entities(hass):
    """Two hubs for different postcodes must not collide in the registries."""
    home = MockConfigEntry(
        domain=DOMAIN,
        unique_id="84105",
        options={CONF_PARCELS: [{CONF_TRACKING_CODE: ACTIVE_CODE}]},
    )
    work = MockConfigEntry(
        domain=DOMAIN,
        unique_id="04001",
        options={CONF_PARCELS: [{CONF_TRACKING_CODE: OTHER_CODE}]},
    )
    home.add_to_hass(hass)

    with patch(
        "custom_components.slovak_parcel_service.api.SlovakParcelServiceApiClient.async_get_parcel",
        new=AsyncMock(side_effect=lambda code: _sample(code)),
    ), _no_info_patch():
        # Added and set up one at a time — see the comment in
        # test_services_survive_unload_while_another_hub_stays_loaded.
        assert await hass.config_entries.async_setup(home.entry_id)
        await hass.async_block_till_done()
        work.add_to_hass(hass)
        assert await hass.config_entries.async_setup(work.entry_id)
        await hass.async_block_till_done()

    assert home.state is ConfigEntryState.LOADED
    assert work.state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    home_entity = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{home.entry_id}_{ACTIVE_CODE}"
    )
    work_entity = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{work.entry_id}_{OTHER_CODE}"
    )
    assert home_entity is not None
    assert work_entity is not None
    assert home_entity != work_entity
    assert registry.entities[home_entity].device_id != registry.entities[work_entity].device_id


async def test_options_update_applies_live_without_reload(hass):
    """Adding a parcel via options refreshes the coordinator immediately."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="84105",
        options={CONF_PARCELS: [{CONF_TRACKING_CODE: ACTIVE_CODE}]},
    )
    entry.add_to_hass(hass)

    mock = AsyncMock(return_value=_sample())
    with patch(
        "custom_components.slovak_parcel_service.api.SlovakParcelServiceApiClient.async_get_parcel",
        new=mock,
    ), _no_info_patch():
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        mock.side_effect = lambda code: _sample(code)
        hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                CONF_PARCELS: [
                    {CONF_TRACKING_CODE: ACTIVE_CODE},
                    {CONF_TRACKING_CODE: OTHER_CODE},
                ],
            },
        )
        await hass.async_block_till_done()

    incoming = hass.states.get("sensor.slovak_parcel_service_incoming_parcels")
    assert incoming.state == "2"
