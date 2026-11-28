"""Tests for the Fan Thermostat config flow."""

from unittest.mock import patch

from fan_thermostat.const import DOMAIN
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant.common import MockConfigEntry


async def test_full_flow(
    hass: HomeAssistant,
) -> None:
    """Test the full config flow."""
    result: ConfigFlowResult = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch(
        "custom_components.fan_thermostat.climate.async_setup_entry",
        return_value=True,
    ) as mock_setup:
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "name": "Test Fan Thermostat",
                "temperature_sensors": ["sensor.test_temp"],
                "fan_controllers": ["fan.test_fan"],
                "motion_sensors": ["binary_sensor.test_motion"],
                "switches": [],
            },
        )
        await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["title"] == "Test Fan Thermostat"
    assert result2["data"]["name"] == "Test Fan Thermostat"
    assert result2["data"]["temperature_sensors"] == ["sensor.test_temp"]
    assert result2["data"]["fan_controllers"] == ["fan.test_fan"]
    assert result2["data"]["motion_sensors"] == ["binary_sensor.test_motion"]

    # Verify setup_entry was called
    assert mock_setup.call_count == 1


async def test_full_flow_with_switches(
    hass: HomeAssistant,
) -> None:
    """Test the full config flow with switches but no fan controllers."""
    result: ConfigFlowResult = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM

    with patch(
        "custom_components.fan_thermostat.climate.async_setup_entry",
        return_value=True,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "name": "Switch Only Thermostat",
                "temperature_sensors": ["sensor.test_temp"],
                "fan_controllers": [],
                "motion_sensors": [],
                "switches": ["switch.test_switch"],
            },
        )
        await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["data"]["switches"] == ["switch.test_switch"]


async def test_full_flow_default_values(
    hass: HomeAssistant,
) -> None:
    """Test that default values are applied correctly.

    Timer defaults (retrigger_time, motion_timeout, manual_override_time)
    are no longer stored in config entry data since they moved to number entities.
    The climate entity reads them with .get() and falls back to const defaults.
    """
    result: ConfigFlowResult = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM

    with patch(
        "custom_components.fan_thermostat.climate.async_setup_entry",
        return_value=True,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "name": "Default Values Thermostat",
                "temperature_sensors": ["sensor.test_temp"],
                "fan_controllers": ["fan.test_fan"],
                "motion_sensors": [],
                "switches": [],
            },
        )
        await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.CREATE_ENTRY


async def test_duplicate_name_abort(
    hass: HomeAssistant,
) -> None:
    """Test that duplicate names are aborted."""
    # First, set up an existing entry whose unique ID (the name) matches.
    existing = MockConfigEntry(
        domain=DOMAIN,
        title="Test Fan Thermostat",
        data={
            "name": "Test Fan Thermostat",
            "temperature_sensors": ["sensor.test_temp"],
            "fan_controllers": ["fan.test_fan"],
            "motion_sensors": [],
            "switches": [],
        },
        unique_id="Test Fan Thermostat",
    )
    existing.add_to_hass(hass)

    result: ConfigFlowResult = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Test Fan Thermostat",
            "temperature_sensors": ["sensor.test_temp"],
            "fan_controllers": ["fan.test_fan"],
            "motion_sensors": [],
            "switches": [],
        },
    )
    await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


async def test_duplicate_name_abort_different_unique_id(
    hass: HomeAssistant,
) -> None:
    """Test that a duplicate name is aborted even when the entry's unique ID differs from the name."""
    # An entry created by an older version (or by another means) whose unique ID
    # is not the name, but whose stored name matches.
    existing = MockConfigEntry(
        domain=DOMAIN,
        title="Test Fan Thermostat",
        data={
            "name": "Test Fan Thermostat",
            "temperature_sensors": ["sensor.test_temp"],
            "fan_controllers": ["fan.test_fan"],
            "motion_sensors": [],
            "switches": [],
        },
        unique_id="0f1f2a0b-1234-5678-9abc-def012345678",
    )
    existing.add_to_hass(hass)

    result: ConfigFlowResult = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Test Fan Thermostat",
            "temperature_sensors": ["sensor.test_temp"],
            "fan_controllers": ["fan.test_fan"],
            "motion_sensors": [],
            "switches": [],
        },
    )
    await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


async def test_no_temp_sensors_error(
    hass: HomeAssistant,
) -> None:
    """Test that missing temperature sensors produces an error."""
    result: ConfigFlowResult = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "No Temp Sensors",
            "temperature_sensors": [],
            "fan_controllers": ["fan.test_fan"],
            "motion_sensors": [],
            "switches": [],
        },
    )

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] is not None
    assert result2["errors"]["base"] == "no_temp_sensors"


async def test_no_fan_or_switch_error(
    hass: HomeAssistant,
) -> None:
    """Test that missing fan controllers and switches produces an error."""
    result: ConfigFlowResult = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "No Fan Or Switch",
            "temperature_sensors": ["sensor.test_temp"],
            "fan_controllers": [],
            "motion_sensors": [],
            "switches": [],
        },
    )

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] is not None
    assert result2["errors"]["base"] == "no_fan_or_switch"


async def test_empty_name_error(
    hass: HomeAssistant,
) -> None:
    """Test that an empty name produces an error."""
    result: ConfigFlowResult = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "   ",
            "temperature_sensors": ["sensor.test_temp"],
            "fan_controllers": ["fan.test_fan"],
            "motion_sensors": [],
            "switches": [],
        },
    )

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] is not None
    assert result2["errors"]["name"] == "name_required"


async def test_name_is_stripped(
    hass: HomeAssistant,
) -> None:
    """Test that the name is stripped of surrounding whitespace."""
    result: ConfigFlowResult = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM

    with patch(
        "custom_components.fan_thermostat.climate.async_setup_entry",
        return_value=True,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "name": "  Padded Name  ",
                "temperature_sensors": ["sensor.test_temp"],
                "fan_controllers": ["fan.test_fan"],
                "motion_sensors": [],
                "switches": [],
            },
        )
        await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["title"] == "Padded Name"
    assert result2["data"]["name"] == "Padded Name"
