"""The device every entity of this integration belongs to.

One place, because sensors, the button and the calendar must all land on the
*same* device entry — and because the account-based variant only has to change
this file to name devices per account.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo

from .const import CONF_POSTAL_CODE, DOMAIN

CONFIGURATION_URL = "https://mysps.sk/sk/tracking/"

ATTRIBUTION = "Data provided by Slovak Parcel Service"


def build_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the DeviceInfo shared by every entity of this hub.

    The postcode is part of the device name so multiple hubs (e.g. home and
    work) stay distinguishable.
    """
    postal_code = entry.options.get(CONF_POSTAL_CODE, "")
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Slovak Parcel Service ({postal_code})"
        if postal_code
        else "Slovak Parcel Service",
        manufacturer="Slovak Parcel Service",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=CONFIGURATION_URL,
    )
