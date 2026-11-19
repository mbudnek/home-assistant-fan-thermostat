"""Tests for the Fan Thermostat config flow."""

from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant.common import MockConfigEntry
from homeassistant.data_entry_flow import FlowResultType

from homeassistant import config_entries
from fan_thermostat.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from . import setup_integration


async def test_full_flow(
    hass: HomeAssistant,
) -> None:
    """Test the full config flow."""
    result: FlowResult = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch(
        "fan_thermostat.config_flow.FanThermostatConfigFlow.async_set_unique_id",
        return_value=None,
    ), patch(
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
    result: FlowResult = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM

    with patch(
        "fan_thermostat.config_flow.FanThermostatConfigFlow.async_set_unique_id",
        return_value=None,
    ), patch(
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
    result: FlowResult = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM

    with patch(
        "fan_thermostat.config_flow.FanThermostatConfigFlow.async_set_unique_id",
        return_value=None,
    ), patch(
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
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that duplicate names are aborted."""
    # First, set up an existing entry
    mock_config_entry.add_to_hass(hass)

    result: FlowResult = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    with patch(
        "fan_thermostat.config_flow.FanThermostatConfigFlow.async_set_unique_id",
        return_value=mock_config_entry,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "name": mock_config_entry.data["name"],
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
    result: FlowResult = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    with patch(
        "fan_thermostat.config_flow.FanThermostatConfigFlow.async_set_unique_id",
        return_value=None,
    ):
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
    assert result2["errors"]["base"] == "no_temp_sensors"


async def test_no_fan_or_switch_error(
    hass: HomeAssistant,
) -> None:
    """Test that missing fan controllers and switches produces an error."""
    result: FlowResult = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    with patch(
        "fan_thermostat.config_flow.FanThermostatConfigFlow.async_set_unique_id",
        return_value=None,
    ):
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
    assert result2["errors"]["base"] == "no_fan_or_switch"
