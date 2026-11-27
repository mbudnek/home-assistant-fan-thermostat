"""Tests for the Fan Thermostat integration."""

from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry


async def setup_integration(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Set up the Fan Thermostat integration in the test Home Assistant instance."""
    config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
