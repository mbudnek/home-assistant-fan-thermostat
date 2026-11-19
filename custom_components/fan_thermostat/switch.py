"""Switch platform for the Fan Thermostat manual override.

The manual override switch allows the user to temporarily disable
automatic fan control. When the switch is turned on, the climate entity
will stop controlling the fans and wait for the override to expire or
be manually cleared.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    DEFAULT_MANUAL_OVERRIDE_TIME,
    DOMAIN,
    get_device_info,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Fan Thermostat switch entity."""
    name: str = config_entry.data["name"]
    manual_override_time: int = config_entry.data.get(
        "manual_override_time", DEFAULT_MANUAL_OVERRIDE_TIME
    )

    entity = FanThermostatManualOverride(
        config_entry=config_entry,
        name=name,
        manual_override_time=manual_override_time,
    )
    async_add_entities([entity])


class FanThermostatManualOverride(SwitchEntity, RestoreEntity):
    """Representation of a Fan Thermostat manual override switch.

    This switch temporarily disables automatic fan control when turned on.
    It can auto-expire after the configured override time, or be manually
    turned off to resume automatic control.
    """

    _attr_name = "Manual Override"
    _attr_has_entity_name = True

    def __init__(
        self,
        config_entry: ConfigEntry,
        name: str,
        manual_override_time: int,
    ) -> None:
        """Initialize the manual override switch."""
        super().__init__()
        self._config_entry = config_entry
        uid = config_entry.unique_id or config_entry.entry_id
        self._attr_unique_id = f"{uid}_override"
        self._attr_device_info = self._get_device_info(config_entry, name)
        self._manual_override_time = manual_override_time
        self._manual_override_timer_handle: asyncio.TimerHandle | None = None
        self._is_on = False

    @staticmethod
    def _get_device_info(config_entry: ConfigEntry, name: str):
        """Get device info for the thermostat."""
        return get_device_info(config_entry, name)

    @property
    def is_on(self) -> bool:
        """Return true if manual override is active."""
        return self._is_on

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return True

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        # Restore last state
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state == "on":
            self._is_on = True
            self._schedule_clear_timer()

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass."""
        self._cancel_clear_timer()
        await super().async_will_remove_from_hass()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on manual override to disable automatic fan control."""
        self._is_on = True
        self._schedule_clear_timer()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off manual override to resume automatic fan control."""
        self._is_on = False
        self._cancel_clear_timer()
        self.async_write_ha_state()

    def _schedule_clear_timer(self) -> None:
        """Schedule the automatic clear of manual override."""
        if self._manual_override_time > 0:
            self._manual_override_timer_handle = self.hass.loop.call_later(
                self._manual_override_time, self._auto_clear_sync
            )

    def _auto_clear_sync(self) -> None:
        """Synchronous wrapper for auto-clear that schedules the async work."""
        asyncio.create_task(self._auto_clear())

    async def _auto_clear(self) -> None:
        """Automatically clear the manual override."""
        self._is_on = False
        self._manual_override_timer_handle = None
        self.async_write_ha_state()

    def _cancel_clear_timer(self) -> None:
        """Cancel the auto-clear timer."""
        if self._manual_override_timer_handle is not None:
            self._manual_override_timer_handle.cancel()
            self._manual_override_timer_handle = None
