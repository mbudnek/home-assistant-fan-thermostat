"""The Fan Thermostat integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .controller import FanThermostatController

_LOGGER = logging.getLogger(__name__)

type FanThermostatConfigEntry = ConfigEntry[None]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Fan Thermostat component."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: FanThermostatConfigEntry) -> bool:
    """Set up Fan Thermostat from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = FanThermostatController(entry)
    
    # Forward to climate, number, and switch platforms
    await hass.config_entries.async_forward_entry_setups(entry, ["climate", "number", "switch"])
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: FanThermostatConfigEntry
) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["climate", "number", "switch"])
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
