"""Shared runtime state for a Fan Thermostat config entry."""

from collections.abc import Callable
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

    The climate's first control pass is deferred to ``mark_switch_ready``:
    the switch is the owner of the manual override and restores its state
    during setup, so the first pass must not run before the restored override
    is in place. ``add_startup_callback`` queues the callback until then (or
    runs it immediately if the switch is already ready).
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the controller."""
        self.config_entry = config_entry
        self.climate: FanThermostatClimate | None = None
        self.switch: FanThermostatManualOverride | None = None
        self._switch_ready = False
        self._startup_callbacks: list[Callable[[], None]] = []

    def mark_switch_ready(self) -> None:
        """Mark the switch as ready and run any queued startup callbacks."""
        if self._switch_ready:
            return
        self._switch_ready = True
        callbacks = self._startup_callbacks
        self._startup_callbacks = []
        for callback in callbacks:
            callback()

    def add_startup_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to run once the switch is ready.

        If the switch is already ready the callback runs immediately;
        otherwise it is called by ``mark_switch_ready``.
        """
        if self._switch_ready:
            callback()
        else:
            self._startup_callbacks.append(callback)
