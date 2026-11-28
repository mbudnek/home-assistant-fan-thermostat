"""Switch platform for the Fan Thermostat manual override.

The manual override switch allows the user to temporarily disable
automatic fan control. This switch is the single owner of the override
state: it holds the on/off flag, the auto-clear timer and the
``manual_override_time`` setting. The climate entity only queries the
switch (``is_on``) to gate its control passes and resumes automatic
control when the override is cleared.

The expiry time is persisted as extra restore state data, so after a
restart the override is re-enabled with the same end time rather than a
freshly derived one.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_MANUAL_OVERRIDE_TIME,
    DOMAIN,
    get_device_info,
)
from .controller import FanThermostatController

_LOGGER = logging.getLogger(__name__)


class _OverrideEndData(ExtraStoredData):
    """Extra stored data for the manual override switch.

    Persists the time at which the override expires so a restart can
    re-enable the override with the same end time instead of re-deriving it
    from a freshly written last_changed.
    """

    def __init__(self, end: datetime) -> None:
        """Initialize the data with the persisted end time."""
        self._end = end

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation of the extra data."""
        return {"override_end": self._end.isoformat()}


def _stored_override_end(extra_data: ExtraStoredData | None) -> datetime | None:
    """Parse the persisted override end time from restored extra data."""
    if extra_data is None:
        return None
    try:
        raw = extra_data.as_dict().get("override_end")
        end = dt_util.parse_datetime(raw) if isinstance(raw, str) else None
    except Exception:
        return None
    if end is None:
        return None
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return end


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

    # A disabled entity is never added, so async_added_to_hass never runs and
    # the climate's first control pass would wait forever for the switch to
    # become ready. The disabled switch holds no override state, so mark the
    # controller ready right away.
    if entity.unique_id is not None:
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id("switch", DOMAIN, entity.unique_id)
        if entity_id is not None:
            registry_entry = registry.async_get(entity_id)
            if registry_entry is not None and registry_entry.disabled:
                controller = _get_controller(hass, config_entry.entry_id)
                if controller is not None:
                    controller.mark_switch_ready()


def _get_controller(hass: HomeAssistant, entry_id: str) -> FanThermostatController | None:
    """Return the shared controller for a config entry, if registered."""
    data = hass.data.get(DOMAIN)
    if not isinstance(data, dict):
        return None
    controller = data.get(entry_id)
    return controller if isinstance(controller, FanThermostatController) else None


