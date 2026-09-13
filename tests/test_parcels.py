"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping (the part you
rewrite per carrier) can be tested as plain functions.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.slovak_parcel_service.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    ParcelStatus,
)
from custom_components.slovak_parcel_service.parcels import (
    apply_delivered_filter,
    build_history,
    extract_shipment_info,
    format_dimensions,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    parse_local_time,
    sort_parcels_by_ts,
    to_iso_timestamp,
)

from .payloads import (
    active_sample,
    authorized_info_sample,
    collection_sample,
    delivered_sample,
    event,
    pickup_sample,
    unauthorized_info_sample,
)

# ---------------------------------------------------------------------------
# map_parcel_status (textual shipmentStatus vocabulary)
# ---------------------------------------------------------------------------


def test_map_parcel_status_delivered():
    assert map_parcel_status("delivered") == ParcelStatus.DELIVERED


def test_map_parcel_status_missing_is_unknown():
    assert map_parcel_status(None) == ParcelStatus.UNKNOWN
    assert map_parcel_status("") == ParcelStatus.UNKNOWN


def test_map_parcel_status_unmapped_is_unknown():
    """Only "delivered" is a confirmed textual value — everything else falls
    through to unknown rather than guessing a label."""
    assert map_parcel_status("in_transit") == ParcelStatus.UNKNOWN


def test_unmapped_shipment_status_warns_only_once(caplog):
    assert map_parcel_status("ABDUCTED") == ParcelStatus.UNKNOWN
    assert map_parcel_status("ABDUCTED") == ParcelStatus.UNKNOWN
    assert caplog.text.count("ABDUCTED") == 1
    assert "issues/new" in caplog.text


# ---------------------------------------------------------------------------
# map_event_status (states[] event vocabulary)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status_code,expected",
    [
        ("DDEF", ParcelStatus.REGISTERED),
        ("INIT", ParcelStatus.REGISTERED),
        ("TOUR", ParcelStatus.OUT_FOR_DELIVERY),
        ("DELY", ParcelStatus.DELIVERED),
        ("PICK", ParcelStatus.IN_TRANSIT),
    ],
)
def test_map_event_status_known_status_codes(status_code, expected):
    assert map_event_status({"statusCode": status_code, "status": 0}) == expected


def test_map_event_status_prefers_status_code_over_numeric():
    """`statusCode` is the stable, non-localized key and wins when both are
    present but would disagree (which never happens on real data, but proves
    the lookup order)."""
    assert (
        map_event_status({"statusCode": "DELY", "status": 30})
        == ParcelStatus.DELIVERED
    )


def test_map_event_status_falls_back_to_numeric_code():
    """A missing/unrecognised statusCode falls back to the numeric code."""
    assert map_event_status({"statusCode": None, "status": 40}) == ParcelStatus.DELIVERED
    assert map_event_status({"status": 8}) == ParcelStatus.REGISTERED


def test_map_event_status_retc_is_in_transit_not_returning():
    """`50`/RETC was followed by a later out-for-delivery event in both
    confirmed payloads, so the parcel is back in the network awaiting another
    attempt — never a terminal return."""
    assert (
        map_event_status({"statusCode": "RETC", "status": 50})
        is ParcelStatus.IN_TRANSIT
    )


def test_map_event_status_ddsp_is_silent_and_unmapped(caplog):
    """`38`/DDSP is a recipient action, not a movement: no canonical status,
    and no report-this-status warning either."""
    assert map_event_status({"statusCode": "DDSP", "status": 38}) is None
    assert "Unrecognised" not in caplog.text


def test_map_event_status_missing_and_unmapped_are_none():
    """History keeps ``null`` rather than ``unknown`` so consumers can tell
    "no mapping" from "mapped to unknown"."""
    assert map_event_status(None) is None
    assert map_event_status({}) is None
    assert map_event_status({"statusCode": "SOMETHING_NEW"}) is None


def test_unmapped_event_status_warns_only_once(caplog):
    assert map_event_status({"statusCode": "ZAPPED"}) is None
    assert map_event_status({"statusCode": "ZAPPED"}) is None
    assert caplog.text.count("ZAPPED") == 1
    assert "issues/new" in caplog.text


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == timezone.utc
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_converts_epoch_milliseconds():
    assert to_iso_timestamp(1784203767167) == "2026-07-16T12:09:27.167000+00:00"
    assert to_iso_timestamp("2026-04-29T13:12:42Z") == "2026-04-29T13:12:42Z"
    assert to_iso_timestamp(None) is None
    assert to_iso_timestamp(10**20) is None  # out of range -> None, never raises


def test_parse_local_time_treats_naive_values_as_bratislava_local():
    # DST (CEST, UTC+2) in late April.
    assert parse_local_time("2026-04-29 13:12:42") == "2026-04-29T13:12:42+02:00"
    # Standard time (CET, UTC+1) in January.
    assert parse_local_time("2026-01-15 09:00:00") == "2026-01-15T09:00:00+01:00"


