"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

The carrier-specific mapping lives in :data:`_SHIPMENT_STATUS_MAP`,
:data:`_EVENT_STATUS_MAP`, :func:`normalize_parcel` and
:func:`extract_shipment_info`, plus the local-time parsing in
:func:`parse_local_time` (shaped around this surface's own ``states[].time``
field, which is Slovak local time, not UTC). Everything else — generic
timestamp parsing, the history builder, the sort contract, the delivered
filter, the one-shot warning for unmapped statuses — is suite-wide machinery
and should be left alone.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-slovak-parcel-service/issues/new"
    "?template=unrecognised_status.yml"
)

# Two distinct, disjoint vocabularies exist on this payload and must not
# share one map (this used to be the integration's headline bug):
#
# * ``shipmentStatus`` — a textual, one-shot lifecycle label for the whole
#   shipment (e.g. "delivered"). Only "delivered" has ever been observed;
#   anything else falls through to ``unknown`` plus the one-shot warning
#   below rather than guessing a label.
# * ``states[]`` events — each has both a stable, non-localized ``statusCode``
#   (e.g. "DELY") and a Slovak-language numeric ``status`` (e.g. ``40``).
#   ``statusCode`` is preferred as the map key since it does not depend on a
#   translation staying stable release to release; the numeric code is kept
#   as a fallback for the (so far unobserved) case where ``statusCode`` is
#   missing. `50`/`RETC` ("Vrátený do skladu") is deliberately NOT mapped to
#   a terminal `returning` — in both confirmed payloads it was followed by a
#   later out-for-delivery/delivery event, so the parcel is back in the
#   carrier's network awaiting another attempt, which is `in_transit`.
#   `44`/`PICK` ("Odnáška", the collection scan at the sender) maps to
#   `in_transit` on a single observation — revisit if a counterexample turns
#   up.
_SHIPMENT_STATUS_MAP: dict[str, ParcelStatus] = {
    "delivered": ParcelStatus.DELIVERED,
}

_EVENT_STATUS_MAP: dict[str, ParcelStatus] = {
    # statusCode — stable, non-localized (preferred key)
    "DDEF": ParcelStatus.REGISTERED,
    "INIT": ParcelStatus.REGISTERED,
    "TOUR": ParcelStatus.OUT_FOR_DELIVERY,
    "DELY": ParcelStatus.DELIVERED,
    "PICK": ParcelStatus.IN_TRANSIT,
    "RETC": ParcelStatus.IN_TRANSIT,
    # numeric status — fallback when statusCode is missing/unrecognised
    "8": ParcelStatus.REGISTERED,
    "10": ParcelStatus.REGISTERED,
    "30": ParcelStatus.OUT_FOR_DELIVERY,
    "40": ParcelStatus.DELIVERED,
    "44": ParcelStatus.IN_TRANSIT,
    "50": ParcelStatus.IN_TRANSIT,
}

# Codes we recognise but that describe no movement of the parcel, so they have
# no canonical equivalent. Kept apart from the map above so they report the
# same ``None``/``unknown`` as a genuinely unknown code *without* asking the
# user to report something we have already looked at.
_NON_LIFECYCLE_EVENT_CODES = frozenset(
    {
        "DDSP",  # "Upresnenie doručenia (cez WEB)" — recipient gave delivery
        "38",    # instructions on the website; the parcel itself did not move.
    }
)

# Status codes we have already warned about, so each unmapped one is logged
# only once per HA session instead of on every poll.
_unmapped_statuses_logged: set[str] = set()


def _warn_unmapped_status(code: str) -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    if code in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(code)
    _LOGGER.warning(
        "Unrecognised Slovak Parcel Service status — help us map it. Open an issue "
        "and paste this line: %s\n  status=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        code,
    )


def map_parcel_status(status_text: str | None) -> ParcelStatus:
    """Map the shipment-level ``shipmentStatus`` text to a canonical status.

    ``None`` (a not-yet-scanned parcel) reports ``unknown`` silently; an
    unrecognised value reports ``unknown`` with a one-shot warning.
    """
    if not status_text:
        return ParcelStatus.UNKNOWN
    mapped = _SHIPMENT_STATUS_MAP.get(status_text)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(status_text)
    return ParcelStatus.UNKNOWN


