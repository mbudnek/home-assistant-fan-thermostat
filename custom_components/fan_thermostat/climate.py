"""Climate platform for the Fan Thermostat integration."""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.climate import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
    RestoreClimate,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PRECISION_TENTHS, STATE_OFF, STATE_ON, UnitOfTemperature
from homeassistant.core import (
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import (
    DEFAULT_MANUAL_OVERRIDE_TIME,
    DEFAULT_MOTION_TIMEOUT,
    DEFAULT_RETRIGGER_TIME,
    DEFAULT_TARGET_TEMP_HIGH,
    DEFAULT_TARGET_TEMP_LOW,
    DOMAIN,
    STARTUP_GRACE_SECONDS,
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

    entity = FanThermostatClimate(
        config_entry=config_entry,
        name=name,
        temperature_sensor_ids=temperature_sensor_ids,
        fan_controller_ids=fan_controller_ids,
        motion_sensor_ids=motion_sensor_ids,
        switch_ids=switch_ids,
    )
    async_add_entities([entity])


class FanThermostatClimate(RestoreClimate):
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
        ClimateEntityFeature.TARGET_TEMPERATURE_RANGE | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
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
    ) -> None:
        """Initialize the Fan Thermostat climate entity."""
        super().__init__()
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.unique_id}_climate"
        self._attr_device_info = get_device_info(config_entry, name)

        self._temperature_sensor_ids = temperature_sensor_ids
        self._fan_controller_ids = fan_controller_ids
        self._motion_sensor_ids = motion_sensor_ids
        self._switch_ids = switch_ids

        # Internal state
        self._hvac_mode: HVACMode = HVACMode.FAN_ONLY
        # Whether the thermostat most recently commanded the fans on. This is
        # the thermostat's own command state - never the physical fan speed -
        # and is only used to arm the retrigger window on a real turn-off.
        self._fans_commanded_on: bool = False
        self._retrigger_cancel: Callable[[], None] | None = None
        self._motion_cancel: Callable[[], None] | None = None
        self._startup_ignore_timer: Callable[[], None] | None = None
        # Initial range: the defaults. The runtime range is restored from
        # the restore cache in async_added_to_hass; the defaults are only the
        # initial value used until then (and when there is nothing to restore).
        self._target_temp_low: float = DEFAULT_TARGET_TEMP_LOW
        self._target_temp_high: float = DEFAULT_TARGET_TEMP_HIGH

    @property
    def hvac_mode(self) -> HVACMode:
        return self._hvac_mode

    def _desired_percentage(self) -> int:
        """Calculate the desired fan speed from the current state.

        Pure function of the current state (HVAC mode, motion, retrigger
        window, target range) - no physical fan state is tracked. The entity
        displays this value and the control pass commands it, so the fans'
        actual speed is never read or kept.
        """
        if self._hvac_mode == HVACMode.OFF:
            return 0
        if self._retrigger_cancel is not None:
            return 0
        if self._motion_sensor_ids and not self._motion_gate_open():
            return 0
        return self._calculate_percentage()

    @property
    def hvac_action(self) -> HVACAction:
        if self._hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if self._desired_percentage() > 0:
            return HVACAction.FAN
        # Fan is off but mode is FAN_ONLY - within target range, so IDLE
        return HVACAction.IDLE

    def _manual_override_active(self) -> bool:
        """Return True if the override switch is on (the switch owns the state)."""
        controller = self._get_controller()
        return controller is not None and controller.switch is not None and controller.switch.is_on

    @property
    def available(self) -> bool:
        # Available when any configured input reports a usable state. An
        # input that is unknown or unavailable (or referenced but never
        # reported) is not usable, so a typo'd or dead configuration reports
        # the entity as unavailable rather than letting it look healthy.
        for entity_id in [
            *self._temperature_sensor_ids,
            *self._fan_controller_ids,
            *self._switch_ids,
        ]:
            state = self.hass.states.get(entity_id)
            if state is not None and state.state not in ("unknown", "unavailable"):
                return True
        return False

    @property
    def native_current_temperature(self) -> float | None:
        return self._compute_temperature()

    @property
    def native_target_temperature_low(self) -> float | None:
        """Return the target temperature low."""
        return self._target_temp_low

    @property
    def native_target_temperature_high(self) -> float | None:
        """Return the target temperature high."""
        return self._target_temp_high

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return custom extra state attributes."""
        # Only include percentage when the desired speed is > 0
        desired = self._desired_percentage()
        if desired > 0:
            return {"percentage": desired}
        return {}

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()

        # Register with the shared controller. Platforms are set up concurrently,
        # so the switch may be added before (or after) this entity. The switch
        # owns the manual override state, so the climate only queries it; on
        # startup the gate is evaluated fresh for every control pass.
        controller = self._get_controller()
        if controller is not None:
            controller.climate = self

        # Restore the HVAC mode
        last_state = await self.async_get_last_state()
        if last_state is not None:
            try:
                self._hvac_mode = HVACMode(last_state.state) if last_state.state else HVACMode.FAN_ONLY
            except ValueError:
                # Unavailable/unknown/invalid state at shutdown - fall back to FAN_ONLY
                self._hvac_mode = HVACMode.FAN_ONLY

        # Restore the target temperature range from the restore cache. The
        # cache holds the runtime-updated values in native units (never the
        # display-rounded state attributes), so the setpoints survive
        # restarts losslessly. The constructor's const defaults are only the
        # initial range used when there is nothing usable to restore.
        if (climate_data := await self.async_get_last_climate_data(validate_range=True)) is not None:
            restored_low = climate_data.native_target_temperature_low
            restored_high = climate_data.native_target_temperature_high
            # Only adopt the cached pair when it forms a usable range: both
            # values present and ordered. A partial or inconsistent pair
            # (e.g. a hand-edited cache) leaves the constructor defaults in
            # place rather than a broken or ambiguous range.
            if restored_low is not None and restored_high is not None and restored_low <= restored_high:
                self._target_temp_low = restored_low
                self._target_temp_high = restored_high

        # Subscribe to temperature sensors
        for sensor_id in self._temperature_sensor_ids:
            self.async_on_remove(async_track_state_change_event(self.hass, {sensor_id}, self._on_state_changed))

        # Subscribe to fan controllers
        for fan_id in self._fan_controller_ids:
            self.async_on_remove(async_track_state_change_event(self.hass, {fan_id}, self._on_state_changed))

        # Subscribe to motion sensors
        for sensor_id in self._motion_sensor_ids:
            self.async_on_remove(async_track_state_change_event(self.hass, {sensor_id}, self._on_state_changed))

        # Subscribe to switches
        for sw_id in self._switch_ids:
            self.async_on_remove(async_track_state_change_event(self.hass, {sw_id}, self._on_state_changed))

        # Arm the startup grace window: fan/switch integrations report their own
        # state when they boot, and that boot report was not requested by the
        # thermostat. While the window is pending, external-change events are
        # ignored so a boot report cannot flip the manual override switch on.
        self._startup_ignore_timer = async_call_later(
            self.hass, STARTUP_GRACE_SECONDS, self._clear_startup_ignore_timer
        )

        # Control fans on startup. The desired speed is recalculated from the
        # current state of the sensors - the fan's speed is never recovered
        # from the previous session (fans may cycle off/on on reboot).
        #
        # The pass is deferred until the manual override switch is ready:
        # the switch restores its override state during setup, and the first
        # pass must run with that restored override in place (otherwise the
        # fans can be commanded on while the override is active).
        controller = self._get_controller()
        if controller is not None:
            controller.add_startup_callback(self._startup_control_pass)
        else:
            self._startup_control_pass()

    def _startup_control_pass(self) -> None:
        """Run the first control pass and write the state."""
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
    def _on_state_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle state change events from sensors."""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        if entity_id in self._temperature_sensor_ids:
            context = self._new_trigger_context(parent_id=event.context.id)
            self._control_fans(context)
            self.async_write_ha_state()
            return

        if entity_id in self._motion_sensor_ids:
            # The active->inactive transition arms the stay-on window, and the
            # inactive->active transition cancels it. The gate itself is
            # computed fresh for every control pass.
            was_active = self._motion_present_before_event(entity_id, event.data.get("old_state"))
            is_active = self._motion_present_now()
            if was_active and not is_active and self._motion_timeout > 0 and self._motion_cancel is None:
                # Motion just stopped: keep the fans on for the timeout.
                self._motion_cancel = async_call_later(
                    self.hass,
                    self._motion_timeout,
                    self._handle_motion_timeout_elapsed,
                )
            elif not was_active and is_active:
                # Motion resumed after a stop: cancel the keep-on window.
                self._cancel_motion_timer()
            context = self._new_trigger_context(parent_id=event.context.id)
            self._control_fans(context)
            self.async_write_ha_state()
            return

        if entity_id in self._fan_controller_ids or entity_id in self._switch_ids:
            self._handle_external_change(entity_id, event)
            self.async_write_ha_state()

    def _compute_temperature(self) -> float | None:
        """Compute the aggregated temperature from the current sensor states."""
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
                    except ValueError, TypeError:
                        pass
                if temp is not None:
                    temperatures.append(float(temp))

        if not temperatures:
            return None

        operation = self._temperature_operation
        if operation == "min":
            return min(temperatures)
        if operation == "avg":
            return sum(temperatures) / len(temperatures)
        return max(temperatures)

    def _motion_present_now(self) -> bool:
        """Return True if any configured motion sensor currently reports motion."""
        for sensor_id in self._motion_sensor_ids:
            state = self.hass.states.get(sensor_id)
            if state is not None and state.state in ("on", "active", "home"):
                return True
        return False

    def _motion_present_before_event(self, entity_id: str, old_state: State | None) -> bool:
        """Return True if any configured motion sensor reported motion before this event."""
        for sensor_id in self._motion_sensor_ids:
            if sensor_id == entity_id:
                old_str = old_state.state if old_state is not None else None
            else:
                state = self.hass.states.get(sensor_id)
                old_str = state.state if state is not None else None
            if old_str in ("on", "active", "home"):
                return True
        return False

    def _motion_gate_open(self) -> bool:
        """Return True if the motion gate should allow the fans.

        The gate is open when any configured motion sensor reports motion, or
        when the stay-on window (armed by the latest active->inactive
        transition) is still pending. No motion sensors configured means no
        gate at all.
        """
        if not self._motion_sensor_ids:
            return True
        return self._motion_present_now() or self._motion_cancel is not None

    @callback
    def _handle_motion_timeout_elapsed(self, _time: datetime) -> None:
        """Motion has been inactive for the configured timeout."""
        self._motion_cancel = None
        self._control_fans(self._new_trigger_context())
        self.async_write_ha_state()

    @callback
    def _handle_retrigger_elapsed(self, _time: datetime) -> None:
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
        1. If HVAC mode is OFF, do nothing (the thermostat is off, not the fans)
        2. If manual override is active, do nothing (manual control)
        3. If inside the retrigger window, fans remain off
        4. If motion sensors are configured and no motion is detected, turn fans off
        5. If current temp <= target_temp_low, fan is off (0%)
        6. If current temp >= target_temp_high, fan is at full speed (100%)
        7. Between these points, scale fan speed linearly based on position in the range

        Turning the fans off (from any of these paths other than HVAC mode OFF)
        starts the retrigger window, so the fans do not restart immediately.

        The desired speed is recalculated from the current sensor state on
        every pass - the physical state of the fans is never read or remembered.
        """
        # 1. HVAC mode off - the thermostat is off, not the fans. Do nothing:
        # the physical fans stay exactly as the user left them.
        if self._hvac_mode == HVACMode.OFF:
            return

        # 2. Manual override active
        if self._manual_override_active():
            return

        # 3. Retrigger window: fans must remain off even if motion is
        # re-detected or temperature warrants it. The window was started when
        # the fans went off, so it must not be restarted here.
        if self._retrigger_cancel is not None:
            self._set_all_fans_off(context)
            return

        # 4. Motion check (only relevant when motion sensors are configured)
        if self._motion_sensor_ids and not self._motion_gate_open():
            self._set_all_fans_off(context)
            if self._fans_commanded_on:
                self._arm_retrigger_timer()
            self._fans_commanded_on = False
            return

        # 5. Temperature check - only when current temp is known
        if self._compute_temperature() is None:
            return

        # Calculate fan percentage based on target temperature range
        percentage = self._calculate_percentage()

        if percentage > 0:
            self._set_all_fans_on(percentage, context)
            self._fans_commanded_on = True
        else:
            self._set_all_fans_off(context)
            if self._fans_commanded_on:
                self._arm_retrigger_timer()
            self._fans_commanded_on = False

    def _calculate_percentage(self) -> int:
        """Calculate fan speed percentage based on current temperature relative to target range.

        Uses the thermostat's target temperature range as the reference point:
        - At or below target_temp_low: 0% (fan off)
        - At or above target_temp_high: 100% (full speed)
        - Between: linear interpolation based on position in the range
        """
        current = self._compute_temperature()

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

    def _call_service(self, domain: str, service: str, data: dict[str, Any], context: Context) -> None:
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
                await self.hass.services.async_call(domain, service, data, context=context)
            except Exception as err:  # noqa: BLE001 - log and continue
                _LOGGER.warning(
                    "Failed to call %s.%s for %s: %s",
                    domain,
                    service,
                    data.get("entity_id"),
                    err,
                )

        self._config_entry.async_create_task(
            self.hass, _call(), name=f"fan_thermostat.{service}.{data.get('entity_id')}"
        )

    def _set_all_fans_on(self, percentage: int, context: Context) -> None:
        """Set all fan controllers to the given percentage and turn on all switches."""
        for fan_id in self._fan_controller_ids:
            self._call_service(
                "fan",
                "set_percentage",
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

    def _handle_external_change(self, entity_id: str, event: Event[EventStateChangedData]) -> None:
        """Handle changes to fan controllers or switches from external sources.

        When an external source (wall switch, automation, etc.) toggles a fan/switch,
        this triggers manual override mode to prevent the thermostat from fighting
        with the user's intent. A notification is logged for visibility.
        Changes initiated by the thermostat itself are ignored: the state-change
        event for those calls carries the same control-pass context object we
        issued, so the event is matched by context type.

        Two further kinds of events are never manual override either:
        - boot reports: fan/switch integrations report their own state when they
          start, which is not a user intent. The startup grace window (armed in
          async_added_to_hass) ignores any such event for a short time after
          setup.
        - consistent reports: a reported state that matches what the thermostat
          would have commanded is not a divergence from the user's intent -
          the thermostat would have set it to the same value anyway.
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

        if self._startup_ignore_timer is not None:
            # Boot report from the fan/switch integration's own startup: it
            # was not requested by the thermostat, so it cannot be a user's
            # intent. Ignore it rather than entering manual override.
            _LOGGER.debug("Ignoring %s state event during startup grace window", entity_id)
            return

        if self._matches_desired_state(entity_id, event):
            # The reported state is consistent with what the thermostat would
            # have commanded: not an external change, just a report of the
            # state the user (or the thermostat) already asked for.
            return

        if self._manual_override_time <= 0:
            # Automatic manual override is disabled (0s time): the change is
            # left to the user's control instead of stopping the thermostat.
            return
        _LOGGER.info("External change detected on %s - entering manual override mode", entity_id)
        # Link the switch's state write (the visible override transition) to
        # the external event, the same convention automations use for their
        # own state row. This is a fresh plain child context - the event is
        # not self-initiated, so it must not carry the marker subclass.
        controller = self._get_controller()
        if controller is not None and controller.switch is not None:
            controller.switch.enter(Context(parent_id=context.id) if context is not None else None)

    def _reported_fan_percentage(self, new_state: State | None) -> int | None:
        """Return the reported fan percentage from a state, or None if indeterminate.

        An off fan reports 0%; anything else is only comparable when the
        reported state carries a numeric percentage.
        """
        if new_state is None:
            return None
        if new_state.state == STATE_OFF:
            return 0
        percentage = new_state.attributes.get("percentage")
        if percentage is None:
            return None
        try:
            return int(percentage)
        except TypeError, ValueError:
            return None

    def _matches_desired_state(self, entity_id: str, event: Event[EventStateChangedData]) -> bool:
        """Return True if the reported state is consistent with the desired state.

        Fans are compared by exact percentage: the reported value must equal
        what the thermostat would command. Switches carry no speed, so a
        consistent report is simply one that agrees with the desired on/off.
        """
        new_state = event.data.get("new_state")
        if entity_id in self._fan_controller_ids:
            reported = self._reported_fan_percentage(new_state)
            if reported is None:
                return False
            return reported == self._desired_percentage()
        if entity_id in self._switch_ids:
            if new_state is None:
                return False
            desired_on = self._desired_percentage() > 0
            reported_on = new_state.state == STATE_ON
            return reported_on == desired_on
        return False

    @callback
    def _clear_startup_ignore_timer(self, _time: datetime) -> None:
        """Startup grace window elapsed: manual override detection is back on."""
        self._startup_ignore_timer = None

    def _cancel_startup_ignore_timer(self) -> None:
        """Cancel the startup grace window timer."""
        if self._startup_ignore_timer is not None:
            self._startup_ignore_timer()
            self._startup_ignore_timer = None

    @callback
    def _on_switch_override_changed(self, active: bool) -> None:
        """React to the override switch's state change.

        The switch owns the override state; the climate only reacts to it.
        On enter the gate is evaluated fresh for every control pass, so there
        is nothing to record. On exit, automatic control resumes.
        """
        if not active:
            self._control_fans(self._new_trigger_context())
        self.async_write_ha_state()

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
        """Start the retrigger window so fans cannot restart immediately.

        A pending window is left running: it is anchored to the time the fans
        last went off and must not be extended by repeated turn-off commands.
        """
        if self._retrigger_time <= 0:
            return
        if self._retrigger_cancel is not None:
            return
        self._retrigger_cancel = async_call_later(self.hass, self._retrigger_time, self._handle_retrigger_elapsed)

    def handle_settings_changed(self, key: str) -> None:
        """Reschedule timers after a timer setting changed at runtime.

        Number entities update the config entry directly, so the climate
        entity is notified to adjust any pending timers to the new value.
        """
        if key == "temperature_operation":
            context = self._new_trigger_context()
            self._control_fans(context)
            self.async_write_ha_state()
        elif key == "motion_timeout":
            context = self._new_trigger_context()
            window_active = self._motion_cancel is not None
            self._cancel_motion_timer()
            if self._motion_present_now():
                # Motion is active: cancel any pending stay-on window.
                pass
            elif window_active and self._motion_timeout > 0:
                # Motion just stopped: keep the fans on for the new timeout.
                self._motion_cancel = async_call_later(
                    self.hass,
                    self._motion_timeout,
                    self._handle_motion_timeout_elapsed,
                )
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
            # The switch owns the override state and its auto-clear timer.
            controller = self._get_controller()
            if controller is not None and controller.switch is not None:
                controller.switch.handle_settings_changed(key)

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass, cancel all timers."""
        self._cancel_motion_timer()
        self._cancel_retrigger_timer()
        self._cancel_startup_ignore_timer()
        controller = self._get_controller()
        if controller is not None and controller.climate is self:
            controller.climate = None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the HVAC mode."""
        self._hvac_mode = hvac_mode
        # HVAC mode OFF means the thermostat is off, not the fans: no fan
        # commands are issued, the physical fans stay exactly as the user
        # left them. Reset the thermostat's own command state so no
        # retrigger window is armed by a turn-off the thermostat never issued.
        if hvac_mode == HVACMode.OFF:
            self._fans_commanded_on = False
            self.async_write_ha_state()
            return
        # Service calls set the entity's context to the caller's context;
        # use it as the parent so the trace links the caller to the fans.
        parent_id = self._context.id if self._context is not None else None
        context = self._new_trigger_context(parent_id=parent_id)
        self._control_fans(context)
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature range."""
        # Work on local copies first: the range is only applied once it is
        # valid, so a rejected call leaves the entity untouched.
        raw_low = kwargs.get("target_temp_low")
        raw_high = kwargs.get("target_temp_high")
        target_temp_low = float(raw_low) if raw_low is not None else self._target_temp_low
        target_temp_high = float(raw_high) if raw_high is not None else self._target_temp_high

        # The range must stay ordered: the low setpoint must not exceed the
        # high. (The service handler validates the values passed with the
        # call; this guards a value against the one already configured.)
        if target_temp_low > target_temp_high:
            raise ServiceValidationError("Invalid target temperature range: low setpoint must not exceed high setpoint")

        self._target_temp_low = target_temp_low
        self._target_temp_high = target_temp_high

        # The range survives restarts automatically: the restore state
        # machinery persists it (native values) in extra_restore_state_data
        # and async_added_to_hass restores it on the next setup.

        # Temperature change affects fan speed, so recalculate. Service
        # calls set the entity's context to the caller's context; use it as
        # the parent so the trace links the caller to the fans.
        parent_id = self._context.id if self._context is not None else None
        context = self._new_trigger_context(parent_id=parent_id)
        self._control_fans(context)
        self.async_write_ha_state()
