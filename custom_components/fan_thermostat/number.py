"""Number platform for Fan Thermostat configuration."""

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    DEFAULT_RETRIGGER_TIME,
    DEFAULT_MOTION_TIMEOUT,
    DEFAULT_MANUAL_OVERRIDE_TIME,
    DOMAIN,
)
from .controller import FanThermostatController

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Fan Thermostat number entities."""
    
    retrigger_entity = FanThermostatNumberEntity(
        config_entry=config_entry,
        key="retrigger_time",
        default_value=DEFAULT_RETRIGGER_TIME,
        min_value=0,
        max_value=3600,
        step=10,
    )

    motion_timeout_entity = FanThermostatNumberEntity(
        config_entry=config_entry,
        key="motion_timeout",
        default_value=DEFAULT_MOTION_TIMEOUT,
        min_value=0,
        max_value=3600,
        step=10,
    )

    override_time_entity = FanThermostatNumberEntity(
        config_entry=config_entry,
        key="manual_override_time",
        default_value=DEFAULT_MANUAL_OVERRIDE_TIME,
        min_value=0,
        max_value=86400,
        step=60,
    )

    async_add_entities([retrigger_entity, motion_timeout_entity, override_time_entity])


class FanThermostatNumberEntity(NumberEntity):
    """Representation of a configuration number entity."""

    _attr_mode = NumberMode.BOX
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    def __init__(
        self,
        config_entry: ConfigEntry,
        key: str,
        default_value: int,
        min_value: float,
        max_value: float,
        step: float,
    ) -> None:
        """Initialize the number entity."""
        self._config_entry = config_entry
        self._key = key
        self._attr_translation_key = key
        self._default_value = default_value
        self._min_value = min_value
        self._max_value = max_value
        self._step = step
        
        # Use entry_id if unique_id is not set to ensure uniqueness
        base_uid = config_entry.unique_id or config_entry.entry_id
        self._attr_unique_id = f"{base_uid}_{self._key}"

        # Use the device name (config entry title), not the entity name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=config_entry.data.get("name", "Fan Thermostat"),
            manufacturer="Fan Thermostat",
            model="Configuration",
        )

    @property
    def native_min_value(self) -> float:
        return self._min_value

    @property
    def native_max_value(self) -> float:
        return self._max_value

    @property
    def native_step(self) -> float:
        return self._step

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {"key": self._key}

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        # Initialize value from config entry or default
        current_val = self._config_entry.data.get(self._key, self._default_value)
        self._attr_native_value = int(current_val)

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value with bounds validation."""
        # Validate against min/max values
        if value < self._min_value or value > self._max_value:
            _LOGGER.warning(
                "Value %s for %s out of range [%d, %d]",
                value, self._key, int(self._min_value), int(self._max_value)
            )
            return

        new_data = {**self._config_entry.data}
        new_data[self._key] = int(value)

        # Update config entry in storage and memory
        self.hass.config_entries.async_update_entry(
            self._config_entry,
            data=new_data
        )

        # Update internal state immediately so UI reflects change without reload
        self._attr_native_value = int(value)

        # Adjust any pending timers to the new value
        controller = self._get_controller()
        if controller is not None and controller.climate is not None:
            controller.climate.handle_settings_changed(self._key)

    def _get_controller(self) -> FanThermostatController | None:
        """Return the shared controller for this config entry, if available."""
        data = self.hass.data.get(DOMAIN)
        if not isinstance(data, dict):
            return None
        controller = data.get(self._config_entry.entry_id)
        if isinstance(controller, FanThermostatController):
            return controller
        return None
