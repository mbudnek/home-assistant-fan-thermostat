"""Climate platform for the Fan Thermostat integration."""

import logging
from typing import Any, Callable

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, UnitOfTemperature
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DEFAULT_MANUAL_OVERRIDE_TIME,
    DEFAULT_MOTION_TIMEOUT,
    DEFAULT_RETRIGGER_TIME,
    DEFAULT_TARGET_TEMP_LOW,
    DEFAULT_TARGET_TEMP_HIGH,
    DOMAIN,
    get_device_info,
)

from .controller import FanThermostatController

_LOGGER = logging.getLogger(__name__)


class _ControlPassContext(Context):
    """Marker subclass of Context identifying control passes initiated by the thermostat.

    The same context object is passed to every service call the pass issues and set on
    the thermostat entity itself, so the state-change event for each fan/switch change
    carries this type (see ``homeassistant.helpers.service._with_context`` and
    ``homeassistant.core.Event``). ``isinstance`` on the event's context therefore
    proves the change was self-initiated, without tracking pending ids.
    """

    __slots__ = ()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Fan Thermostat climate entity."""
    name: str = config_entry.data["name"]
    temperature_sensor_ids: list[str] = config_entry.data["temperature_sensors"]
    fan_controller_ids: list[str] = config_entry.data.get("fan_controllers", [])
    motion_sensor_ids: list[str] = config_entry.data.get("motion_sensors", [])
    switch_ids: list[str] = config_entry.data.get("switches", [])

    retrigger_time: int = config_entry.data.get(
        "retrigger_time", DEFAULT_RETRIGGER_TIME
    )
    motion_timeout: int = config_entry.data.get(
        "motion_timeout", DEFAULT_MOTION_TIMEOUT
    )
    manual_override_time: int = config_entry.data.get(
        "manual_override_time", DEFAULT_MANUAL_OVERRIDE_TIME
    )

    entity = FanThermostatClimate(
        config_entry=config_entry,
        name=name,
        temperature_sensor_ids=temperature_sensor_ids,
        fan_controller_ids=fan_controller_ids,
        motion_sensor_ids=motion_sensor_ids,
        switch_ids=switch_ids,
        retrigger_time=retrigger_time,
        motion_timeout=motion_timeout,
        manual_override_time=manual_override_time,
    )
    async_add_entities([entity])


class FanThermostatClimate(ClimateEntity, RestoreEntity):
    """Representation of a Fan Thermostat climate entity.

    This entity acts as a thermostat that controls ceiling fan speed based on
    temperature readings from configured sensors. It supports:
    - Temperature-based automatic fan speed control (linear scaling)
    - Motion sensor integration (fan only runs when motion is detected)
    - Manual override switch to temporarily disable automatic control
    - Retrigger time to prevent rapid fan cycling

    Fan speed is controlled relative to a dual temperature range:
    - Fan turns on when current temp exceeds target_temp_low
    - Reaches 100% speed at target_temp_high
    - Speed scales linearly between the two setpoints
    """

    _attr_name = None
    _attr_translation_key = "fan_thermostat"
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.FAN_ONLY]
    _attr_preset_modes = None
    _attr_native_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_min_temp = 45
    _attr_max_temp = 95
    _attr_precision = PRECISION_TENTHS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    @property
    def _temperature_operation(self) -> str:
        """Return the configured temperature aggregation (min/max/avg)."""
        return self._config_entry.data.get("temperature_operation", "max")

    @property
    def _retrigger_time(self) -> int:
        """Return current retrigger time from config entry."""
        return self._config_entry.data.get("retrigger_time", DEFAULT_RETRIGGER_TIME)

    @property
    def _motion_timeout(self) -> int:
        """Return current motion timeout from config entry."""
        return self._config_entry.data.get("motion_timeout", DEFAULT_MOTION_TIMEOUT)

    @property
    def _manual_override_time(self) -> int:
        """Return current manual override time from config entry."""
        return self._config_entry.data.get("manual_override_time", DEFAULT_MANUAL_OVERRIDE_TIME)

    def __init__(
        self,
        config_entry: ConfigEntry,
        name: str,
        temperature_sensor_ids: list[str],
        fan_controller_ids: list[str],
        motion_sensor_ids: list[str],
        switch_ids: list[str],
        retrigger_time: int,
        motion_timeout: int,
        manual_override_time: int,
    ) -> None:
        """Initialize the Fan Thermostat climate entity."""
        super().__init__()
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.unique_id}_climate" if config_entry.unique_id else f"{config_entry.entry_id}_climate"
        self._attr_device_info = self._get_device_info(config_entry, name)

        self._temperature_sensor_ids = temperature_sensor_ids
        self._fan_controller_ids = fan_controller_ids
        self._motion_sensor_ids = motion_sensor_ids
        self._switch_ids = switch_ids

        # Internal state
        self._hvac_mode: HVACMode = HVACMode.FAN_ONLY
        self._fan_percentage: int | None = None
        self._manual_override_active: bool = False
        self._motion_active: bool = False
        self._motion_sensor_active: bool = False
        self._retrigger_cancel: Callable[[], None] | None = None
        self._manual_override_cancel: Callable[[], None] | None = None
        self._motion_cancel: Callable[[], None] | None = None
        # Temperature tracking
        self._current_temperature: float | None = None
        self._target_temp_low: float = config_entry.data.get(
            "target_temp_low", DEFAULT_TARGET_TEMP_LOW
        )
        self._target_temp_high: float = config_entry.data.get(
            "target_temp_high", DEFAULT_TARGET_TEMP_HIGH
        )

    @staticmethod
    def _get_device_info(config_entry: ConfigEntry, name: str):
        """Get device info for the thermostat."""
        return get_device_info(config_entry, name)

    @property
    def hvac_mode(self) -> HVACMode:
        return self._hvac_mode

    @property
    def hvac_action(self) -> HVACAction:
        if self._hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if self._fan_percentage is not None and self._fan_percentage > 0:
            return HVACAction.FAN
        # Fan is off but mode is FAN_ONLY - within target range, so IDLE
        return HVACAction.IDLE

    @property
    def percentage(self) -> int | None:
        return self._fan_percentage

    @property
    def manual_override_active(self) -> bool:
        return self._manual_override_active

    @property
    def is_on(self) -> bool:
        return self._fan_percentage is not None and self._fan_percentage > 0

    @property
    def available(self) -> bool:
        has_seen_any = False
        for sensor_id in self._temperature_sensor_ids:
            state = self.hass.states.get(sensor_id)
            if state is not None:
                has_seen_any = True
                if state.state != "unavailable":
                    return True
        for fan_id in self._fan_controller_ids:
            state = self.hass.states.get(fan_id)
            if state is not None:
                has_seen_any = True
                if state.state != "unavailable":
                    return True
        for sw_id in self._switch_ids:
            state = self.hass.states.get(sw_id)
            if state is not None:
                has_seen_any = True
                if state.state != "unavailable":
                    return True
        if not has_seen_any:
            return True
        return False

    @property
    def native_current_temperature(self) -> float | None:
        return self._current_temperature

    @property
    def native_target_temperature(self) -> float | None:
        # For backward compatibility, return the low value when single temp is expected
        return self._target_temp_low

    @property
    def native_target_temperature_low(self) -> float | None:
        """Return the target temperature low."""
        return self._target_temp_low

    @property
    def native_target_temperature_high(self) -> float | None:
        """Return the target temperature high."""
        return self._target_temp_high

    @property
    def native_temperature_unit(self) -> str:
        return UnitOfTemperature.FAHRENHEIT

    @property
    def min_temp(self) -> float:
        return 45

    @property
    def max_temp(self) -> float:
        return 95

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return custom extra state attributes."""
        # Only include percentage when fan is actually running (>0)
        if self._fan_percentage is not None and self._fan_percentage > 0:
            return {"percentage": self._fan_percentage}
        return {}

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()

        # Register with the shared controller. Platforms are set up concurrently,
        # so the switch may be added before (or after) this entity.
        controller = self._get_controller()
        if controller is not None:
            controller.climate = self
            # If the switch was already registered and is on, resume manual override.
            if controller.switch is not None and controller.switch.is_on:
                self.enter_manual_override()

        # Restore last state
        last_state = await self.async_get_last_state()
        if last_state is not None:
            try:
                self._hvac_mode = HVACMode(last_state.state) if last_state.state else HVACMode.FAN_ONLY
            except ValueError:
                # Unavailable/unknown/invalid state at shutdown - fall back to FAN_ONLY
                self._hvac_mode = HVACMode.FAN_ONLY
            try:
                # Restore target temperature range from previous state
                saved_low = last_state.attributes.get("target_temp_low")
                if saved_low is not None:
                    self._target_temp_low = float(saved_low)
                saved_high = last_state.attributes.get("target_temp_high")
                if saved_high is not None:
                    self._target_temp_high = float(saved_high)
                # Restore fan percentage from previous state
                saved_percentage = last_state.attributes.get("percentage")
                if saved_percentage is not None:
                    # Use round() to avoid truncation of float values like 50.5 -> 50
                    self._fan_percentage = round(float(saved_percentage))
            except (TypeError, ValueError):
                pass

        # Subscribe to temperature sensors
        for sensor_id in self._temperature_sensor_ids:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, {sensor_id}, self._on_state_changed
                )
            )

        # Subscribe to fan controllers
        for fan_id in self._fan_controller_ids:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, {fan_id}, self._on_state_changed
                )
            )

        # Subscribe to motion sensors
        for sensor_id in self._motion_sensor_ids:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, {sensor_id}, self._on_state_changed
                )
            )

        # Subscribe to switches
        for sw_id in self._switch_ids:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, {sw_id}, self._on_state_changed
                )
            )

        # Initialize motion state from current sensor readings
        self._update_motion_state()

        # Initialize current temperature from sensors and control fans
        self._update_current_temperature()
        # The restored percentage is only a display value: it must not
        # influence the initial control pass (e.g. arming a retrigger window
        # for a fan that never ran this session).
        self._fan_percentage = None
        self._control_fans(self._new_trigger_context())
        self.async_write_ha_state()

    def _get_controller(self) -> FanThermostatController | None:
        """Return the shared controller for this config entry, if available."""
        data = self.hass.data.get(DOMAIN)
        if not isinstance(data, dict):
            return None
        controller = data.get(self._config_entry.entry_id)
        if isinstance(controller, FanThermostatController):
            return controller
        return None

    @callback
    def _on_state_changed(self, event) -> None:
        """Handle state change events from sensors."""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        if entity_id in self._temperature_sensor_ids:
            context = self._new_trigger_context(parent_id=event.context.id)
            self._update_current_temperature()
            self._control_fans(context)
            self.async_write_ha_state()
            return

        if entity_id in self._motion_sensor_ids:
            context = self._new_trigger_context(parent_id=event.context.id)
            self._update_motion_state()
            self._control_fans(context)
            self.async_write_ha_state()
            return

        if entity_id in self._fan_controller_ids or entity_id in self._switch_ids:
            self._handle_external_change(entity_id, event)
            self.async_write_ha_state()

    def _update_current_temperature(self) -> None:
        """Update current temperature from all configured sensors."""
        temperatures = []
        for sensor_id in self._temperature_sensor_ids:
            state = self.hass.states.get(sensor_id)
            if state is not None and state.state not in ("unknown", "unavailable"):
                # Try attributes["temperature"] first (standard sensor attribute),
                # then fall back to state.value itself for sensors that use raw values
                temp = state.attributes.get("temperature")
                if temp is None:
                    try:
                        temp = float(state.state)
                    except (ValueError, TypeError):
                        pass
                if temp is not None:
                    temperatures.append(float(temp))

        if not temperatures:
            self._current_temperature = None
            return

        operation = self._temperature_operation
        if operation == "min":
            self._current_temperature = min(temperatures)
        elif operation == "avg":
            self._current_temperature = sum(temperatures) / len(temperatures)
        else:
            self._current_temperature = max(temperatures)

    def _update_motion_state(self) -> None:
        """Update the motion state from configured motion sensors.

        Fans stay on for ``motion_timeout`` seconds after motion stops; after
        that the motion gate closes. A motion sensor transition from inactive
        to active cancels the stay-on window. If motion sensors are configured
        but no motion has ever been detected, the gate stays closed.
        """
        if not self._motion_sensor_ids:
            self._motion_active = True
            return

        motion = False
        for sensor_id in self._motion_sensor_ids:
            state = self.hass.states.get(sensor_id)
            if state is not None and state.state in ("on", "active", "home"):
                motion = True
                break

        if motion:
            if not self._motion_sensor_active:
                # Motion resumed after a stop: cancel the stay-on window.
                self._cancel_motion_timer()
            self._motion_sensor_active = True
            self._motion_active = True
            return

        if self._motion_sensor_active:
            # Transition: motion just stopped.
            self._motion_sensor_active = False
            fans_running = self._fan_percentage is not None and self._fan_percentage > 0
            if self._motion_timeout > 0 and self._motion_cancel is None and fans_running:
                # Keep the fans on until the timeout elapses.
                self._motion_active = True
                self._motion_cancel = async_call_later(
                    self.hass,
                    self._motion_timeout,
                    self._handle_motion_timeout_elapsed,
                )
            else:
                # Fans are already off (or no timeout configured): no stay-on
                # window, matching the Hubitat behavior of not scheduling one.
                self._motion_active = False
            return

        # No transition (already inactive, possibly never active): keep the gate as-is.

    @callback
    def _handle_motion_timeout_elapsed(self, _time) -> None:
        """Motion has been inactive for the configured timeout."""
        self._motion_cancel = None
        self._motion_active = False
        self._control_fans(self._new_trigger_context())
        self.async_write_ha_state()

    @callback
    def _handle_retrigger_elapsed(self, _time) -> None:
        """Retrigger window elapsed: fans may turn back on if motion and temperature warrant."""
        self._retrigger_cancel = None
        self._control_fans(self._new_trigger_context())
        self.async_write_ha_state()

    def _control_fans(self, context: Context) -> None:
        """Control the fans based on temperature, motion, and settings.

        ``context`` is the trigger context for this control pass: it is
        passed to every service call the pass issues and set on this
        entity, so the Activity details trace can link the originating
        sensor (or the caller) to the fan/switch changes.

        Fan speed is controlled relative to a dual temperature range (target_temp_low to target_temp_high):
        1. If HVAC mode is OFF, turn all fans off
        2. If manual override is active, do nothing (manual control)
        3. If inside the retrigger window, fans remain off
        4. If motion sensors are configured and no motion is detected, turn fans off
        5. If current temp <= target_temp_low, fan is off (0%)
        6. If current temp >= target_temp_high, fan is at full speed (100%)
        7. Between these points, scale fan speed linearly based on position in the range

        Turning the fans off (from any of these paths other than HVAC mode OFF)
        starts the retrigger window, so the fans do not restart immediately.
        """
        # 1. HVAC mode off
        if self._hvac_mode == HVACMode.OFF:
            self._fan_percentage = None
            self._set_all_fans_off(context)
            return

        # 2. Manual override active
        if self._manual_override_active:
            return

        # 3. Retrigger window: fans must remain off even if motion is
        # re-detected or temperature warrants it. The window was started when
        # the fans went off, so it must not be restarted here.
        if self._retrigger_cancel is not None:
            if self._fan_percentage is not None and self._fan_percentage > 0:
                self._fan_percentage = None
                self._set_all_fans_off(context)
            return

        # 4. Motion check (only relevant when motion sensors are configured)
        if self._motion_sensor_ids and not self._motion_active:
            was_running = self._fan_percentage is not None and self._fan_percentage > 0
            self._fan_percentage = None
            if was_running:
                self._arm_retrigger_timer()
                self._set_all_fans_off(context)
            return

        # 5. Temperature check - only when current temp is known
        if self._current_temperature is None:
            return

        # Calculate fan percentage based on target temperature range
        percentage = self._calculate_percentage()
        was_running = self._fan_percentage is not None and self._fan_percentage > 0
        self._fan_percentage = percentage

        if percentage > 0:
            self._set_all_fans_on(percentage, context)
        elif was_running:
            self._arm_retrigger_timer()
            self._set_all_fans_off(context)

    def _calculate_percentage(self) -> int:
        """Calculate fan speed percentage based on current temperature relative to target range.

        Uses the thermostat's target temperature range as the reference point:
        - At or below target_temp_low: 0% (fan off)
        - At or above target_temp_high: 100% (full speed)
        - Between: linear interpolation based on position in the range
        """
        current = self._current_temperature
        
        # If we don't have a temperature reading yet, return 0
        if current is None:
            return 0
        
        # Below or at target_temp_low, fan is off
        if current <= self._target_temp_low:
            return 0
        
        # At or above target_temp_high, fan is at full speed
        if current >= self._target_temp_high:
            return 100
        
        # Linear interpolation between low and high
        range_size = self._target_temp_high - self._target_temp_low
        if range_size == 0:
            return 0
        deviation = current - self._target_temp_low
        percentage = (deviation / range_size) * 100
        return round(max(0, min(100, percentage)))

    def _new_trigger_context(self, parent_id: str | None = None) -> Context:
        """Create and register the trigger context for one control pass.

        Mirrors the automation/script convention: a fresh Context per pass
        whose parent_id is the id of the context that triggered the pass
        (the sensor event's context, or the call context from the UI/service).
        The same context is passed to every service call the pass issues
        and set on this entity, so the Activity details trace can link the
        originator to the fan/switch changes (and the thermostat itself).

        The marker subclass doubles as the self-initiation check in
        ``_handle_external_change``: the state-change events for the calls in
        this pass carry this same context, so ``isinstance`` on the event's
        context proves the change came from us (see ``_ControlPassContext``).
        """
        context = _ControlPassContext(parent_id=parent_id)
        self.async_set_context(context)
        return context

    def _call_service(
        self, domain: str, service: str, data: dict[str, Any], context: Context
    ) -> None:
        """Fire-and-forget a service call under a control-pass trigger context.

        The state-change event fired by the called entity carries the same
        context (the same provenance the Activity details trace is built
        from), so the event can be matched to this pass by context id instead
        of by comparing reported state.

        Failures are logged, never silently swallowed, and never propagated
        into the caller (fan control must not take down the thermostat).
        """
        async def _call() -> None:
            try:
                await self.hass.services.async_call(
                    domain, service, data, context=context
                )
            except Exception as err:  # noqa: BLE001 - log and continue
                _LOGGER.warning(
                    "Failed to call %s.%s for %s: %s",
                    domain, service, data.get("entity_id"), err,
                )

        self._config_entry.async_create_task(
            self.hass, _call(), name=f"fan_thermostat.{service}.{data.get('entity_id')}"
        )

    def _set_all_fans_on(self, percentage: int, context: Context) -> None:
        """Set all fan controllers to the given percentage and turn on all switches."""
        for fan_id in self._fan_controller_ids:
            self._call_service(
                "fan", "set_percentage",
                {"entity_id": fan_id, "percentage": percentage},
                context,
            )
        for sw_id in self._switch_ids:
            self._call_service("switch", "turn_on", {"entity_id": sw_id}, context)

    def _set_all_fans_off(self, context: Context) -> None:
        """Turn off all fan controllers and switches."""
        for fan_id in self._fan_controller_ids:
            self._call_service("fan", "turn_off", {"entity_id": fan_id}, context)
        for sw_id in self._switch_ids:
            self._call_service("switch", "turn_off", {"entity_id": sw_id}, context)

    def _handle_external_change(self, entity_id: str, event: Event) -> None:
        """Handle changes to fan controllers or switches from external sources.

        When an external source (wall switch, automation, etc.) toggles a fan/switch,
        this triggers manual override mode to prevent the thermostat from fighting
        with the user's intent. A notification is logged for visibility.
        Changes initiated by the thermostat itself are ignored: the state-change
        event for those calls carries the same control-pass context object we
        issued, so the event is matched by context type.
        """
        if entity_id not in self._fan_controller_ids and entity_id not in self._switch_ids:
            return

        if event.data.get("new_state") is None:
            return

        context = event.context
        if isinstance(context, _ControlPassContext):
            # Our own call completed: the marker context is definitive proof of
            # causality - never a manual override.
            return

        if self._manual_override_time <= 0:
            # Automatic manual override is disabled (0s time): the change is
            # left to the user's control instead of stopping the thermostat.
            return
        _LOGGER.info(
            "External change detected on %s - entering manual override mode",
            entity_id
        )
        # Link the switch's state write (the visible override transition) to
        # the external event, the same convention automations use for their
        # own state row. This is a fresh plain child context - the event is
        # not self-initiated, so it must not carry the marker subclass.
        self.enter_manual_override(
            Context(parent_id=context.id) if context is not None else None
        )
        self.enter_manual_override()

    def enter_manual_override(self, context: Context | None = None) -> None:
        """Enter manual override mode and stop automatic control.

        ``context``, when given, is set on the override switch's state write
        before it is written, so the Activity details trace links the external
        change to the switch (the visible override transition).
        """
        self._manual_override_active = True
        self._schedule_manual_override_timer()
        self._sync_override_switch(True, context=context)
        self.async_write_ha_state()

    def exit_manual_override(self) -> None:
        """Exit manual override mode and resume automatic control."""
        self._manual_override_active = False
        self._cancel_manual_override_timer()
        self._control_fans(self._new_trigger_context())
        self.async_write_ha_state()

    def _sync_override_switch(self, on: bool, context: Context | None = None) -> None:
        """Push the manual override state to the override switch entity."""
        controller = self._get_controller()
        if controller is None or controller.switch is None:
            return
        controller.switch.sync_state(on, context=context)

    def _schedule_manual_override_timer(self) -> None:
        """Schedule the automatic clear of manual override."""
        if self._manual_override_time > 0:
            self._cancel_manual_override_timer()
            self._manual_override_cancel = async_call_later(
                self.hass, self._manual_override_time,
                self._clear_manual_override_callback,
            )

    @callback
    def _clear_manual_override_callback(self, _time) -> None:
        """Clear the manual override and resume automatic control."""
        self._manual_override_active = False
        self._manual_override_cancel = None
        self._sync_override_switch(False)
        self._control_fans(self._new_trigger_context())
        self.async_write_ha_state()

    def _cancel_manual_override_timer(self) -> None:
        """Cancel the auto-clear timer."""
        if self._manual_override_cancel is not None:
            self._manual_override_cancel()
            self._manual_override_cancel = None

    def _cancel_motion_timer(self) -> None:
        """Cancel the motion inactive timer."""
        if self._motion_cancel is not None:
            self._motion_cancel()
            self._motion_cancel = None

    def _cancel_retrigger_timer(self) -> None:
        """Cancel the retrigger timer."""
        if self._retrigger_cancel is not None:
            self._retrigger_cancel()
            self._retrigger_cancel = None

    def _arm_retrigger_timer(self) -> None:
        """Start the retrigger window so fans cannot restart immediately."""
        if self._retrigger_time <= 0:
            return
        self._cancel_retrigger_timer()
        self._retrigger_cancel = async_call_later(
            self.hass, self._retrigger_time, self._handle_retrigger_elapsed
        )

    def handle_settings_changed(self, key: str) -> None:
        """Reschedule timers after a timer setting changed at runtime.

        Number entities update the config entry directly, so the climate
        entity is notified to adjust any pending timers to the new value.
        """
        if key == "temperature_operation":
            context = self._new_trigger_context()
            self._update_current_temperature()
            self._control_fans(context)
            self.async_write_ha_state()
        elif key == "motion_timeout":
            context = self._new_trigger_context()
            self._cancel_motion_timer()
            if not self._motion_sensor_active:
                fans_running = self._fan_percentage is not None and self._fan_percentage > 0
                if self._motion_timeout > 0 and fans_running:
                    self._motion_active = True
                    self._motion_cancel = async_call_later(
                        self.hass,
                        self._motion_timeout,
                        self._handle_motion_timeout_elapsed,
                    )
                else:
                    self._motion_active = False
            self._control_fans(context)
            self.async_write_ha_state()
        elif key == "retrigger_time":
            context = self._new_trigger_context()
            if self._retrigger_cancel is not None:
                self._cancel_retrigger_timer()
                if self._retrigger_time > 0:
                    self._retrigger_cancel = async_call_later(
                        self.hass, self._retrigger_time, self._handle_retrigger_elapsed
                    )
            self._control_fans(context)
            self.async_write_ha_state()
        elif key == "manual_override_time":
            if self._manual_override_active:
                self._cancel_manual_override_timer()
                self._schedule_manual_override_timer()
                self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass, cancel all timers."""
        self._cancel_manual_override_timer()
        self._cancel_motion_timer()
        self._cancel_retrigger_timer()
        controller = self._get_controller()
        if controller is not None and controller.climate is self:
            controller.climate = None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the HVAC mode."""
        self._hvac_mode = hvac_mode
        # Service calls set the entity's context to the caller's context;
        # use it as the parent so the trace links the caller to the fans.
        parent_id = self._context.id if self._context is not None else None
        context = self._new_trigger_context(parent_id=parent_id)
        if hvac_mode == HVACMode.OFF:
            self._fan_percentage = None
            self._set_all_fans_off(context)
        else:
            self._control_fans(context)
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature range."""
        if (target_temp_low := kwargs.get("target_temp_low")) is not None:
            self._target_temp_low = float(target_temp_low)
        if (target_temp_high := kwargs.get("target_temp_high")) is not None:
            self._target_temp_high = float(target_temp_high)
        
        # Also support legacy single temperature attribute for backward compatibility
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self._target_temp_low = float(temperature)
        
        # Temperature change affects fan speed, so recalculate. Service
        # calls set the entity's context to the caller's context; use it as
        # the parent so the trace links the caller to the fans.
        parent_id = self._context.id if self._context is not None else None
        context = self._new_trigger_context(parent_id=parent_id)
        self._control_fans(context)
        self.async_write_ha_state()

