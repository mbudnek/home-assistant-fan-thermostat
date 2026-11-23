"""Tests for the Fan Thermostat integration setup."""

from pytest_homeassistant.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from . import setup_integration


async def test_setup_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test setting up config entry."""
    await setup_integration(hass, mock_config_entry)

    # Check that the climate entity was set up
    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.state == "fan_only"

    # Check that the switch entity was set up
    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state is not None
    assert switch_state.state == "off"


async def test_unload_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test unloading config entry."""
    await setup_integration(hass, mock_config_entry)

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None

    # Unload the entry
    assert await hass.config_entries.async_unload(
        mock_config_entry.entry_id
    )
    await hass.async_block_till_done()

    # After unload, entities are restored to unavailable (restore_state behavior)
    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.state == "unavailable"
