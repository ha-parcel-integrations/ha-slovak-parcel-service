"""Services for the Slovak Parcel Service parcel tracker integration.

`slovak_parcel_service.track_parcel` / `slovak_parcel_service.untrack_parcel` let you add or remove a
tracked parcel without opening the integration options — so a Lovelace button
can start tracking a parcel straight from a dashboard.
`slovak_parcel_service.update_contents` sets or clears the text describing what
is in a parcel, tracked or not, on the hub(s) that list the code.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .config_flow import (
    normalize_postcode,
    normalize_tracking_code,
    valid_tracking_code,
)
from .const import (
    CONF_CONFIG_ENTRY_ID,
    CONF_CONTENTS,
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DOMAIN,
)
from .contents import ContentsStore, normalize_code

SERVICE_TRACK_PARCEL = "track_parcel"
SERVICE_UNTRACK_PARCEL = "untrack_parcel"
SERVICE_UPDATE_CONTENTS = "update_contents"

_TRACK_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TRACKING_CODE): cv.string,
        vol.Optional(CONF_POSTAL_CODE): cv.string,
        vol.Optional(CONF_CONTENTS): cv.string,
    }
)
_UNTRACK_SCHEMA = vol.Schema({vol.Required(CONF_TRACKING_CODE): cv.string})
_UPDATE_CONTENTS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TRACKING_CODE): cv.string,
        vol.Optional(CONF_CONTENTS): cv.string,
        vol.Optional(CONF_CONFIG_ENTRY_ID): cv.string,
    }
)


def _resolve_entry(hass: HomeAssistant, postal_code: str | None):
    """Pick the Slovak Parcel Service hub to act on.

    With one hub, that hub. With several, the ``postal_code`` argument
    selects it; if omitted and ambiguous, raise so the caller knows to
    specify one.
    """
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("Slovak Parcel Service is not set up")
    if postal_code:
        target = normalize_postcode(postal_code)
        for entry in entries:
            if entry.options.get(CONF_POSTAL_CODE) == target:
                return entry
        raise ServiceValidationError(
            f"No Slovak Parcel Service hub for postal code {target}"
        )
    if len(entries) == 1:
        return entries[0]
    raise ServiceValidationError(
        "Multiple Slovak Parcel Service hubs are set up — pass postal_code to choose one"
    )


def _loaded_coordinator(entry):
    """Return the coordinator of a set-up entry, or raise when it is not loaded."""
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError("Slovak Parcel Service is not loaded")
    return entry.runtime_data.coordinator


def _contents_store(entry) -> ContentsStore | None:
    """Return the entry's contents store, or ``None`` when it is not loaded."""
    if entry.state is not ConfigEntryState.LOADED:
        return None
    return entry.runtime_data.coordinator.contents_store


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Slovak Parcel Service services (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_TRACK_PARCEL):
        return

    async def _track(call: ServiceCall) -> None:
        tracking_code = normalize_tracking_code(call.data[CONF_TRACKING_CODE])
        if not valid_tracking_code(tracking_code):
            raise ServiceValidationError(
                f"'{tracking_code}' is not a valid Slovak Parcel Service tracking code"
            )
        entry = _resolve_entry(hass, call.data.get(CONF_POSTAL_CODE))

        has_contents = CONF_CONTENTS in call.data
        coordinator = _loaded_coordinator(entry) if has_contents else None

        parcels = [dict(p) for p in entry.options.get(CONF_PARCELS, [])]
        if any(p[CONF_TRACKING_CODE] == tracking_code for p in parcels):
            # Already tracked: only a changed contents value does anything.
            if coordinator is not None and coordinator.contents_store.async_set(
                tracking_code, call.data[CONF_CONTENTS]
            ):
                coordinator.async_reapply_contents()
            return
        if coordinator is not None:
            # Stored before the options write, whose listener refreshes.
            coordinator.contents_store.async_set(
                tracking_code, call.data[CONF_CONTENTS]
            )
        parcels.append({CONF_TRACKING_CODE: tracking_code})
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_PARCELS: parcels}
        )

    async def _untrack(call: ServiceCall) -> None:
        tracking_code = normalize_tracking_code(call.data[CONF_TRACKING_CODE])
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError("Slovak Parcel Service is not set up")
        # Remove the parcel from whichever hub(s) track it.
        for entry in entries:
            if (store := _contents_store(entry)) is not None:
                store.async_set(tracking_code, None)
            current = entry.options.get(CONF_PARCELS, [])
            kept = [p for p in current if p[CONF_TRACKING_CODE] != tracking_code]
            if len(kept) != len(current):
                hass.config_entries.async_update_entry(
                    entry, options={**entry.options, CONF_PARCELS: kept}
                )

    async def _update_contents(call: ServiceCall) -> None:
        tracking_code = normalize_tracking_code(call.data[CONF_TRACKING_CODE])
        if not tracking_code:
            raise ServiceValidationError("A tracking code is required")
        loaded = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED
        ]
        if not loaded:
            raise ServiceValidationError("Slovak Parcel Service is not set up")
        if entry_id := call.data.get(CONF_CONFIG_ENTRY_ID):
            targets = [entry for entry in loaded if entry.entry_id == entry_id]
            if not targets:
                raise ServiceValidationError(
                    f"'{entry_id}' is not a loaded Slovak Parcel Service entry"
                )
        elif len(loaded) == 1:
            targets = loaded
        else:
            # A code can sit in several hubs, so every hub that lists it gets the
            # value; guessing a hub would write to the wrong one.
            key = normalize_code(tracking_code)
            targets = [
                entry
                for entry in loaded
                if key in entry.runtime_data.coordinator.known_codes
            ]
            if not targets:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="update_contents_entry_required",
                )
        for entry in targets:
            coordinator = entry.runtime_data.coordinator
            if coordinator.contents_store.async_set(
                tracking_code, call.data.get(CONF_CONTENTS)
            ):
                coordinator.async_reapply_contents()

    hass.services.async_register(
        DOMAIN, SERVICE_TRACK_PARCEL, _track, schema=_TRACK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_UNTRACK_PARCEL, _untrack, schema=_UNTRACK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_CONTENTS,
        _update_contents,
        schema=_UPDATE_CONTENTS_SCHEMA,
    )


def async_unload_services(hass: HomeAssistant) -> None:
    """Remove the Slovak Parcel Service services.

    Called from ``__init__.py`` only once the last hub has unloaded — the
    services are shared across every hub, not owned by any one of them.
    """
    for service in (
        SERVICE_TRACK_PARCEL,
        SERVICE_UNTRACK_PARCEL,
        SERVICE_UPDATE_CONTENTS,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
