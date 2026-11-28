"""Config flow for the Fan Thermostat integration."""

import logging
from typing import Any

import probatio
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    Selector,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _create_entity_selector(domain_filter: str | None = None) -> Selector[EntitySelectorConfig]:
    """Create a multi-entity selector with an optional domain filter."""
    config = EntitySelectorConfig(multiple=True)
    if domain_filter:
        config["domain"] = domain_filter
    return EntitySelector(config)


class FanThermostatConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Fan Thermostat."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=self._get_schema(),
            )

        # Validate inputs
        errors: dict[str, str] = {}

        name = user_input.get("name", "").strip()
        if not name:
            errors["name"] = "name_required"

        temperature_sensors = user_input.get("temperature_sensors", [])
        if not temperature_sensors:
            errors["base"] = "no_temp_sensors"

        fan_controllers = user_input.get("fan_controllers", [])
        switches = user_input.get("switches", [])
        if not fan_controllers and not switches:
            errors["base"] = "no_fan_or_switch"

        if errors:
            return self.async_show_form(
                step_id="user",
                data_schema=self._get_schema(),
                errors=errors,
            )

        # Use the name as the unique ID so duplicate names are detected by the
        # standard config-entry uniqueness check.
        await self.async_set_unique_id(name)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=name,
            data={
                **user_input,
                "name": name,
            },
        )

    def _get_schema(self) -> probatio.Schema:
        """Get the default schema for the config flow."""
        return probatio.Schema(
            {
                probatio.Required("name"): str,
                probatio.Required("temperature_sensors"): _create_entity_selector("sensor"),
                probatio.Optional("fan_controllers"): _create_entity_selector("fan"),
                probatio.Optional("motion_sensors"): _create_entity_selector("binary_sensor"),
                probatio.Optional("switches"): _create_entity_selector("switch"),
            }
        )
