"""Tests for the Fan Thermostat select platform."""

import pytest
from fan_thermostat.select import FanThermostatTemperatureOperation
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant.common import MockConfigEntry

from . import setup_integration


async def test_select_rejects_unknown_option_direct_call(
    hass: HomeAssistant,
    mock_config_entry_multiple_sensors: MockConfigEntry,
) -> None:
    """Direct entity calls reject unknown options instead of silently dropping them."""
    hass.states.async_set("sensor.living_room_temp_1", "72.0", {"device_class": "temperature"})
    hass.states.async_set("sensor.living_room_temp_2", "73.0", {"device_class": "temperature"})
    await setup_integration(hass, mock_config_entry_multiple_sensors)

    entity = FanThermostatTemperatureOperation(config_entry=mock_config_entry_multiple_sensors)
    entity.hass = hass

    with pytest.raises(ServiceValidationError):
        await entity.async_select_option("bogus")

    # The rejected value must not be persisted.
    assert "temperature_operation" not in mock_config_entry_multiple_sensors.data


async def test_select_accepts_known_option_direct_call(
    hass: HomeAssistant,
    mock_config_entry_multiple_sensors: MockConfigEntry,
) -> None:
    """Direct entity calls update the aggregation for a known option."""
    hass.states.async_set("sensor.living_room_temp_1", "72.0", {"device_class": "temperature"})
    hass.states.async_set("sensor.living_room_temp_2", "73.0", {"device_class": "temperature"})
    await setup_integration(hass, mock_config_entry_multiple_sensors)

    entity = FanThermostatTemperatureOperation(config_entry=mock_config_entry_multiple_sensors)
    entity.hass = hass

    await entity.async_select_option("avg")

    assert mock_config_entry_multiple_sensors.data["temperature_operation"] == "avg"


async def test_select_device_info_uses_shared_model(
    hass: HomeAssistant,
    mock_config_entry_multiple_sensors: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """The temperature aggregation device reports the same model as the climate and switch."""
    hass.states.async_set("sensor.living_room_temp_1", "72.0", {"device_class": "temperature"})
    hass.states.async_set("sensor.living_room_temp_2", "73.0", {"device_class": "temperature"})
    await setup_integration(hass, mock_config_entry_multiple_sensors)

    device = device_registry.async_get_device_by_identifier(
        ("fan_thermostat", mock_config_entry_multiple_sensors.entry_id),
        mock_config_entry_multiple_sensors.entry_id,
    )
    assert device is not None
    assert device.model == "Fan Thermostat"
