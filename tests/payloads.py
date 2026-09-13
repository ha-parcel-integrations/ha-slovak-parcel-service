"""Synthetic, privacy-safe MySPS payloads.

Shaped after two real captures (redacted `senderAddress`/`receiverAddress`,
synthetic tracking codes): ``shipmentStatus`` is a textual lifecycle label
(only ``"delivered"`` has been observed), ``lastStatusScan`` is the stable
``statusCode`` string of the current event (not a dict), and each
``states[]`` entry carries an integer ``status`` alongside its ``statusCode``
and a naive, space-separated, Slovak-local-time ``time``.
"""
from __future__ import annotations

ACTIVE_CODE = "SK1234567890"
DELIVERED_CODE = "JJD123456789012345678901"
PICKUP_CODE = "SK9988776655"


def event(status: int, status_code: str, timestamp: str, descr: str) -> dict:
    """Build one ``states[]`` entry in the real, confirmed shape."""
    return {
        "time": timestamp,
        "status": status,
        "statusCode": status_code,
        "statusDescr": descr,
        "statusText": f"{status:03d}-{status_code}({descr})",
        "packageNrs": "REDACTED",
        "centerId": 20,
        "centerName": "SPS Košice",
        "info": "",
        "userName": "REDACTED",
        "crtim": timestamp,
    }


def _envelope(code: str, shipment_status: str, states: list[dict]) -> dict:
    return {
        "shipmentNr": code,
        "shipmentStatus": shipment_status,
        "lastStatusScan": states[0]["statusCode"] if states else None,
        "shippingDate": None,
        "states": states,
        "senderAddress": "REDACTED",
        "receiverAddress": "REDACTED",
        "actions": {"reportDamage": {"action": "ReportDamage", "label": "REDACTED"}},
    }


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A delivered parcel — newest-first, matching the confirmed payload shape."""
    states = [
        event(40, "DELY", "2026-04-29 13:12:42", "Doručenie"),
        event(30, "TOUR", "2026-04-29 08:46:00", "Doručovací deň"),
        event(10, "INIT", "2026-04-28 15:52:17", "Prvá registrácia"),
        event(8, "DDEF", "2026-04-27 23:03:58", "Definícia dát"),
    ]
    return _envelope(code, "delivered", states)


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel out for delivery — its current event is `30`/`TOUR`.

    ``shipmentStatus`` is not "delivered" here; only "delivered" is a
    confirmed textual value, so the current status comes from the confirmed
    event vocabulary instead (see ``normalize_parcel``).
    """
    states = [
        event(30, "TOUR", "2026-04-29 08:46:00", "Doručovací deň"),
        event(10, "INIT", "2026-04-28 15:52:17", "Prvá registrácia"),
        event(8, "DDEF", "2026-04-27 23:03:58", "Definícia dát"),
    ]
    return _envelope(code, "in_transit", states)


def pickup_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel whose current event is `38`/`DDSP` — deliberately unmapped."""
    states = [
        event(38, "DDSP", "2026-04-29 09:00:00", "Upresnenie doručenia (cez WEB)"),
        event(10, "INIT", "2026-04-28 15:52:17", "Prvá registrácia"),
        event(8, "DDEF", "2026-04-27 23:03:58", "Definícia dát"),
    ]
    return _envelope(code, "in_transit", states)


def unauthorized_info_sample(code: str = ACTIVE_CODE) -> dict:
    """A getShipmentInfo response with no (or a non-matching) recipientzip."""
    return {
        "shipmentNr": code,
        "authorized": False,
        "packageNrs": [],
        "panels": {
            "panel_0": {
                "name": "panel_0",
                "fields": {
                    "shipmentNr": {"name": "shipmentNr", "type": "string", "value": code},
                    "sender": {"name": "sender", "label": "LBL_SENDER", "type": "string", "value": "Example Sender s.r.o."},
                    "weight": {"name": "weight", "label": "LBL_WEIGHT", "type": "float", "value": 4.55},
                },
            }
        },
    }


def authorized_info_sample(code: str = ACTIVE_CODE) -> dict:
    """A getShipmentInfo response with a matching recipientzip header."""
    return {
        "shipmentNr": code,
        "authorized": True,
        "packageNrs": ["REDACTED"],
        "panels": {
            "panel_0": {
                "name": "panel_0",
                "fields": {
                    "shipmentNr": {"name": "shipmentNr", "type": "string", "value": code},
                    "sender": {"name": "sender", "label": "LBL_SENDER", "type": "string", "value": "Example Sender s.r.o."},
                    "weight": {"name": "weight", "label": "LBL_WEIGHT", "type": "float", "value": 7.7},
                    "payments": {"name": "payments", "type": "boolean", "value": False},
                    "cod": {"name": "cod", "type": "currency", "value": None},
                },
            },
            "panel_1": {
                "name": "panel_1",
                "label": "LBL_RECIPIENT",
                "fields": {
                    "recipient": {"name": "recipient", "label": "LBL_RECIPIENT", "type": "string", "value": "Jana Vzorova"},
                    "recipientPhone": {"name": "recipientPhone", "type": "string", "value": "REDACTED"},
                    "recipientMail": {"name": "recipientMail", "type": "string", "value": "REDACTED"},
                    "recipientAddress": {"name": "recipientAddress", "type": "string", "value": "REDACTED"},
                },
            },
            "panel_2": {
                "name": "panel_2",
                "label": "LBL_SENDER",
                "fields": {
                    "sender": {"name": "sender", "type": "string", "value": "Example Sender s.r.o."},
                    "ref1": {"name": "ref1", "type": "string", "value": "REDACTED"},
                },
            },
        },
        "rawData": [
            {
                "senderStreet": "REDACTED",
                "senderCity": "REDACTED",
                "senderZIP": "84105",
                "recipientStreet": "REDACTED",
                "recipientCity": "REDACTED",
                "recipientZIP": "04001",
                "status": 30,
                "statusCode": "TOUR",
                "statusDescr": "Doručovací deň",
            }
        ],
    }


def collection_sample(code: str = PICKUP_CODE) -> dict:
    """The second real capture's shape — exercises `44`/`PICK` -> `in_transit`.

    Modelled on a confirmed live payload (SK0SCCY550000009ZD, redacted/
    resynthesised): registered, collected from the sender (`PICK`), out for
    delivery, bounced to `RETC` (deliberately unmapped, not terminal), out
    for delivery again, delivered.
    """
    states = [
        event(40, "DELY", "2025-09-12 16:06:38", "Doručenie"),
        event(50, "RETC", "2025-09-11 12:54:41", "Vrátený do skladu"),
        event(30, "TOUR", "2025-09-11 08:06:40", "Doručovací deň"),
        event(10, "INIT", "2025-09-10 21:45:50", "Prvá registrácia"),
        event(44, "PICK", "2025-09-10 14:20:14", "Odnáška"),
        event(8, "DDEF", "2025-09-09 13:01:44", "Definícia dát"),
    ]
    return _envelope(code, "delivered", states)