def map_event_status(event: dict | None) -> ParcelStatus | None:
    """Map one ``states[]`` entry to a canonical status, or ``None``.

    Tries the stable ``statusCode`` first, then the numeric ``status`` code.
    Unmapped codes keep ``status: null`` on the history entry (rather than
    ``unknown``, so a consumer can tell "no mapping" from "mapped to
    unknown") and warn once, reusing the shipment-status one-shot set — except
    the recognised non-lifecycle codes, which are silent.
    """
    if not event:
        return None
    status_code = event.get("statusCode")
    if status_code:
        mapped = _EVENT_STATUS_MAP.get(str(status_code))
        if mapped is not None:
            return mapped
    numeric_code = event.get("status")
    numeric_code = str(numeric_code) if numeric_code is not None else None
    if numeric_code:
        mapped = _EVENT_STATUS_MAP.get(numeric_code)
        if mapped is not None:
            return mapped
    warn_key = str(status_code) if status_code else numeric_code
    if warn_key and warn_key not in _NON_LIFECYCLE_EVENT_CODES:
        _warn_unmapped_status(warn_key)
    return None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# Every ``states[].time`` value is Slovak local time regardless of where HA
# runs — confirmed against two live payloads spanning DST and non-DST dates
# with no offset in the string. Fixed to the carrier's own country rather
# than HA's configured timezone, which is a fine distinction if this ever
# tracks a parcel from outside Slovakia's own timezone.
_CARRIER_TZ = ZoneInfo("Europe/Bratislava")


