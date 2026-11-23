"""Switch platform for the Fan Thermostat manual override.

The manual override switch allows the user to temporarily disable
automatic fan control. When the switch is turned on, the climate entity
stops controlling the fans and waits for the override to expire or
be manually cleared.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    DOMAIN,
    get_device_info,
)
from .controller import FanThermostatController

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Fan Thermostat switch entity."""
    name: str = config_entry.data["name"]

    entity = FanThermostatManualOverride(
        config_entry=config_entry,
        name=name,
    )
    async_add_entities([entity])


class FanThermostatManualOverride(SwitchEntity, RestoreEntity):
    """Representation of a Fan Thermostat manual override switch.

    This switch temporarily disables automatic fan control when turned on.
    It can auto-expire after the configured override time (managed by the
    climate entity), or be manually turned off to resume automatic control.
    """

    _attr_name = "Manual Override"
    _attr_has_entity_name = True

    def __init__(
        self,
        config_entry: ConfigEntry,
        name: str,
    ) -> None:
        """Initialize the manual override switch."""
        super().__init__()
        self._config_entry = config_entry
        uid = config_entry.unique_id or config_entry.entry_id
        self._attr_unique_id = f"{uid}_override"
        self._attr_device_info = self._get_device_info(config_entry, name)
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
        # Register with the shared controller. Platforms are set up concurrently,
        # so the climate entity may not be registered yet.
        controller = self._get_controller()
        if controller is not None:
            controller.switch = self
            # If the climate entity was already registered and is in manual
            # override, sync to it (and keep the switch consistent).
            if controller.climate is not None:
                self._is_on = controller.climate.manual_override_active
        # Restore last state
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state == "on":
            self._is_on = True
            self._notify_climate()

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass."""
        controller = self._get_controller()
        if controller is not None and controller.switch is self:
            controller.switch = None
        await super().async_will_remove_from_hass()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on manual override to disable automatic fan control."""
        self._is_on = True
        self._notify_climate()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off manual override to resume automatic fan control."""
        self._is_on = False
        self._notify_climate()
        self.async_write_ha_state()

    def sync_state(self, on: bool) -> None:
        """Silently set the switch state without notifying the climate entity."""
        if self._is_on == on:
            return
        self._is_on = on
        self.async_write_ha_state()

    def _notify_climate(self) -> None:
        """Push this switch's state to the climate entity."""
        controller = self._get_controller()
        if controller is None or controller.climate is None:
            return
        if self._is_on:
            controller.climate.enter_manual_override()
        else:
            controller.climate.exit_manual_override()

    def _get_controller(self) -> "FanThermostatController | None":
        """Return the shared controller for this config entry, if available."""
        data = self.hass.data.get(DOMAIN)
        if not isinstance(data, dict):
            return None
        controller = data.get(self._config_entry.entry_id)
        if isinstance(controller, FanThermostatController):
            return controller
        return None