def test_parse_local_time_trusts_an_explicit_offset():
    assert parse_local_time("2026-04-29T13:12:42+00:00") == "2026-04-29T13:12:42+00:00"


def test_parse_local_time_handles_garbage_and_missing():
    assert parse_local_time("not-a-date") is None
    assert parse_local_time(None) is None
    assert parse_local_time("") is None


def test_format_dimensions_needs_all_three_axes():
    assert format_dimensions(30, 20, 10) == {
        "length": 30,
        "width": 20,
        "height": 10,
        "text": "30 x 20 x 10 cm",
    }
    assert format_dimensions(30, None, 10) is None


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def test_build_history_orders_oldest_to_newest():
    history = build_history(delivered_sample()["states"])
    assert len(history) == 4
    assert history[0]["raw_status"] == "Definícia dát"
    assert history[0]["status"] == ParcelStatus.REGISTERED
    assert history[-1]["status"] == ParcelStatus.DELIVERED


def test_build_history_caps_to_max_events():
    events = [
        event(10, "INIT", f"2026-04-{day:02d} 10:00:00", "moved")
        for day in range(1, 26)
    ]
    assert len(build_history(events, max_events=20)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"status": 10}]) == []  # no timestamp
    assert build_history(["not-a-dict"]) == []


def test_build_history_drops_events_with_unparseable_time():
    """A `time` value that doesn't parse as a Slovak local timestamp is
    dropped rather than kept with a broken timestamp."""
    history = build_history(
        [
            event(8, "DDEF", "2026-04-24 10:00:00", "fine"),
            event(10, "INIT", "not-a-date", "odd"),
        ]
    )
    assert [entry["raw_status"] for entry in history] == ["fine"]


def test_build_history_falls_back_to_status_code_without_text():
    history = build_history([event(10, "INIT", "2026-04-24 10:00:00", "")])
    assert history[0]["raw_status"] == "INIT"


def test_build_history_maps_pick_to_in_transit():
    """44/PICK ("Odnáška", the collection scan at the sender) maps to
    `in_transit` — exercised via the second confirmed real parcel."""
    history = build_history(collection_sample()["states"])
    pick_entry = next(entry for entry in history if entry["raw_status"] == "Odnáška")
    assert pick_entry["status"] == ParcelStatus.IN_TRANSIT


def test_build_history_maps_retc_to_in_transit():
    """`50`/RETC reads as back-in-the-network, never as a terminal return."""
    history = build_history(collection_sample()["states"])
    retc_entries = [
        entry for entry in history if entry["raw_status"] == "Vrátený do skladu"
    ]
    assert retc_entries
    assert all(entry["status"] is ParcelStatus.IN_TRANSIT for entry in retc_entries)
    assert all(entry["status"] is not ParcelStatus.RETURNING for entry in retc_entries)


# ---------------------------------------------------------------------------
# extract_shipment_info (getShipmentInfo panel parsing)
# ---------------------------------------------------------------------------


def test_extract_shipment_info_without_postcode_gets_sender_and_weight_only():
    info = extract_shipment_info(unauthorized_info_sample())
    assert info == {"sender": "Example Sender s.r.o.", "weight": 4.55, "receiver": None}


def test_extract_shipment_info_with_matching_postcode_adds_receiver():
    info = extract_shipment_info(authorized_info_sample())
    assert info == {
        "sender": "Example Sender s.r.o.",
        "weight": 7.7,
        "receiver": "Jana Vzorova",
    }


def test_extract_shipment_info_handles_none_and_malformed():
    assert extract_shipment_info(None) == {"sender": None, "weight": None, "receiver": None}
    assert extract_shipment_info({}) == {"sender": None, "weight": None, "receiver": None}
    assert extract_shipment_info({"panels": "not-a-dict"}) == {
        "sender": None,
        "weight": None,
        "receiver": None,
    }


def test_extract_shipment_info_ignores_receiver_when_not_authorized():
    """``authorized: false`` must never surface panel_1 even if it somehow appears."""
    data = unauthorized_info_sample()
    data["authorized"] = False
    data["panels"]["panel_1"] = {
        "fields": {"recipient": {"value": "Should not leak"}}
    }
    assert extract_shipment_info(data)["receiver"] is None


def test_extract_shipment_info_handles_unparseable_weight():
    data = unauthorized_info_sample()
    data["panels"]["panel_0"]["fields"]["weight"]["value"] = "not-a-number"
    assert extract_shipment_info(data)["weight"] is None


def test_extract_shipment_info_blank_sender_becomes_none():
    data = unauthorized_info_sample()
    data["panels"]["panel_0"]["fields"]["sender"]["value"] = ""
    assert extract_shipment_info(data)["sender"] is None


