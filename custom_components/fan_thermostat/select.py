"""Select platform for the Fan Thermostat temperature aggregation."""

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .controller import FanThermostatController

_LOGGER = logging.getLogger(__name__)

TEMPERATURE_OPERATION_OPTIONS = ["min", "max", "avg"]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Fan Thermostat temperature operation select."""
    temperature_sensor_ids: list[str] = config_entry.data["temperature_sensors"]
    # Only offer the aggregation option when multiple sensors are configured,
    # matching the original Hubitat app.
    if len(temperature_sensor_ids) < 2:
        return

    entity = FanThermostatTemperatureOperation(config_entry=config_entry)
    async_add_entities([entity])


class FanThermostatTemperatureOperation(SelectEntity):
    """Aggregation used to combine multiple temperature sensors."""

    _attr_has_entity_name = True
    _attr_translation_key = "temperature_operation"
    _attr_options = TEMPERATURE_OPERATION_OPTIONS

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the temperature operation select."""
        self._config_entry = config_entry
        base_uid = config_entry.unique_id or config_entry.entry_id
        self._attr_unique_id = f"{base_uid}_temperature_operation"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=config_entry.data.get("name", "Fan Thermostat"),
            manufacturer="Fan Thermostat",
            model="Configuration",
        )

    @property
    def current_option(self) -> str:
        """Return the configured aggregation."""
        return self._config_entry.data.get("temperature_operation", "max")

    async def async_select_option(self, option: str) -> None:
        """Update the configured aggregation."""
        if option not in TEMPERATURE_OPERATION_OPTIONS:
            _LOGGER.warning("Ignoring unknown temperature operation %s", option)
            return

        new_data = {**self._config_entry.data}
        new_data["temperature_operation"] = option
        self.hass.config_entries.async_update_entry(
            self._config_entry, data=new_data
        )

        controller = self._get_controller()
        if controller is not None and controller.climate is not None:
            controller.climate.handle_settings_changed("temperature_operation")

    def _get_controller(self) -> FanThermostatController | None:
        """Return the shared controller for this config entry, if available."""
        data = self.hass.data.get(DOMAIN)
        if not isinstance(data, dict):
            return None
        controller = data.get(self._config_entry.entry_id)
        if isinstance(controller, FanThermostatController):
            return controller
        return None