class FanThermostatManualOverride(SwitchEntity, RestoreEntity):
    """Representation of a Fan Thermostat manual override switch.

    This switch temporarily disables automatic fan control when turned on.
    The override auto-expires after the configured ``manual_override_time``
    (0 disables auto-expiry) or is manually cleared, either way by turning
    the switch off. Expiry and manual clear both resume automatic control
    via the climate entity.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "manual_override"

    def __init__(
        self,
        config_entry: ConfigEntry,
        name: str,
    ) -> None:
        """Initialize the manual override switch."""
        super().__init__()
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.unique_id}_override"
        self._attr_device_info = get_device_info(config_entry, name)
        self._is_on = False
        self._auto_clear_cancel: Callable[[], None] | None = None
        self._override_end: datetime | None = None

    @property
    def _manual_override_time(self) -> int:
        """Return current manual override time from config entry."""
        return self._config_entry.data.get("manual_override_time", DEFAULT_MANUAL_OVERRIDE_TIME)

    @property
    def is_on(self) -> bool:
        """Return true if manual override is active."""
        return self._is_on

    @property
    def extra_restore_state_data(self) -> ExtraStoredData | None:
        """Return the persisted override end time, or None when not active."""
        if not self._is_on or self._override_end is None:
            return None
        return _OverrideEndData(self._override_end)

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        # Register with the shared controller. Platforms are set up concurrently,
        # so the climate entity may not be registered yet.
        controller = self._get_controller()
        if controller is not None:
            controller.switch = self
        try:
            await self._restore_override(controller)
        finally:
            # The climate's first control pass runs once the switch is ready,
            # so the restored override is in place before the fans are
            # commanded. Mark the switch ready even if the restore raised so
            # the climate is never stuck waiting for it.
            if controller is not None:
                controller.mark_switch_ready()

    async def _restore_override(self, controller: FanThermostatController | None) -> None:
        """Restore the manual override from the last state.

        If the switch was on at shutdown, restore the override and re-arm the
        auto-clear timer. The persisted end time is honored exactly - the
        override is re-enabled with that same end time, so it must not outlive
        the configured duration no matter how many restarts happened in
        between. Only the time remaining at restart is re-armed and an
        override that already expired while the instance was down is not
        restored at all. When no end time is persisted the auto-expiry is
        disabled, so the override is restored without a timer. The climate
        entity queries this switch's state for its control passes, so it
        picks up the restored override automatically.
        """
        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state != "on":
            return

        end = _stored_override_end(await self.async_get_last_extra_data())
        if end is None:
            # Auto-expiry disabled: restore the override without a timer.
            self._is_on = True
            self._notify_climate()
            return

        remaining = (end - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            # The override expired while the instance was down: do not
            # restore it.
            return
        self._is_on = True
        self._schedule_auto_clear(remaining, end=end)
        self._notify_climate()

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass."""
        self._cancel_auto_clear()
        controller = self._get_controller()
        if controller is not None and controller.switch is self:
            controller.switch = None
        await super().async_will_remove_from_hass()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on manual override to disable automatic fan control."""
        self.enter()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off manual override to resume automatic fan control."""
        self.exit()

    def enter(self, context: Context | None = None) -> None:
        """Enter manual override: record the override and arm the auto-clear timer.

        ``context``, when given, is attached to the state write before it is
        written, so the Activity details trace links the triggering event
        (e.g. an external change) to the visible override transition.
        """
        if self._is_on:
            return
        self._is_on = True
        self._schedule_auto_clear()
        if context is not None:
            self.async_set_context(context)
        self.async_write_ha_state()
        self._notify_climate()

    def exit(self) -> None:
        """Exit manual override: clear the override and resume automatic control."""
        if not self._is_on:
            return
        self._is_on = False
        self._cancel_auto_clear()
        self._override_end = None
        self.async_write_ha_state()
        self._notify_climate()

    def handle_settings_changed(self, key: str) -> None:
        """Re-arm the auto-clear timer after the override time changed."""
        if key == "manual_override_time" and self._is_on:
            self._reschedule_auto_clear()

    def _schedule_auto_clear(self, delay: float | None = None, end: datetime | None = None) -> None:
        """Schedule the automatic clear of manual override.

        ``delay`` overrides the configured manual_override_time for this
        timer (used when restoring an override: only the time remaining at
        shutdown is re-armed). ``end``, when given, is stored as the
        persisted override end time instead of being derived from ``delay``.
        """
        duration = self._manual_override_time if delay is None else delay
        if duration <= 0:
            self._override_end = None
            return
        self._cancel_auto_clear()
        self._auto_clear_cancel = async_call_later(
            self.hass,
            duration,
            self._auto_clear_callback,
        )
        self._override_end = end if end is not None else datetime.now(UTC) + timedelta(seconds=duration)

    def _reschedule_auto_clear(self) -> None:
        """Re-arm the auto-clear timer with the current time."""
        self._cancel_auto_clear()
        self._schedule_auto_clear()

    def _cancel_auto_clear(self) -> None:
        """Cancel the auto-clear timer."""
        if self._auto_clear_cancel is not None:
            self._auto_clear_cancel()
            self._auto_clear_cancel = None

    @callback
    def _auto_clear_callback(self, _time: datetime) -> None:
        """The override time elapsed: clear the override and resume control."""
        self._auto_clear_cancel = None
        self.exit()

    def _notify_climate(self) -> None:
        """Push this switch's state to the climate entity."""
        controller = self._get_controller()
        if controller is None or controller.climate is None:
            return
        controller.climate._on_switch_override_changed(self._is_on)

    def _get_controller(self) -> FanThermostatController | None:
        """Return the shared controller for this config entry, if available."""
        return _get_controller(self.hass, self._config_entry.entry_id)
