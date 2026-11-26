"""The parcel contract's checks, run over every normalised sample."""

from datetime import date, datetime

import pytest

from custom_components.slovak_parcel_service.const import (
    PENDING_CAPABILITIES,
    ParcelStatus,
)
from custom_components.slovak_parcel_service.parcels import (
    _CARRIER_TZ,
    _EVENT_STATUS_MAP,
    is_pickup,
    normalize_parcel,
    parse_iso,
    planned_for_status,
    planned_window,
)

from .payloads import active_sample, collection_sample, delivered_sample, pickup_sample

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]

SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "unmapped_current_event": pickup_sample,
    "bounced_then_delivered": collection_sample,
    "bare": lambda: {"shipmentNr": "SK0000000001"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_history_key_exists_when_the_option_is_off(parcel):
    assert parcel["history"] is None


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_a_delivered_parcel_has_the_delivery_scan_time():
    parcel = normalize_parcel(collection_sample())
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2025-09-12T16:06:38+02:00"


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_the_locker_name_is_pending_and_stays_none(parcel):
    assert "pickup_point" in PENDING_CAPABILITIES
    assert parcel["pickup_point"] is None


@pytest.mark.parametrize(
    "status",
    [ParcelStatus.DELIVERED, ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING],
)
def test_the_window_is_emptied_once_the_parcel_is_done_or_waiting(status):
    assert planned_for_status(status, "2099-01-01T00:00:00", "2099-01-01T23:59:59") == (
        None,
        None,
    )


def test_the_window_is_kept_while_the_parcel_is_moving():
    window = ("2099-01-01T00:00:00", "2099-01-01T23:59:59")
    assert planned_for_status(ParcelStatus.OUT_FOR_DELIVERY, *window) == window


def test_pickup_stays_true_after_collection():
    assert is_pickup(None, ParcelStatus.DELIVERED, [ParcelStatus.AT_PICKUP_POINT])
    assert is_pickup(True, ParcelStatus.DELIVERED)
    assert not is_pickup(None, ParcelStatus.DELIVERED, [ParcelStatus.IN_TRANSIT])


def test_a_moment_ends_at_the_end_of_that_day():
    start, end = planned_window("2099-04-29T13:00:00Z", None, zone=_CARRIER_TZ)
    last = datetime.fromisoformat(end)
    assert (last.hour, last.minute, last.second, last.microsecond) == (23, 59, 59, 0)
    assert last.date() == date(2099, 4, 29)
    assert start == "2099-04-29T13:00:00Z"


def test_a_date_covers_the_whole_day():
    start, end = planned_window("2099-04-29", None, zone=_CARRIER_TZ)
    first, last = datetime.fromisoformat(start), datetime.fromisoformat(end)
    assert (first.hour, first.minute, first.second) == (0, 0, 0)
    assert (last.hour, last.minute, last.second, last.microsecond) == (23, 59, 59, 0)
    assert first.date() == last.date()


def test_planned_window_handles_every_input_shape():
    zone = _CARRIER_TZ
    assert planned_window(None, None, zone=zone) == (None, None)
    assert planned_window(None, "2099-04-29", zone=zone)[0].startswith("2099-04-29")
    assert planned_window("garbage", None, zone=zone) == (None, None)
    assert planned_window(12345, None, zone=zone) == (None, None)
    assert planned_window(date(2099, 4, 29), None, zone=zone) == (
        "2099-04-29T00:00:00+02:00",
        "2099-04-29T23:59:59+02:00",
    )
    assert planned_window(datetime(2099, 4, 29, 9, 30), None, zone=zone) == (
        "2099-04-29T09:30:00+02:00",
        "2099-04-29T23:59:59+02:00",
    )
    assert planned_window("2099-04-29T09:30:00", "2099-04-29T11:30:00", zone=zone) == (
        "2099-04-29T09:30:00+02:00",
        "2099-04-29T11:30:00+02:00",
    )


def test_planned_window_swaps_reversed_and_collapses_equal_values():
    zone = _CARRIER_TZ
    assert planned_window("2099-04-30", "2099-04-28", zone=zone) == (
        "2099-04-28T00:00:00+02:00",
        "2099-04-30T23:59:59+02:00",
    )
    assert planned_window("2099-04-29T09:30:00", "2099-04-29T09:30:00", zone=zone) == (
        "2099-04-29T09:30:00+02:00",
        "2099-04-29T23:59:59+02:00",
    )


def test_a_moment_with_no_day_left_has_no_end():
    assert planned_window("2099-04-29T23:59:59", None, zone=_CARRIER_TZ) == (
        "2099-04-29T23:59:59+02:00",
        None,
    )


def test_a_locker_event_makes_the_parcel_a_pickup_parcel(monkeypatch):
    monkeypatch.setitem(_EVENT_STATUS_MAP, "DDSP", ParcelStatus.AT_PICKUP_POINT)
    parcel = normalize_parcel(pickup_sample())
    assert parcel["status"] is ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] is None
