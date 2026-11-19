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
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
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
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.COOL]
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
        self._hvac_mode: HVACMode = HVACMode.COOL
        self._fan_percentage: int | None = None
        self._manual_override_active: bool = False
        self._motion_active: bool = True
        self._last_motion_time: float | None = None
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
            return HVACAction.COOLING
        # Fan is off but mode is COOL - within target range, so IDLE
        return HVACAction.IDLE

    @property
    def percentage(self) -> int | None:
        return self._fan_percentage

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

        # Restore last state
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._hvac_mode = HVACMode(last_state.state) if last_state.state else HVACMode.COOL
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

        # Subscribe to the manual override switch entity
        self._subscribe_to_override_switch()

        # Initialize motion state from current sensor readings
        self._update_motion_state()
        
        # Initialize current temperature from sensors and control fans
        self._update_current_temperature()
        self._control_fans()
        self.async_write_ha_state()

    def _subscribe_to_override_switch(self) -> None:
        """Subscribe to the manual override switch entity state changes."""
        # Use a try/except in case the registry isn't fully loaded yet.
        # This can happen during setup when async_get creates an unloaded instance.
        override_entity_id = None
        try:
            entity_registry = async_get_entity_registry(self.hass)
            for entity in entity_registry.entities.values():
                if (
                    entity.platform == "fan_thermostat"
                    and entity.unique_id is not None
                    and entity.unique_id.endswith("_override")
                    and self._config_entry.entry_id in entity.unique_id
                ):
                    override_entity_id = entity.entity_id
                    break
        except AttributeError:
            # Entity registry may not be loaded yet during early setup
            pass
        
        if override_entity_id is not None:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, {override_entity_id}, self._on_override_switch_changed
                )
            )

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

    @callback
    def _on_override_switch_changed(self, event) -> None:
        """Handle manual override switch state changes."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        if new_state.state == "on":
            self._manual_override_active = True
            self._schedule_manual_override_timer()
        else:
            self._manual_override_active = False
            self._cancel_manual_override_timer()
            self._control_fans()

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
        """Update the motion state from configured motion sensors."""
        if not self._motion_sensor_ids:
            self._motion_active = True
            return
        motion = False
        for sensor_id in self._motion_sensor_ids:
            state = self.hass.states.get(sensor_id)
            if state is not None and state.state in ("on", "active", "home"):
                motion = True
                self._last_motion_time = self.hass.loop.time()
                break
        self._motion_active = motion
        if motion or self._motion_timeout == 0:
            self._cancel_motion_timer()
        else:
            if self._motion_cancel is None:
                self._schedule_motion_inactive_timer()

    def _schedule_motion_inactive_timer(self) -> None:
        """Schedule a timer to mark motion as inactive after the timeout."""
        if self._motion_timeout > 0 and self._motion_cancel is None:
            self._cancel_motion_timer()
            self._motion_cancel = async_call_later(
                self.hass, self._motion_timeout, self._set_motion_inactive_callback
            )

    @callback
    def _set_motion_inactive_callback(self, _time) -> None:
        """Set motion to inactive and update state."""
        self._motion_active = False
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
            if not self._motion_active:
                if self._retrigger_cancel is not None:
                    pass  # Still in retrigger window, keep fans on
                else:
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

    def _set_all_fans_on(self, percentage: int) -> None:
        """Set all fan controllers to the given percentage.
        
        If there are no fan controllers but there are switches, turn them on instead.
        """
        if self._fan_controller_ids:
            for fan_id in self._fan_controller_ids:
                if self.hass.states.get(fan_id) is not None:
                    try:
                        # Use async service call to avoid blocking during entity setup
                        asyncio.create_task(
                            self.hass.services.async_call(
                                "fan", "set_percentage",
                                {"entity_id": fan_id, "percentage": percentage},
                            )
                        )
                    except Exception:  # noqa: S110 - service call may fail for fake/test entities
                        pass
        elif self._switch_ids:
            for sw_id in self._switch_ids:
                if self.hass.states.get(sw_id) is not None:
                    try:
                        asyncio.create_task(
                            self.hass.services.async_call(
                                "switch", "turn_on",
                                {"entity_id": sw_id},
                            )
                        )
                    except Exception:  # noqa: S110 - service call may fail for fake/test entities
                        pass

    def _set_all_fans_off(self) -> None:
        """Turn off all fan controllers and switches."""
        if self._fan_controller_ids:
            for fan_id in self._fan_controller_ids:
                if self.hass.states.get(fan_id) is not None:
                    try:
                        asyncio.create_task(
                            self.hass.services.async_call(
                                "fan", "turn_off",
                                {"entity_id": fan_id},
                            )
                        )
                    except Exception:  # noqa: S110 - service call may fail for fake/test entities
                        pass
        if self._switch_ids:
            for sw_id in self._switch_ids:
                if self.hass.states.get(sw_id) is not None:
                    try:
                        asyncio.create_task(
                            self.hass.services.async_call(
                                "switch", "turn_off",
                                {"entity_id": sw_id},
                            )
                        )
                    except Exception:  # noqa: S110 - service call may fail for fake/test entities
                        pass

    def _handle_external_change(self, entity_id: str, new_state: State) -> None:
        """Handle changes to fan controllers or switches from external sources.
        
        When an external source (wall switch, automation, etc.) toggles a fan/switch,
        this triggers manual override mode to prevent the thermostat from fighting 
        with the user's intent. A notification is logged for visibility.
        """
        if entity_id in self._fan_controller_ids or entity_id in self._switch_ids:
            _LOGGER.info(
                "External change detected on %s - entering manual override mode",
                entity_id
            )
            self._manual_override_active = True
            self._schedule_manual_override_timer()

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

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass, cancel all timers."""
        self._cancel_manual_override_timer()
        self._cancel_motion_timer()
        if self._retrigger_cancel is not None:
            self._retrigger_cancel()
            self._retrigger_cancel = None

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

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the thermostat to enable automatic fan control.
        
        The fans will run at a speed determined by the current temperature relative
        to the target setpoints (target_temp_low and target_temp_high). If the room
        is already within or below the target range, the fans will remain off (0%).
        """
        if self._hvac_mode == HVACMode.OFF:
            return

        percentage = self._calculate_percentage()

        self._fan_percentage = percentage
        if percentage > 0:
            self._set_all_fans_on(percentage)
        else:
            self._set_all_fans_off()

        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the fan."""
        self._fan_percentage = None
        self._set_all_fans_off()
        self.async_write_ha_state()