def test_extract_shipment_info_warns_once_on_unexpected_shape(caplog):
    assert extract_shipment_info({"shipmentNr": "SK000WARN", "panels": {}}) == {
        "sender": None,
        "weight": None,
        "receiver": None,
    }
    assert extract_shipment_info({"shipmentNr": "SK000WARN", "panels": {}}) == {
        "sender": None,
        "weight": None,
        "receiver": None,
    }
    assert caplog.text.count("shipment-info response had no usable") == 1


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
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


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    """Every declared CAPABILITIES entry must come true somewhere in a sample.

    Copy this test into a real carrier's own test_parcels.py verbatim — it
    stays correct for whatever subset of CAPABILITIES that carrier declares.
    """
    # "weight" is only ever populated via the shipment-info enrichment call
    # (see coordinator.py) — not something normalize_parcel derives from the
    # tracking payload alone, so it needs an explicit ``info`` here.
    delivered = normalize_parcel(
        delivered_sample(), info={"sender": "Example Sender", "weight": 4.55, "receiver": None}
    )
    active = normalize_parcel(active_sample())
    pickup = normalize_parcel(pickup_sample())
    with_history = normalize_parcel(delivered_sample(), include_history=True)

    if "weight" in CAPABILITIES:
        assert delivered["weight"] is not None
    if "dimensions" in CAPABILITIES:
        assert delivered["dimensions"] is not None
    if "delivery_window" in CAPABILITIES:
        assert active["planned_from"] is not None or active["planned_to"] is not None
    if "pickup_point" in CAPABILITIES:
        assert pickup["pickup_point"] is not None
    if "url" in CAPABILITIES:
        assert delivered["url"] is not None
    if "history" in CAPABILITIES:
        assert with_history["history"] is not None


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "Slovak Parcel Service"
    assert parcel["barcode"] == "JJD123456789012345678901"
    assert parcel["sender"] is None
    assert parcel["receiver"] is None
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Doručenie"
    assert parcel["delivered"] is True
    # Slovak local time (CEST, UTC+2 in late April), not UTC.
    assert parcel["delivered_at"] == "2026-04-29T13:12:42+02:00"
    # A delivered parcel drops its ETA — the window is meaningless once it has
    # arrived.
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["url"] == "https://mysps.sk/sk/tracking/JJD123456789012345678901"
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_wires_in_sender_weight_and_receiver_from_info():
    info = extract_shipment_info(authorized_info_sample())
    parcel = normalize_parcel(delivered_sample(), info=info)
    assert parcel["sender"] == "Example Sender s.r.o."
    assert parcel["weight"] == 7.7
    assert parcel["receiver"] == "Jana Vzorova"


def test_normalize_without_info_leaves_sender_weight_receiver_none():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["sender"] is None
    assert parcel["weight"] is None
    assert parcel["receiver"] is None


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert len(parcel["history"]) == 4
    assert parcel["history"][0]["status"] == ParcelStatus.REGISTERED


def test_normalize_active_parcel_derives_status_from_current_event():
    """`shipmentStatus` is only confirmed for "delivered"; a non-delivered
    parcel's status comes from its current event's confirmed statusCode."""
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["delivered"] is False
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None


def test_normalize_unknown_status_does_not_infer_pickup():
    parcel = normalize_parcel(pickup_sample())
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_normalize_pending_placeholder():
    """A tracked-but-not-yet-scanned code still yields a full parcel dict."""
    parcel = normalize_parcel({"shipmentNr": "SK0000000001"})
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None
    assert parcel["history"] is None


def test_normalize_blank_fields_become_none():
    raw = active_sample()
    raw["sender"] = ""
    raw["recipient"] = ""
    parcel = normalize_parcel(raw)
    assert parcel["sender"] is None
    assert parcel["receiver"] is None


def test_normalize_removes_personal_raw_payload():
    raw = active_sample()
    safe = normalize_parcel(raw)["raw"]
    assert "senderAddress" not in safe and "receiverAddress" not in safe and "actions" not in safe


def test_normalize_falls_back_to_status_code_without_text():
    raw = active_sample()
    raw["states"][0]["statusDescr"] = ""
    assert normalize_parcel(raw)["raw_status"] == raw["states"][0]["statusCode"]


def test_normalize_last_status_scan_falls_back_to_newest_event():
    """An unrecognised `lastStatusScan` falls back to the newest event."""
    raw = active_sample()
    raw["lastStatusScan"] = "SOMETHING_UNSEEN"
    parcel = normalize_parcel(raw)
    assert parcel["raw_status"] == raw["states"][0]["statusDescr"]


def test_normalize_delivered_at_falls_back_to_scanning_states_for_dely():
    """If `lastStatusScan` points at a stale/mismatched event, `delivered_at`
    still finds the newest `DELY` event by scanning `states`."""
    raw = delivered_sample()
    raw["lastStatusScan"] = "TOUR"  # doesn't match the current (DELY) event
    parcel = normalize_parcel(raw)
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-04-29T13:12:42+02:00"
    assert parcel["raw_status"] == "Doručovací deň"


def test_normalize_collection_sample_is_delivered_via_shipment_status():
    """The second real capture: collected at the sender (`44`/PICK), bounced
    through `50`/RETC, delivered — `shipmentStatus` "delivered" is
    authoritative regardless of the current event."""
    parcel = normalize_parcel(collection_sample())
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] is not None


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id="84105",
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels
