"""Config flow for the Fan Thermostat integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
)

from .const import DOMAIN
import uuid

_LOGGER = logging.getLogger(__name__)


def _create_entity_selector(
    domain_filter: str | None = None,
    entity_category_filter: EntityCategory | None = None,
) -> EntitySelector:
    """Create an entity selector with optional filters."""
    config: dict[str, Any] = {"multiple": True}
    if domain_filter:
        config["domain"] = domain_filter
    if entity_category_filter:
        config["entity_category"] = entity_category_filter
    return EntitySelector(config)


class FanThermostatConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Fan Thermostat."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._config_data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=self._get_schema(),
            )

        self._config_data.update(user_input)

        # Validate inputs
        errors: dict[str, str] = {}

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

        # Check for duplicate names among existing entries (FIXED from broken random UUID)
        for existing_entry in self.hass.config_entries.async_entries(DOMAIN):
            if existing_entry.data.get("name") == user_input["name"]:
                return self.async_abort(reason="already_configured")

        # Generate a unique ID for this new entry
        new_unique_id = str(uuid.uuid4())
        await self.async_set_unique_id(new_unique_id)

        return self.async_create_entry(
            title=user_input["name"],
            data=user_input,
        )

    def _get_schema(self) -> vol.Schema:
        """Get the default schema for the config flow."""
        schema = vol.Schema(
            {
                vol.Required("name"): str,
                vol.Required("temperature_sensors"): _create_entity_selector(
                    domain_filter="sensor"
                ),
                vol.Required("fan_controllers"): _create_entity_selector(
                    domain_filter="fan"
                ),
                vol.Optional("motion_sensors"): _create_entity_selector(
                    domain_filter="binary_sensor"
                ),
                vol.Optional("switches"): _create_entity_selector(
                    domain_filter="switch"
                ),

            }
        )
        return schema