def parse_local_time(value: str | None) -> str | None:
    """Parse a ``states[].time`` value ("YYYY-MM-DD HH:MM:SS") as Slovak local time.

    Returns an aware ISO 8601 string (so it sorts and compares like every
    other timestamp in this module), or ``None`` on a missing/unparseable
    value. A value that already carries an offset is trusted as-is.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_CARRIER_TZ)
    return parsed.isoformat()


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds** — the common case for the
    consumer APIs in this suite. Strings pass through untouched; their
    consumers are guarded by :func:`parse_iso`. Adjust the numeric branch if
    your carrier stamps in seconds.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: **centimetres**, with ``text`` pre-formatted as
    ``"L x W x H cm"`` (integer values, lowercase ``x``) so dashboards can show
    a dimension without doing their own formatting. Convert before calling if
    the carrier reports millimetres or inches.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{int(length)} x {int(width)} x {int(height)} cm",
    }


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from the carrier's event list.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. ``timestamp`` comes from each event's own
    ``time``, parsed as Slovak local time (see :func:`parse_local_time`) —
    never treated as UTC. ``raw_status`` is the carrier's own text, or its
    event code when the API has no human-readable text. Sorted oldest →
    newest and capped to the most recent ``max_events``.
    """
    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[dict] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = parse_local_time(event.get("time"))
        if not timestamp:
            continue
        entry = {
            "timestamp": timestamp,
            "status": map_event_status(event),
            "raw_status": event.get("statusDescr") or event.get("statusCode") or event.get("status"),
        }
        parsed = parse_iso(timestamp)
        if parsed is None:
            unparseable.append(entry)
        else:
            parseable.append((parsed, entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
    return ordered[-max_events:]


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


# Shipment-info panels/fields we've seen have been confirmed against two real
# parcels — both with and without a matching ``recipientzip`` header (see
# api.py's ``async_get_shipment_info``). A response that doesn't match this
# shape at all is logged once rather than raising, since this endpoint is
# enrichment, never the primary source.
_info_shape_warned: set[str] = set()


def _panel_field_value(data: dict, panel_key: str, field_key: str) -> Any:
    """Dig one ``panels.<panel_key>.fields.<field_key>.value`` out of a getShipmentInfo response."""
    panels = data.get("panels")
    panel = panels.get(panel_key) if isinstance(panels, dict) else None
    fields = panel.get("fields") if isinstance(panel, dict) else None
    field = fields.get(field_key) if isinstance(fields, dict) else None
    return field.get("value") if isinstance(field, dict) else None


def extract_shipment_info(data: dict | None) -> dict[str, Any]:
    """Pull ``sender``/``weight``/``receiver`` out of one getShipmentInfo response.

    ``data`` is the endpoint's own ``data`` object (already unwrapped from the
    envelope by :meth:`SlovakParcelServiceApiClient.async_get_shipment_info`).
    ``sender`` and ``weight`` live on ``panel_0`` and are present even without
    a matching postcode; ``receiver`` only appears on ``panel_1`` once
    ``data.authorized`` is true — a wrong or absent postcode is not an error,
    it just means ``receiver`` stays ``None``. Anything malformed or missing
    degrades to ``None`` fields (with a one-shot warning) rather than
    raising, since this is enrichment, never the primary source.
    """
    if not isinstance(data, dict):
        return {"sender": None, "weight": None, "receiver": None}

    sender = _panel_field_value(data, "panel_0", "sender")
    sender = str(sender) if sender else None

    weight_value = _panel_field_value(data, "panel_0", "weight")
    try:
        weight = float(weight_value) if weight_value is not None else None
    except (TypeError, ValueError):
        weight = None

    receiver = None
    if data.get("authorized") is True:
        receiver = _panel_field_value(data, "panel_1", "recipient")
        receiver = str(receiver) if receiver else None

    if sender is None and weight is None:
        shipment_nr = data.get("shipmentNr")
        warn_key = str(shipment_nr) if shipment_nr else "?"
        if warn_key not in _info_shape_warned:
            _info_shape_warned.add(warn_key)
            _LOGGER.warning(
                "Slovak Parcel Service shipment-info response had no usable "
                "sender/weight — the panel shape may have changed. Open an "
                "issue and paste this line: %s",
                NEW_ISSUE_URL,
            )

    return {"sender": sender, "weight": weight, "receiver": receiver}


def normalize_parcel(
    raw: dict, *, include_history: bool = False, info: dict | None = None
) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The **keys of the returned dict are the contract**: every carrier in the
    suite returns exactly these, in this order, and the aggregator and
    cross-carrier dashboards depend on it. Set a key to ``None`` when the
    carrier does not expose it — never omit it.

    Rules worth keeping when you rewrite the body:

    * ``status`` is canonical, ``raw_status`` is the carrier's own text.
    * A delivered parcel has ``delivered_at`` set and ``planned_from`` /
      ``planned_to`` cleared — the ETA is meaningless once it has arrived.
    * ``planned_to`` is ``None`` for a point estimate; only fill it when the
      carrier genuinely reports a *window*.
    * ``weight`` is kilograms, ``dimensions`` centimetres (see
      :func:`format_dimensions`).
    * ``history`` is ``None`` when the option is off — the key still exists.

    ``info`` is the ``extract_shipment_info()`` result for this parcel's
    getShipmentInfo response, if one has been fetched (see coordinator.py —
    it's cached per tracking code and not re-fetched on every poll). ``None``
    when no info call has succeeded yet, which leaves sender/weight/receiver
    at their default of ``None``.
    """
    info = info or {}
    tracking_code = raw.get("shipmentNr")
    states = [state for state in raw.get("states") or [] if isinstance(state, dict)]

    # ``lastStatusScan`` is the stable statusCode ("DELY") of the current
    # event — look it up in ``states`` (newest-first) to get its full detail.
    # Falls back to the first (newest) event when it doesn't match anything,
    # e.g. an unrecognised statusCode.
    last_status_scan = raw.get("lastStatusScan")
    current_scan = None
    if isinstance(last_status_scan, str) and last_status_scan:
        for state in states:
            if state.get("statusCode") == last_status_scan:
                current_scan = state
                break
    if current_scan is None and states:
        current_scan = states[0]

    # The event vocabulary (``states[].statusCode``/``status``) is the primary
    # source for the current lifecycle status: every value we map from it —
    # registered/out_for_delivery/delivered/in_transit — has been directly
    # confirmed in a live payload. The shipment-level ``shipmentStatus`` text
    # is only confirmed for "delivered"; it is checked for the ``delivered``
    # signal specifically (redundant with a `DELY` event, but authoritative
    # and cheap to honour), and otherwise left to warn-and-fall-through rather
    # than override a confirmed event-derived status with a guess.
    shipment_status_text = raw.get("shipmentStatus")
    shipment_status = map_parcel_status(
        str(shipment_status_text) if shipment_status_text is not None else None
    )
    event_status = map_event_status(current_scan) if current_scan else None
    status = (
        ParcelStatus.DELIVERED
        if shipment_status is ParcelStatus.DELIVERED
        else (event_status or ParcelStatus.UNKNOWN)
    )
    delivered = status is ParcelStatus.DELIVERED

    raw_status = None
    if current_scan:
        raw_status = current_scan.get("statusDescr") or current_scan.get("statusCode")

    delivered_at = None
    if delivered:
        delivery_event = (
            current_scan
            if current_scan and map_event_status(current_scan) is ParcelStatus.DELIVERED
            else None
        )
        if delivery_event is None:
            for state in states:
                if map_event_status(state) is ParcelStatus.DELIVERED:
                    delivery_event = state
                    break
        if delivery_event:
            delivered_at = parse_local_time(delivery_event.get("time"))

    safe_raw = {
        key: raw[key]
        for key in ("shipmentStatus", "shippingDate", "lastStatusScan", "states")
        if key in raw
    }

    return {
        "carrier": "Slovak Parcel Service",
        "barcode": tracking_code,
        "sender": info.get("sender"),
        "receiver": info.get("receiver"),
        "status": status,
        "raw_status": raw_status or shipment_status_text,
        "delivered": delivered,
        "delivered_at": delivered_at if delivered else None,
        "planned_from": None,
        "planned_to": None,
        "pickup": False,
        "pickup_point": None,
        "url": tracking_url(tracking_code),
        "weight": info.get("weight"),
        "dimensions": None,
        "history": build_history(raw.get("states")) if include_history else None,
        "raw": safe_raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
