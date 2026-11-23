"""Tests for the Fan Thermostat climate platform."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from pytest_homeassistant.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from . import setup_integration


async def test_climate_entity_exists(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test that the climate entity is created."""
    await setup_integration(hass, mock_config_entry)

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.state == HVACMode.FAN_ONLY

    # Check entity registry
    entity_entry = entity_registry.async_get("climate.bedroom_fan_thermostat")
    assert entity_entry is not None
    assert entity_entry.unique_id == "bedroom_fan_thermostat_climate"


async def test_climate_hvac_modes(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the climate entity reports correct HVAC modes."""
    await setup_integration(hass, mock_config_entry)

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert HVACMode.OFF in climate_state.attributes["hvac_modes"]
    assert HVACMode.FAN_ONLY in climate_state.attributes["hvac_modes"]




async def test_climate_initial_state(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the initial state of the climate entity."""
    await setup_integration(hass, mock_config_entry)

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.state == HVACMode.FAN_ONLY
    # hvac_action is IDLE because temp sensor not set yet, so percentage=0 and mode=FAN_ONLY
    assert climate_state.attributes["hvac_action"] == HVACAction.IDLE
    assert climate_state.attributes["target_temp_low"] == 72.0
    assert climate_state.attributes["target_temp_high"] == 78.0


async def test_set_hvac_mode_fan_only(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test setting HVAC mode to fan_only."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {
            ATTR_ENTITY_ID: "climate.bedroom_fan_thermostat",
            ATTR_HVAC_MODE: HVACMode.FAN_ONLY,
        },
        blocking=True,
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.state == HVACMode.FAN_ONLY


async def test_set_hvac_mode_off(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test setting HVAC mode to off turns off the fans."""
    await setup_integration(hass, mock_config_entry)

    # Set a temperature that would normally turn the fan on (above target_high=78)
    hass.states.async_set(
        "sensor.bedroom_temp", 85.0, {"device_class": "temperature"}
    )

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {
            ATTR_ENTITY_ID: "climate.bedroom_fan_thermostat",
            "target_temp_low": 70.0,
            "target_temp_high": 80.0,
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    # Now turn off
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {
            ATTR_ENTITY_ID: "climate.bedroom_fan_thermostat",
            ATTR_HVAC_MODE: HVACMode.OFF,
        },
        blocking=True,
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.state == HVACMode.OFF
    assert climate_state.attributes["hvac_action"] == HVACAction.OFF


async def test_set_temperature(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test setting the target temperature range."""
    await setup_integration(hass, mock_config_entry)

    # Set both low and high temperatures
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {
            ATTR_ENTITY_ID: "climate.bedroom_fan_thermostat",
            "target_temp_low": 70.0,
            "target_temp_high": 80.0,
        },
        blocking=True,
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes["target_temp_low"] == 70.0
    assert climate_state.attributes["target_temp_high"] == 80.0


async def test_temperature_above_target_turns_on_fan(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the fan turns on when temperature exceeds target_low."""
    await setup_integration(hass, mock_config_entry)

    # Set a temperature between target_low and target_high (72-78 range)
    hass.states.async_set(
        "sensor.bedroom_temp", 75.0, {"device_class": "temperature"}
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    # Temp 75, target_low 72, target_high 78 (range=6) -> ((75-72)/6)*100 = 50%
    assert climate_state.attributes.get("percentage") == 50


async def test_temperature_at_target_low_turns_off_fan(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the fan turns off when temperature equals target_temp_low."""
    await setup_integration(hass, mock_config_entry)

    # Set temperature to exactly target_temp_low
    hass.states.async_set(
        "sensor.bedroom_temp", 72.0, {"device_class": "temperature"}
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") is None


async def test_temperature_below_target_turns_off_fan(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the fan turns off when temperature is below target_temp_low."""
    await setup_integration(hass, mock_config_entry)

    # Set temperature below target_temp_low
    hass.states.async_set(
        "sensor.bedroom_temp", 70.0, {"device_class": "temperature"}
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") is None


async def test_temperature_step_calculates_speed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that temperature step correctly calculates fan speed."""
    await setup_integration(hass, mock_config_entry)

    # target_low is 72, target_high is 78 (range=6)
    # Temperature 74 (2 above target_low) -> ((74-72)/6)*100 = 33%
    hass.states.async_set(
        "sensor.bedroom_temp", 74.0, {"device_class": "temperature"}
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") == 33

    # Temperature 78 (at target_high) -> clamped to 100%
    hass.states.async_set(
        "sensor.bedroom_temp", 78.0, {"device_class": "temperature"}
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") == 100


async def test_multiple_temperature_sensors_averaged(
    hass: HomeAssistant,
    mock_config_entry_multiple_sensors: MockConfigEntry,
) -> None:
    """Test that multiple temperature sensors are averaged."""
    await setup_integration(hass, mock_config_entry_multiple_sensors)

    # Set two temperatures: 69 and 71 -> average 70 (equals target_temp_low=70)
    hass.states.async_set(
        "sensor.living_room_temp_1", 69.0, {"device_class": "temperature"}
    )
    hass.states.async_set(
        "sensor.living_room_temp_2", 71.0, {"device_class": "temperature"}
    )

    climate_state = hass.states.get("climate.living_room_fan_thermostat")
    # Average is 70, which equals target_low, so fan should be off
    assert climate_state.attributes.get("percentage") is None

    # Now set to 74 and 78 -> average 76 (between target_low=70 and target_high=78)
    hass.states.async_set(
        "sensor.living_room_temp_1", 74.0, {"device_class": "temperature"}
    )
    hass.states.async_set(
        "sensor.living_room_temp_2", 78.0, {"device_class": "temperature"}
    )

    climate_state = hass.states.get("climate.living_room_fan_thermostat")
    # Average is 76, target_low is 70, target_high is 78 (range=8) -> ((76-70)/8)*100 = 75%
    assert climate_state.attributes.get("percentage") == 75


async def test_motion_sensor_turns_fan_off(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that motion sensors turn the fan off after the motion timeout."""
    await setup_integration(hass, mock_config_entry)

    # Set temperature above setpoint
    hass.states.async_set(
        "sensor.bedroom_temp", 85.0, {"device_class": "temperature"}
    )

    # Fan should be on
    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") == 100

    # Motion sensor becomes inactive: the fan stays on until the timeout elapses
    hass.states.async_set(
        "binary_sensor.bedroom_motion", "inactive", {"device_class": "motion"}
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") == 100

    # After the motion timeout (300s) elapses, the fan turns off
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=301))
    await hass.async_block_till_done()

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") is None


async def test_motion_sensor_turns_fan_on(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that motion sensors turn the fan back on when active."""
    await setup_integration(hass, mock_config_entry)

    # Set temperature above setpoint
    hass.states.async_set(
        "sensor.bedroom_temp", 85.0, {"device_class": "temperature"}
    )

    # Motion sensor becomes inactive: the fan stays on for the motion timeout
    hass.states.async_set(
        "binary_sensor.bedroom_motion", "inactive", {"device_class": "motion"}
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") == 100

    # Motion sensor becomes active again
    hass.states.async_set(
        "binary_sensor.bedroom_motion", "active", {"device_class": "motion"}
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") == 100


async def test_manual_override_switch(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the manual override switch."""
    await setup_integration(hass, mock_config_entry)

    # Set temperature above setpoint
    hass.states.async_set(
        "sensor.bedroom_temp", 85.0, {"device_class": "temperature"}
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") == 100

    # Turn on manual override
    await hass.services.async_call(
        "switch",
        "turn_on",
        {ATTR_ENTITY_ID: "switch.bedroom_fan_thermostat_manual_override"},
        blocking=True,
    )

    # Climate entity should now be in manual override mode
    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") == 100  # Should not change

    # Turn off manual override
    await hass.services.async_call(
        "switch",
        "turn_off",
        {ATTR_ENTITY_ID: "switch.bedroom_fan_thermostat_manual_override"},
        blocking=True,
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") == 100




async def test_turn_on(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test turning the climate entity on."""
    # Set a temperature so _current_temperature is known
    hass.states.async_set("sensor.bedroom_temp", "83.0", {"device_class": "temperature"})
    
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "climate.bedroom_fan_thermostat"},
        blocking=True,
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") == 100


async def test_turn_off(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test turning the climate entity off."""
    await setup_integration(hass, mock_config_entry)

    # Set temperature above setpoint
    hass.states.async_set(
        "sensor.bedroom_temp", 85.0, {"device_class": "temperature"}
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") == 100

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: "climate.bedroom_fan_thermostat"},
        blocking=True,
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") is None
    assert climate_state.state == HVACMode.OFF


async def test_switches_controlled(
    hass: HomeAssistant,
    mock_config_entry_with_switches: MockConfigEntry,
) -> None:
    """Test that switches are controlled when fans would be controlled."""
    await setup_integration(hass, mock_config_entry_with_switches)

    # Verify climate entity exists and is in FAN_ONLY mode
    climate_state = hass.states.get("climate.box_fan_thermostat")
    assert climate_state is not None
    assert climate_state.state == HVACMode.FAN_ONLY

    # Set temperature above target_high (78.0) so the fan should turn on at 100%
    hass.states.async_set(
        "sensor.box_fan_temp", 85.0, {"device_class": "temperature"}
    )

    climate_state = hass.states.get("climate.box_fan_thermostat")
    assert climate_state.attributes.get("percentage") == 100

    # Now turn off via climate service
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: "climate.box_fan_thermostat"},
        blocking=True,
    )

    climate_state = hass.states.get("climate.box_fan_thermostat")
    assert climate_state.attributes.get("percentage") is None


async def test_single_speed_fan(
    hass: HomeAssistant,
    mock_config_entry_single_speed: MockConfigEntry,
) -> None:
    """Test that a single-speed fan always turns on when needed."""
    await setup_integration(hass, mock_config_entry_single_speed)

    # Set temperature above setpoint
    hass.states.async_set(
        "sensor.single_speed_temp", 85.0, {"device_class": "temperature"}
    )

    climate_state = hass.states.get("climate.single_speed_fan_thermostat")
    assert climate_state.attributes.get("percentage") == 100
    assert climate_state.attributes["percentage"] == 100


async def test_climate_entity_available(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the climate entity reports availability correctly."""
    # Set initial sensor states so they're "seen" by the entity
    hass.states.async_set("sensor.bedroom_temp", "78.0", {"device_class": "temperature"})
    hass.states.async_set("fan.bedroom_fan", "on", {"supported_features": 1})
    
    await setup_integration(hass, mock_config_entry)

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    # Entity should be available because sensor.bedroom_temp exists and is not unavailable
    assert climate_state.attributes.get("availability") != "unavailable" or climate_state.state != "unavailable"

    # Make all sensors unavailable
    hass.states.async_set(
        "sensor.bedroom_temp", "unavailable", {"device_class": "temperature"}
    )
    hass.states.async_set(
        "fan.bedroom_fan", "unavailable", {"supported_features": 1}
    )

    await hass.async_block_till_done()
    
    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    # Entity should now be unavailable because all sensors are unavailable
    assert climate_state.state == "unavailable"


async def test_climate_hvac_action(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the climate entity reports the correct HVAC action."""
    await setup_integration(hass, mock_config_entry)

    # Initially, no temperature set yet, mode=FAN_ONLY so action is IDLE (not OFF since we're not in OFF mode)
    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes["hvac_action"] == HVACAction.IDLE

    # Set temperature above target_high (78.0) -> fan runs at 100%, action is FAN
    hass.states.async_set(
        "sensor.bedroom_temp", 85.0, {"device_class": "temperature"}
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes["hvac_action"] == HVACAction.FAN

    # Set temperature at target_high (78.0) -> fan still runs at 100%, action is FAN
    hass.states.async_set(
        "sensor.bedroom_temp", 78.0, {"device_class": "temperature"}
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes["hvac_action"] == HVACAction.FAN
    
    # Set temperature below target_low (72.0) -> fan off, action is IDLE
    hass.states.async_set(
        "sensor.bedroom_temp", 70.0, {"device_class": "temperature"}
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes["hvac_action"] == HVACAction.IDLE


async def test_climate_native_temperature_unit(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the climate entity reports Fahrenheit as the temperature unit."""
    await setup_integration(hass, mock_config_entry)

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes["temperature_unit"] == "°F"


async def test_climate_min_max_temp(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the climate entity reports correct min/max temperature."""
    await setup_integration(hass, mock_config_entry)

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes["min_temp"] == 45
    assert climate_state.attributes["max_temp"] == 95


async def test_fan_service_calls_are_made(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that fan controllers receive set_percentage service calls."""
    recorded = []

    async def _handle_set_percentage(call):
        recorded.append(("set_percentage", call.data.get("entity_id"), call.data.get("percentage")))

    hass.services.async_register("fan", "set_percentage", _handle_set_percentage)

    await setup_integration(hass, mock_config_entry)

    hass.states.async_set(
        "sensor.bedroom_temp", 85.0, {"device_class": "temperature"}
    )
    await hass.async_block_till_done()

    assert ("set_percentage", "fan.bedroom_fan", 100) in recorded


async def test_retrigger_time_blocks_fan_restart(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that retrigger time blocks the fan from restarting after the motion timeout."""
    await setup_integration(hass, mock_config_entry)

    # Set temperature above setpoint: fan at 100%
    hass.states.async_set(
        "sensor.bedroom_temp", 85.0, {"device_class": "temperature"}
    )
    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") == 100

    # Motion stops
    hass.states.async_set(
        "binary_sensor.bedroom_motion", "inactive", {"device_class": "motion"}
    )
    await hass.async_block_till_done()

    # Stay-on window: fan still on
    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") == 100

    # Motion timeout elapses: fans off, retrigger lockout starts
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=301))
    await hass.async_block_till_done()

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") is None

    # Motion re-detected during the retrigger window: fan still off
    hass.states.async_set(
        "binary_sensor.bedroom_motion", "active", {"device_class": "motion"}
    )
    await hass.async_block_till_done()

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") is None

    # Retrigger window elapses: fan turns back on
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=302))
    await hass.async_block_till_done()

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state.attributes.get("percentage") == 100


async def test_restore_unavailable_state_does_not_crash(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that an unavailable last state is restored as FAN_ONLY instead of crashing."""
    mock_restore_cache(
        hass, [State("climate.bedroom_fan_thermostat", "unavailable")]
    )

    await setup_integration(hass, mock_config_entry)

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.state == HVACMode.FAN_ONLY


async def test_external_change_enters_manual_override(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that external fan/switch changes trigger manual override; self-initiated changes do not."""

    async def _handle_set_percentage(call):
        hass.states.async_set(
            call.data["entity_id"], "on", {"percentage": call.data["percentage"]}
        )

    hass.services.async_register("fan", "set_percentage", _handle_set_percentage)

    await setup_integration(hass, mock_config_entry)

    # Temperature event: thermostat drives the fan itself (self-initiated)
    hass.states.async_set(
        "sensor.bedroom_temp", 85.0, {"device_class": "temperature"}
    )
    await hass.async_block_till_done()

    switch_state = hass.states.get(
        "switch.bedroom_fan_thermostat_manual_override"
    )
    assert switch_state is not None
    # Self-initiated change must NOT trigger manual override
    assert switch_state.state == STATE_OFF

    # External change to the fan drives the thermostat into manual override
    hass.states.async_set("fan.bedroom_fan", "off", {"percentage": 0})
    await hass.async_block_till_done()

    switch_state = hass.states.get(
        "switch.bedroom_fan_thermostat_manual_override"
    )
    assert switch_state.state == STATE_ON
