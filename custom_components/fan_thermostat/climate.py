"""Climate platform for the Fan Thermostat integration."""

from __future__ import annotations

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
from homeassistant.core import HomeAssistant, State, callback
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
        # Self-initiated service call tracking: entity_id -> (expected_state, percentage)
        self._self_initiated: dict[str, tuple[str, float | None]] = {}

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
        self._control_fans()
        self.async_write_ha_state()

    def _get_controller(self) -> "FanThermostatController | None":
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
            self._update_current_temperature()
            self._control_fans()
            self.async_write_ha_state()
            return

        if entity_id in self._motion_sensor_ids:
            self._update_motion_state()
            self._control_fans()
            self.async_write_ha_state()
            return

        if entity_id in self._fan_controller_ids or entity_id in self._switch_ids:
            self._handle_external_change(entity_id, new_state)
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

        if temperatures:
            self._current_temperature = sum(temperatures) / len(temperatures)
        else:
            self._current_temperature = None

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
            if self._motion_timeout > 0 and self._motion_cancel is None:
                # Keep the fans on until the timeout elapses.
                self._motion_active = True
                self._motion_cancel = async_call_later(
                    self.hass,
                    self._motion_timeout,
                    self._handle_motion_timeout_elapsed,
                )
            else:
                self._motion_active = False
            return

        # No transition (already inactive, possibly never active): keep the gate as-is.

    @callback
    def _handle_motion_timeout_elapsed(self, _time) -> None:
        """Motion has been inactive for the configured timeout."""
        self._motion_cancel = None
        self._motion_active = False
        if self._retrigger_time > 0:
            # Lock the fan off until the retrigger window elapses.
            self._cancel_retrigger_timer()
            self._retrigger_cancel = async_call_later(
                self.hass,
                self._retrigger_time,
                self._handle_retrigger_elapsed,
            )
        self._control_fans()
        self.async_write_ha_state()

    @callback
    def _handle_retrigger_elapsed(self, _time) -> None:
        """Retrigger window elapsed: fans may turn back on if motion and temperature warrant."""
        self._retrigger_cancel = None
        self._control_fans()
        self.async_write_ha_state()

    def _control_fans(self) -> None:
        """Control the fans based on temperature, motion, and settings.

        Fan speed is controlled relative to a dual temperature range (target_temp_low to target_temp_high):
        1. If HVAC mode is OFF, turn all fans off
        2. If manual override is active, do nothing (manual control)
        3. If motion sensors are configured and no motion is detected, turn fans off
        4. If current temp <= target_temp_low, fan is off (0%)
        5. If current temp >= target_temp_high, fan is at full speed (100%)
        6. Between these points, scale fan speed linearly based on position in the range
        """
        # 1. HVAC mode off
        if self._hvac_mode == HVACMode.OFF:
            self._fan_percentage = None
            self._set_all_fans_off()
            return

        # 2. Manual override active
        if self._manual_override_active:
            return

        # 3. Motion check
        if self._motion_sensor_ids:
            if self._retrigger_cancel is not None:
                # Still in the retrigger window: fans must remain off even if
                # motion is re-detected or temperature warrants it.
                self._fan_percentage = None
                self._set_all_fans_off()
                return
            if not self._motion_active:
                self._fan_percentage = None
                self._set_all_fans_off()
                return

        # 4. Temperature check - only when current temp is known
        if self._current_temperature is None:
            return

        # Calculate fan percentage based on target temperature range
        percentage = self._calculate_percentage()
        self._fan_percentage = percentage
        
        if percentage > 0:
            self._set_all_fans_on(percentage)
        else:
            self._set_all_fans_off()

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

    def _call_service(self, domain: str, service: str, data: dict[str, Any]) -> None:
        """Fire-and-forget a service call.

        Failures are logged, never silently swallowed, and never propagated
        into the caller (fan control must not take down the thermostat).
        """
        async def _call() -> None:
            try:
                await self.hass.services.async_call(domain, service, data)
            except Exception as err:  # noqa: BLE001 - log and continue
                _LOGGER.warning(
                    "Failed to call %s.%s for %s: %s",
                    domain, service, data.get("entity_id"), err,
                )

        self._config_entry.async_create_task(
            self.hass, _call(), name=f"fan_thermostat.{service}.{data.get('entity_id')}"
        )

    def _set_all_fans_on(self, percentage: int) -> None:
        """Set all fan controllers to the given percentage and turn on all switches."""
        for fan_id in self._fan_controller_ids:
            self._self_initiated[fan_id] = ("on", float(percentage))
            self._call_service(
                "fan", "set_percentage",
                {"entity_id": fan_id, "percentage": percentage},
            )
        for sw_id in self._switch_ids:
            self._self_initiated[sw_id] = ("on", None)
            self._call_service("switch", "turn_on", {"entity_id": sw_id})

    def _set_all_fans_off(self) -> None:
        """Turn off all fan controllers and switches."""
        for fan_id in self._fan_controller_ids:
            self._self_initiated[fan_id] = ("off", None)
            self._call_service("fan", "turn_off", {"entity_id": fan_id})
        for sw_id in self._switch_ids:
            self._self_initiated[sw_id] = ("off", None)
            self._call_service("switch", "turn_off", {"entity_id": sw_id})

    def _handle_external_change(self, entity_id: str, new_state: State) -> None:
        """Handle changes to fan controllers or switches from external sources.
        
        When an external source (wall switch, automation, etc.) toggles a fan/switch,
        this triggers manual override mode to prevent the thermostat from fighting 
        with the user's intent. A notification is logged for visibility.
        Changes initiated by the thermostat itself (tracked in _self_initiated)
        are ignored.
        """
        if entity_id not in self._fan_controller_ids and entity_id not in self._switch_ids:
            return

        expected = self._self_initiated.get(entity_id)
        if expected is not None:
            expected_state, expected_pct = expected
            if new_state.state == expected_state:
                if expected_pct is None:
                    return
                reported_pct = new_state.attributes.get("percentage")
                if reported_pct is None or float(reported_pct) == float(expected_pct):
                    return

        self._self_initiated.pop(entity_id, None)
        _LOGGER.info(
            "External change detected on %s - entering manual override mode",
            entity_id
        )
        self.enter_manual_override()

    def enter_manual_override(self) -> None:
        """Enter manual override mode and stop automatic control."""
        self._manual_override_active = True
        self._schedule_manual_override_timer()
        self._sync_override_switch(True)
        self.async_write_ha_state()

    def exit_manual_override(self) -> None:
        """Exit manual override mode and resume automatic control."""
        self._manual_override_active = False
        self._cancel_manual_override_timer()
        self._control_fans()
        self.async_write_ha_state()

    def _sync_override_switch(self, on: bool) -> None:
        """Push the manual override state to the override switch entity."""
        controller = self._get_controller()
        if controller is None or controller.switch is None:
            return
        controller.switch.sync_state(on)

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
        self._control_fans()
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
        if hvac_mode == HVACMode.OFF:
            self._fan_percentage = None
            self._set_all_fans_off()
        else:
            self._control_fans()
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
        
        # Temperature change affects fan speed, so recalculate
        self._control_fans()
        self.async_write_ha_state()

