"""Tests for the Slovak Parcel Service config and options flow."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.slovak_parcel_service.config_flow import (
    normalize_postcode,
    normalize_tracking_code,
    valid_postcode,
    valid_tracking_code,
)
from custom_components.slovak_parcel_service.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DOMAIN,
)


def test_normalize_tracking_code_strips_and_uppercases():
    assert normalize_tracking_code("example 123-456") == "EXAMPLE123456"
    assert normalize_tracking_code("") == ""
    assert normalize_tracking_code(None) == ""


def test_valid_tracking_code_accepts_any_non_empty_code():
    assert valid_tracking_code("EXAMPLE123456")
    assert valid_tracking_code("ABC")
    assert valid_tracking_code("A" * 31)
    assert not valid_tracking_code("")


def test_normalize_postcode_strips_the_space():
    assert normalize_postcode("841 05") == "84105"
    assert normalize_postcode("84105") == "84105"
    assert normalize_postcode("") == ""
    assert normalize_postcode(None) == ""


def test_valid_postcode_wants_five_digits():
    assert valid_postcode("84105")
    assert not valid_postcode("8410")
    assert not valid_postcode("841 05")  # not normalized first
    assert not valid_postcode("ABCDE")
    assert not valid_postcode("")


async def test_user_flow_needs_a_postcode(hass):
    """No account, but the delivery postcode is required up front."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_POSTAL_CODE: "841 05"}
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "Slovak Parcel Service (84105)"
    assert result["options"][CONF_PARCELS] == []
    assert result["options"][CONF_POSTAL_CODE] == "84105"


async def test_user_flow_rejects_an_invalid_postcode(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_POSTAL_CODE: "not-a-postcode"}
    )
    assert result["type"] == "form"
    assert result["errors"] == {CONF_POSTAL_CODE: "invalid_postcode"}


async def test_same_postcode_hub_rejected(hass):
    """A second hub for the same postcode aborts — it is the unique_id."""
    MockConfigEntry(domain=DOMAIN, unique_id="84105").add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_POSTAL_CODE: "841 05"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_second_hub_different_postcode_allowed(hass):
    """A hub for a different postcode is allowed (e.g. home + work)."""
    MockConfigEntry(domain=DOMAIN, unique_id="84105").add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_POSTAL_CODE: "04001"}
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "Slovak Parcel Service (04001)"
    assert result["options"][CONF_POSTAL_CODE] == "04001"


def _hub(parcels: list[dict], postal_code: str = "84105") -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=postal_code,
        options={CONF_PARCELS: parcels, CONF_POSTAL_CODE: postal_code},
    )


def _settings_input(
    *,
    history=False,
    filter_type="days",
    amount=7,
) -> dict:
    """Build the settings-form submission."""
    return {
        CONF_DELIVERED_FILTER_TYPE: filter_type,
        CONF_DELIVERED_FILTER_AMOUNT: amount,
        CONF_INCLUDE_HISTORY: history,
    }


async def _open_options_step(hass, entry, step_id: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert result["menu_options"] == ["parcels", "settings"]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step_id}
    )


async def test_options_add_parcel(hass):
    entry = _hub([])
    entry.add_to_hass(hass)

    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["example123456"]}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: "EXAMPLE123456"}]


async def test_options_add_code_with_separators(hass):
    """Pasted codes with spaces/dashes are sanitised like the consumer site."""
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["example-123 456"]}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: "EXAMPLE123456"}]


async def test_options_accepts_any_non_empty_code(hass):
    """A short/odd-shaped code is accepted — formats vary too much to gate on."""
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["abc"]}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: "ABC"}]


async def test_options_de_duplicates_tracking_codes(hass):
    entry = _hub([{CONF_TRACKING_CODE: "EXAMPLE111111"}])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["EXAMPLE111111", "example111111"]}
    )
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: "EXAMPLE111111"}]


async def test_options_remove_parcel(hass):
    entry = _hub(
        [
            {CONF_TRACKING_CODE: "EXAMPLE111111"},
            {CONF_TRACKING_CODE: "EXAMPLE222222"},
        ]
    )
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["EXAMPLE222222"]}
    )
    assert result["type"] == "create_entry"
    codes = {p[CONF_TRACKING_CODE] for p in result["data"][CONF_PARCELS]}
    assert codes == {"EXAMPLE222222"}


async def test_options_can_clear_the_tracked_code_list(hass):
    entry = _hub([{CONF_TRACKING_CODE: "EXAMPLE111111"}])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": []}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == []


async def test_options_changes_history_and_delivered(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "settings")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _settings_input(
            history=True,
            filter_type="parcels",
            amount=5,
        ),
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_INCLUDE_HISTORY] is True
    assert result["data"][CONF_DELIVERED_FILTER_TYPE] == "parcels"
    assert result["data"][CONF_DELIVERED_FILTER_AMOUNT] == 5


async def test_options_settings_does_not_touch_the_postcode(hass):
    """The postcode is fixed at setup — it is the hub's identity key — and
    is not offered on the settings step at all."""
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "settings")
    assert CONF_POSTAL_CODE not in result["data_schema"].schema
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _settings_input()
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_POSTAL_CODE] == "84105"


async def test_options_parcels_step_preserves_the_postcode(hass):
    """Editing the tracked-code list must not drop the hub's postcode."""
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["EXAMPLE123456"]}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_POSTAL_CODE] == "84105"
