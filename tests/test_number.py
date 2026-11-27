"""Tests for the Fan Thermostat number platform."""

import pytest
from fan_thermostat.number import FanThermostatNumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant.common import MockConfigEntry

from . import setup_integration


async def test_number_rejects_out_of_range_direct_call(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Direct entity calls reject out-of-range values instead of silently dropping them."""
    hass.states.async_set("sensor.bedroom_temp", "72.0", {"device_class": "temperature"})
    await setup_integration(hass, mock_config_entry)

    entity = FanThermostatNumberEntity(
        config_entry=mock_config_entry,
        key="retrigger_time",
        default_value=300,
        min_value=0,
        max_value=3600,
        step=10,
    )
    entity.hass = hass

    with pytest.raises(ServiceValidationError):
        await entity.async_set_native_value(99999)

    with pytest.raises(ServiceValidationError):
        await entity.async_set_native_value(-1)

    # The out-of-range values must not be persisted.
    assert mock_config_entry.data.get("retrigger_time") == 300


async def test_number_accepts_in_range_direct_call(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Direct entity calls update the value within the range."""
    hass.states.async_set("sensor.bedroom_temp", "72.0", {"device_class": "temperature"})
    await setup_integration(hass, mock_config_entry)

    entity = FanThermostatNumberEntity(
        config_entry=mock_config_entry,
        key="retrigger_time",
        default_value=300,
        min_value=0,
        max_value=3600,
        step=10,
    )
    entity.hass = hass

    await entity.async_set_native_value(120)

    assert mock_config_entry.data["retrigger_time"] == 120
    assert entity.native_value == 120


async def test_number_device_info_uses_shared_model(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """The configuration number device reports the same model as the climate and switch."""
    hass.states.async_set("sensor.bedroom_temp", "72.0", {"device_class": "temperature"})
    await setup_integration(hass, mock_config_entry)

    device = device_registry.async_get_device_by_identifier(
        ("fan_thermostat", mock_config_entry.entry_id), mock_config_entry.entry_id
    )
    assert device is not None
    assert device.model == "Fan Thermostat"
