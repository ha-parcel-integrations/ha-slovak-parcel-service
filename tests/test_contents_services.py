"""Tests for the contents action, the track_parcel field and the store lifecycle."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.slovak_parcel_service.const import (
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DOMAIN,
)
from custom_components.slovak_parcel_service.coordinator import (
    SlovakParcelServiceCoordinator,
)
from custom_components.slovak_parcel_service.parcels import normalize_parcel

from .payloads import ACTIVE_CODE, active_sample, delivered_sample

CLIENT = "custom_components.slovak_parcel_service.api.SlovakParcelServiceApiClient"
REFRESH = f"{SlovakParcelServiceCoordinator.__module__}.SlovakParcelServiceCoordinator"
UNTRACKED = "EXAMPLE555555"
STORE_KEY = f"{DOMAIN}.contents.%s"


@pytest.fixture(autouse=True)
def _no_shipment_info():
    """The shipment-info call adds nothing these tests look at."""
    with patch(f"{CLIENT}.async_get_shipment_info", new=AsyncMock(return_value=None)):
        yield


def _config_entry(parcels: list[dict]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_28012",
        options={CONF_PARCELS: parcels, CONF_POSTAL_CODE: "28012"},
    )


async def _setup(hass, codes: list[str] | None = None) -> MockConfigEntry:
    entry = _config_entry([{CONF_TRACKING_CODE: code} for code in codes or []])
    entry.add_to_hass(hass)
    with patch(
        f"{CLIENT}.async_get_parcel", new=AsyncMock(return_value=active_sample())
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _call(hass, service: str, data: dict) -> None:
    with patch(
        f"{CLIENT}.async_get_parcel",
        new=AsyncMock(side_effect=lambda code, *_: active_sample(code)),
    ):
        await hass.services.async_call(DOMAIN, service, data, blocking=True)
        await hass.async_block_till_done()
        # A refresh requested inside the debouncer's cooldown runs after it.
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
        await hass.async_block_till_done()


def _contents(entry: MockConfigEntry, code: str) -> str | None:
    parcels = {p["barcode"]: p for p in entry.runtime_data.coordinator.data}
    return parcels[code]["contents"]


async def test_a_parcel_has_no_contents_without_a_stored_value(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    assert _contents(entry, ACTIVE_CODE) is None


async def test_update_contents_sets_replaces_and_clears(hass):
    entry = await _setup(hass, [ACTIVE_CODE])

    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": " Running  shoes "},
    )
    assert _contents(entry, ACTIVE_CODE) == "Running shoes"

    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Winter socks"},
    )
    assert _contents(entry, ACTIVE_CODE) == "Winter socks"

    await _call(
        hass, "update_contents", {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "  "}
    )
    assert _contents(entry, ACTIVE_CODE) is None


async def test_update_contents_without_text_clears(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    await _call(hass, "update_contents", {CONF_TRACKING_CODE: ACTIVE_CODE})

    assert _contents(entry, ACTIVE_CODE) is None


async def test_update_contents_trims_and_matches_the_code(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {
            CONF_TRACKING_CODE: " sk-1234 567890 ",
            "contents": "Running shoes",
        },
    )
    assert _contents(entry, ACTIVE_CODE) == "Running shoes"


async def test_update_contents_works_for_a_code_that_is_not_tracked(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: UNTRACKED, "contents": "Running shoes"},
    )

    assert entry.runtime_data.coordinator.contents_store.get(UNTRACKED) == (
        "Running shoes"
    )
    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]


async def test_update_contents_never_polls_and_notifies_once_per_change(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    coordinator = entry.runtime_data.coordinator
    data = {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"}
    with (
        patch(f"{REFRESH}.async_request_refresh", new=AsyncMock()) as refresh,
        patch(f"{CLIENT}.async_get_parcel", new=AsyncMock()) as fetch,
        patch(f"{CLIENT}.async_get_shipment_info", new=AsyncMock()) as info,
        patch.object(
            coordinator,
            "async_update_listeners",
            wraps=coordinator.async_update_listeners,
        ) as listeners,
    ):
        await hass.services.async_call(DOMAIN, "update_contents", data, blocking=True)
        assert listeners.call_count == 1
        await hass.services.async_call(DOMAIN, "update_contents", data, blocking=True)
        assert listeners.call_count == 1
        await hass.services.async_call(
            DOMAIN, "update_contents", {CONF_TRACKING_CODE: UNTRACKED}, blocking=True
        )
        assert listeners.call_count == 1
        await hass.services.async_call(
            DOMAIN, "update_contents", {CONF_TRACKING_CODE: ACTIVE_CODE}, blocking=True
        )
        assert listeners.call_count == 2
    refresh.assert_not_called()
    fetch.assert_not_called()
    info.assert_not_called()
    assert _contents(entry, ACTIVE_CODE) is None


async def test_update_contents_rejects_an_empty_code(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await _call(hass, "update_contents", {CONF_TRACKING_CODE: ""})


async def test_update_contents_needs_a_loaded_entry(hass):
    entry = await _setup(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            "update_contents",
            {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
        )


async def test_track_parcel_stores_contents_for_a_new_code(hass):
    entry = await _setup(hass)
    await _call(
        hass,
        "track_parcel",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]
    assert _contents(entry, ACTIVE_CODE) == "Running shoes"


async def test_track_parcel_without_contents_is_unchanged(hass):
    entry = await _setup(hass)
    await _call(hass, "track_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})

    assert entry.runtime_data.coordinator.contents_store.get(ACTIVE_CODE) is None
    assert _contents(entry, ACTIVE_CODE) is None


async def test_track_parcel_replaces_and_clears_contents_of_a_tracked_code(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    track = {CONF_TRACKING_CODE: ACTIVE_CODE}

    await _call(hass, "track_parcel", {**track, "contents": "Running shoes"})
    assert _contents(entry, ACTIVE_CODE) == "Running shoes"

    await _call(hass, "track_parcel", {**track, "contents": "Winter socks"})
    assert _contents(entry, ACTIVE_CODE) == "Winter socks"

    await _call(hass, "track_parcel", {**track, "contents": ""})
    assert _contents(entry, ACTIVE_CODE) is None
    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]


async def test_track_parcel_on_a_tracked_code_never_polls(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    coordinator = entry.runtime_data.coordinator
    data = {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"}
    with (
        patch(f"{REFRESH}.async_request_refresh", new=AsyncMock()) as refresh,
        patch(f"{CLIENT}.async_get_parcel", new=AsyncMock()) as fetch,
        patch(f"{CLIENT}.async_get_shipment_info", new=AsyncMock()) as info,
        patch.object(
            coordinator,
            "async_update_listeners",
            wraps=coordinator.async_update_listeners,
        ) as listeners,
    ):
        await hass.services.async_call(DOMAIN, "track_parcel", data, blocking=True)
        await hass.services.async_call(DOMAIN, "track_parcel", data, blocking=True)
    assert listeners.call_count == 1
    refresh.assert_not_called()
    fetch.assert_not_called()
    info.assert_not_called()
    assert _contents(entry, ACTIVE_CODE) == "Running shoes"


async def test_a_newly_tracked_code_still_refreshes_via_the_options_listener(hass):
    entry = await _setup(hass)
    fetch = AsyncMock(side_effect=lambda code, *_: active_sample(code))
    with patch(f"{CLIENT}.async_get_parcel", new=fetch):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
            blocking=True,
        )
        await hass.async_block_till_done()
    fetch.assert_awaited()
    assert _contents(entry, ACTIVE_CODE) == "Running shoes"


async def test_reapply_contents_covers_delivered_parcels(hass):
    entry = _config_entry([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    with patch(
        f"{CLIENT}.async_get_parcel",
        new=AsyncMock(return_value=delivered_sample(ACTIVE_CODE)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinator
    coordinator.delivered = [normalize_parcel(delivered_sample(ACTIVE_CODE))]

    coordinator.contents_store.async_set(ACTIVE_CODE, "Running shoes")
    coordinator.async_reapply_contents()
    assert coordinator.delivered[0]["contents"] == "Running shoes"

    coordinator.contents_store.async_set(ACTIVE_CODE, None)
    coordinator.async_reapply_contents()
    assert coordinator.delivered[0]["contents"] is None


# ---------------------------------------------------------------------------
# routing across entries
# ---------------------------------------------------------------------------


async def _setup_two(hass, hr_codes: list[str], bg_codes: list[str]):
    entries = []
    for postal_code, codes in (("28012", hr_codes), ("08001", bg_codes)):
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"{DOMAIN}_{postal_code}",
            options={
                CONF_PARCELS: [{CONF_TRACKING_CODE: c} for c in codes],
                CONF_POSTAL_CODE: postal_code,
            },
        )
        entry.add_to_hass(hass)
        entries.append(entry)
    with patch(
        f"{CLIENT}.async_get_parcel", new=AsyncMock(return_value=active_sample())
    ):
        await hass.config_entries.async_setup(entries[0].entry_id)
        await hass.async_block_till_done()
    return entries


async def _unload_all(hass, *entries) -> None:
    for entry in entries:
        entry.mock_state(hass, ConfigEntryState.LOADED)
        await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def _stored(entry: MockConfigEntry, code: str) -> str | None:
    return entry.runtime_data.coordinator.contents_store.get(code)


async def test_update_contents_goes_to_every_entry_listing_the_code(hass):
    hr, bg = await _setup_two(hass, [ACTIVE_CODE], [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )
    assert _stored(hr, ACTIVE_CODE) == "Running shoes"
    assert _stored(bg, ACTIVE_CODE) == "Running shoes"


async def test_update_contents_goes_only_to_the_entry_listing_the_code(hass):
    hr, bg = await _setup_two(hass, [ACTIVE_CODE], [])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )
    assert _stored(hr, ACTIVE_CODE) == "Running shoes"
    assert _stored(bg, ACTIVE_CODE) is None


async def test_update_contents_needs_an_entry_when_none_lists_the_code(hass):
    await _setup_two(hass, [ACTIVE_CODE], [])
    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass,
            "update_contents",
            {CONF_TRACKING_CODE: UNTRACKED, "contents": "Running shoes"},
        )
    assert err.value.translation_key == "update_contents_entry_required"


async def test_config_entry_id_targets_exactly_that_entry(hass):
    hr, bg = await _setup_two(hass, [ACTIVE_CODE], [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {
            CONF_TRACKING_CODE: UNTRACKED,
            "contents": "Running shoes",
            "config_entry_id": bg.entry_id,
        },
    )
    assert _stored(bg, UNTRACKED) == "Running shoes"
    assert _stored(hr, UNTRACKED) is None


async def test_config_entry_id_of_an_unloaded_or_unknown_entry_is_rejected(hass):
    hr, bg = await _setup_two(hass, [ACTIVE_CODE], [ACTIVE_CODE])
    bg.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    for entry_id in (bg.entry_id, "nope"):
        with pytest.raises(ServiceValidationError):
            await _call(
                hass,
                "update_contents",
                {
                    CONF_TRACKING_CODE: ACTIVE_CODE,
                    "contents": "Running shoes",
                    "config_entry_id": entry_id,
                },
            )
    await _unload_all(hass, hr, bg)


async def test_an_unloaded_entry_is_never_targeted(hass):
    hr, bg = await _setup_two(hass, [ACTIVE_CODE], [ACTIVE_CODE])
    bg.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: UNTRACKED, "contents": "Running shoes"},
    )
    assert _stored(hr, UNTRACKED) == "Running shoes"
    assert _stored(bg, UNTRACKED) is None
    await _unload_all(hass, hr, bg)


async def test_untrack_parcel_drops_the_contents(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})

    assert entry.runtime_data.coordinator.contents_store.get(ACTIVE_CODE) is None
    assert entry.options[CONF_PARCELS] == []


async def test_a_stored_value_survives_a_restart_of_the_entry(hass, hass_storage):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )
    await entry.runtime_data.coordinator.contents_store._store.async_save(
        entry.runtime_data.coordinator.contents_store._data_to_save()
    )

    assert await hass.config_entries.async_unload(entry.entry_id)
    with patch(
        f"{CLIENT}.async_get_parcel", new=AsyncMock(return_value=active_sample())
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert _contents(entry, ACTIVE_CODE) == "Running shoes"


async def test_removing_the_entry_removes_its_store(hass, hass_storage):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )
    store = entry.runtime_data.coordinator.contents_store
    await store._store.async_save(store._data_to_save())
    key = STORE_KEY % entry.entry_id
    assert key in hass_storage

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert key not in hass_storage


async def test_a_contents_change_alone_fires_no_event(hass):
    await _setup(hass, [ACTIVE_CODE])
    events = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_status_changed", events.append)
    hass.bus.async_listen(f"{DOMAIN}_parcel_registered", events.append)
    hass.bus.async_listen(f"{DOMAIN}_parcel_delivered", events.append)

    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    assert events == []


async def test_contents_follow_the_requested_code_when_the_barcode_differs(hass):
    entry = _config_entry([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)

    def other_barcode(code, *_):
        sample = active_sample(code)
        sample["shipmentNr"] = "EXAMPLE-OTHER"
        return sample

    with patch(f"{CLIENT}.async_get_parcel", new=AsyncMock(side_effect=other_barcode)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await _call(
            hass,
            "update_contents",
            {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
        )
    parcel = entry.runtime_data.coordinator.data[0]
    assert parcel["barcode"] == "EXAMPLE-OTHER"
    assert parcel["contents"] == "Running shoes"


async def test_contents_survive_reapply_and_poll_for_a_parcel_without_a_barcode(hass):
    entry = _config_entry([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)

    def no_barcode(code, *_):
        sample = active_sample(code)
        sample["shipmentNr"] = None
        return sample

    with patch(f"{CLIENT}.async_get_parcel", new=AsyncMock(side_effect=no_barcode)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data.coordinator
        assert not coordinator.data[0]["barcode"]
        await _call(
            hass,
            "update_contents",
            {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
        )
        assert coordinator.data[0]["contents"] == "Running shoes"
        coordinator.async_reapply_contents()
        assert coordinator.data[0]["contents"] == "Running shoes"
        await coordinator.async_refresh()
        assert coordinator.data[0]["contents"] == "Running shoes"


async def test_track_parcel_with_contents_needs_a_loaded_entry(hass):
    entry = await _setup(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            "track_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
        )


async def test_update_contents_needs_a_set_up_entry(hass):
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id) is True
    assert not hass.services.has_service(DOMAIN, "update_contents")
