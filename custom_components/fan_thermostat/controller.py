"""Shared runtime state for a Fan Thermostat config entry."""

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .climate import FanThermostatClimate
    from .switch import FanThermostatManualOverride


class FanThermostatController:
    """Shared reference between the climate entity and the manual override switch.

    Platforms are set up concurrently (climate, number, switch), so neither
    entity can reliably rely on the other being registered at setup time.
    Both entities register themselves here as they are added; each side then
    syncs state from the other when it becomes available.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the controller."""
        self.config_entry = config_entry
        self.climate: FanThermostatClimate | None = None
        self.switch: FanThermostatManualOverride | None = None
