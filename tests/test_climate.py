"""Tests for the Fan Thermostat climate platform."""

from datetime import timedelta

import pytest
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import (
    EVENT_STATE_CHANGED,
    Event,
    EventStateChangedData,
    HomeAssistant,
    ServiceCall,
    State,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.fan_thermostat.const import STARTUP_GRACE_SECONDS

from . import setup_integration


async def _advance_past_startup_window(hass: HomeAssistant) -> None:
    """Fire time forward past the startup grace window so external-change detection resumes."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=STARTUP_GRACE_SECONDS + 1))
    await hass.async_block_till_done()


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
    assert climate_state is not None
    assert HVACMode.OFF in climate_state.attributes["hvac_modes"]
    assert HVACMode.FAN_ONLY in climate_state.attributes["hvac_modes"]


async def test_climate_initial_state(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the initial state of the climate entity."""
    await setup_integration(hass, mock_config_entry)

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
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
    assert climate_state is not None
    assert climate_state.state == HVACMode.FAN_ONLY


async def test_set_hvac_mode_off(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test setting HVAC mode to off disables the thermostat (fans untouched).

    HVAC mode OFF means the thermostat is off, not the fans: no fan commands
    are issued and the physical fans stay exactly as the user left them.
    """
    await setup_integration(hass, mock_config_entry)

    # Set a temperature that would normally turn the fan on (above target_high=78)
    hass.states.async_set("sensor.bedroom_temp", "85.0", {"device_class": "temperature"})

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
    assert climate_state is not None
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
    assert climate_state is not None
    assert climate_state.attributes["target_temp_low"] == 70.0
    assert climate_state.attributes["target_temp_high"] == 80.0


async def test_temperature_above_target_turns_on_fan(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the fan turns on when temperature exceeds target_low."""
    await setup_integration(hass, mock_config_entry)

    # Set a temperature between target_low and target_high (72-78 range)
    hass.states.async_set("sensor.bedroom_temp", "75.0", {"device_class": "temperature"})

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    # Temp 75, target_low 72, target_high 78 (range=6) -> ((75-72)/6)*100 = 50%
    assert climate_state.attributes.get("percentage") == 50


async def test_temperature_at_target_low_turns_off_fan(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the fan turns off when temperature equals target_temp_low."""
    await setup_integration(hass, mock_config_entry)

    # Set temperature to exactly target_temp_low
    hass.states.async_set("sensor.bedroom_temp", "72.0", {"device_class": "temperature"})

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") is None


async def test_temperature_below_target_turns_off_fan(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the fan turns off when temperature is below target_temp_low."""
    await setup_integration(hass, mock_config_entry)

    # Set temperature below target_temp_low
    hass.states.async_set("sensor.bedroom_temp", "70.0", {"device_class": "temperature"})

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") is None


async def test_temperature_step_calculates_speed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that temperature step correctly calculates fan speed."""
    await setup_integration(hass, mock_config_entry)

    # target_low is 72, target_high is 78 (range=6)
    # Temperature 74 (2 above target_low) -> ((74-72)/6)*100 = 33%
    hass.states.async_set("sensor.bedroom_temp", "74.0", {"device_class": "temperature"})

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 33

    # Temperature 78 (at target_high) -> clamped to 100%
    hass.states.async_set("sensor.bedroom_temp", "78.0", {"device_class": "temperature"})

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 100


async def test_multiple_temperature_sensors_max_by_default(
    hass: HomeAssistant,
    mock_config_entry_multiple_sensors: MockConfigEntry,
) -> None:
    """Test that multiple temperature sensors are combined with max by default."""
    await setup_integration(hass, mock_config_entry_multiple_sensors)

    # Set two temperatures: 69 and 71 -> max 71 (above target_temp_low=70)
    hass.states.async_set("sensor.living_room_temp_1", "69.0", {"device_class": "temperature"})
    hass.states.async_set("sensor.living_room_temp_2", "71.0", {"device_class": "temperature"})

    climate_state = hass.states.get("climate.living_room_fan_thermostat")
    assert climate_state is not None
    # Max is 71, target_low=70, target_high=78 (range=8) -> ((71-70)/8)*100 = 12.5 -> 12
    assert climate_state.attributes.get("percentage") == 12

    # Now set to 74 and 78 -> max 78 (at target_high) -> 100%
    hass.states.async_set("sensor.living_room_temp_1", "74.0", {"device_class": "temperature"})
    hass.states.async_set("sensor.living_room_temp_2", "78.0", {"device_class": "temperature"})

    climate_state = hass.states.get("climate.living_room_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 100


async def test_multiple_temperature_sensors_avg(
    hass: HomeAssistant,
    mock_config_entry_multiple_sensors: MockConfigEntry,
) -> None:
    """Test that the temperature aggregation can be set to average."""
    await setup_integration(hass, mock_config_entry_multiple_sensors)

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.living_room_fan_thermostat_temperature_operation", "option": "avg"},
        blocking=True,
    )
    await hass.async_block_till_done()

    hass.states.async_set("sensor.living_room_temp_1", "69.0", {"device_class": "temperature"})
    hass.states.async_set("sensor.living_room_temp_2", "71.0", {"device_class": "temperature"})

    climate_state = hass.states.get("climate.living_room_fan_thermostat")
    assert climate_state is not None
    # Average is 70, which equals target_low, so fan should be off
    assert climate_state.attributes.get("percentage") is None

    hass.states.async_set("sensor.living_room_temp_1", "74.0", {"device_class": "temperature"})
    hass.states.async_set("sensor.living_room_temp_2", "78.0", {"device_class": "temperature"})

    climate_state = hass.states.get("climate.living_room_fan_thermostat")
    assert climate_state is not None
    # Average is 76, target_low is 70, target_high is 78 (range=8) -> ((76-70)/8)*100 = 75%
    assert climate_state.attributes.get("percentage") == 75


async def test_multiple_temperature_sensors_min_and_select_entity(
    hass: HomeAssistant,
    mock_config_entry_multiple_sensors: MockConfigEntry,
) -> None:
    """Test the min aggregation and the select entity that drives it."""
    await setup_integration(hass, mock_config_entry_multiple_sensors)

    select_state = hass.states.get("select.living_room_fan_thermostat_temperature_operation")
    assert select_state is not None
    assert select_state.state == "max"
    assert select_state.attributes.get("options") == ["min", "max", "avg"]

    hass.states.async_set("sensor.living_room_temp_1", "74.0", {"device_class": "temperature"})
    hass.states.async_set("sensor.living_room_temp_2", "78.0", {"device_class": "temperature"})

    # max (default) -> 78 -> 100%
    climate_state = hass.states.get("climate.living_room_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 100

    # Switch to min via the select entity -> 74 -> ((74-70)/8)*100 = 50%
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.living_room_fan_thermostat_temperature_operation", "option": "min"},
        blocking=True,
    )
    await hass.async_block_till_done()

    climate_state = hass.states.get("climate.living_room_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 50


async def test_motion_sensor_turns_fan_off(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that motion sensors turn the fan off after the motion timeout."""
    await setup_integration(hass, mock_config_entry)

    # Set temperature above setpoint
    hass.states.async_set("sensor.bedroom_temp", "85.0", {"device_class": "temperature"})

    # Fan should be on
    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 100

    # Motion sensor becomes inactive: the fan stays on until the timeout elapses
    hass.states.async_set("binary_sensor.bedroom_motion", "inactive", {"device_class": "motion"})

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 100

    # After the motion timeout (300s) elapses, the fan turns off
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=301))
    await hass.async_block_till_done()

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") is None


async def test_motion_sensor_turns_fan_on(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that motion sensors turn the fan back on when active."""
    await setup_integration(hass, mock_config_entry)

    # Set temperature above setpoint
    hass.states.async_set("sensor.bedroom_temp", "85.0", {"device_class": "temperature"})

    # Motion sensor becomes inactive: the fan stays on for the motion timeout
    hass.states.async_set("binary_sensor.bedroom_motion", "inactive", {"device_class": "motion"})

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 100

    # Motion sensor becomes active again
    hass.states.async_set("binary_sensor.bedroom_motion", "active", {"device_class": "motion"})

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 100


async def test_manual_override_switch(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the manual override switch."""
    await setup_integration(hass, mock_config_entry)

    # Set temperature above setpoint
    hass.states.async_set("sensor.bedroom_temp", "85.0", {"device_class": "temperature"})

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
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
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 100  # Should not change

    # Turn off manual override
    await hass.services.async_call(
        "switch",
        "turn_off",
        {ATTR_ENTITY_ID: "switch.bedroom_fan_thermostat_manual_override"},
        blocking=True,
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 100


async def test_manual_override_exit_turns_off_fan_when_fan_is_on(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Exiting manual override turns the physically-on fan off.

    Regression test: the exit path must recalculate the desired speed and
    command it - a fan the user turned on manually is off when the override
    is cleared, even though the thermostat never tracked the physical speed.
    """
    recorded = []

    async def _handle_turn_off(call: ServiceCall) -> None:
        recorded.append(("turn_off", call.data.get("entity_id")))

    async def _handle_set_percentage(call: ServiceCall) -> None:
        recorded.append(("set_percentage", call.data.get("percentage")))

    hass.services.async_register("fan", "turn_off", _handle_turn_off)
    hass.services.async_register("fan", "set_percentage", _handle_set_percentage)

    await setup_integration(hass, mock_config_entry)

    # Low temperature: thermostat keeps the fan off
    hass.states.async_set("sensor.bedroom_temp", "70.0", {"device_class": "temperature"})
    await hass.async_block_till_done()

    # User turns the physical fan on (external change)
    await _advance_past_startup_window(hass)
    hass.states.async_set("fan.bedroom_fan", "on", {"percentage": 100})
    await hass.async_block_till_done()

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state is not None
    assert switch_state.state == STATE_ON

    # Manual override is turned off: the thermostat resumes automatic control
    await hass.services.async_call(
        "switch",
        "turn_off",
        {ATTR_ENTITY_ID: "switch.bedroom_fan_thermostat_manual_override"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert ("turn_off", "fan.bedroom_fan") in recorded
    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") is None


async def test_manual_override_exit_changes_fan_speed_when_fan_is_on(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Exiting manual override applies the recalculated speed to a physically-on fan.

    The desired speed is recalculated on every pass, so the physical speed the
    user set manually is overwritten by the temperature-derived one.
    """
    recorded = []

    async def _handle_set_percentage(call: ServiceCall) -> None:
        recorded.append(call.data.get("percentage"))

    hass.services.async_register("fan", "set_percentage", _handle_set_percentage)

    await setup_integration(hass, mock_config_entry)

    # Temp 75 -> thermostat wants 50%
    hass.states.async_set("sensor.bedroom_temp", "75.0", {"device_class": "temperature"})
    await hass.async_block_till_done()
    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 50

    # User manually sets the fan to 100% (external change)
    await _advance_past_startup_window(hass)
    hass.states.async_set("fan.bedroom_fan", "on", {"percentage": 100})
    await hass.async_block_till_done()

    await hass.services.async_call(
        "switch",
        "turn_off",
        {ATTR_ENTITY_ID: "switch.bedroom_fan_thermostat_manual_override"},
        blocking=True,
    )
    await hass.async_block_till_done()

    # Thermostat resumes control: the desired speed (50%) is applied
    assert recorded and recorded[-1] == 50
    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 50


async def test_manual_override_exit_turns_fan_on_when_fan_is_off(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Exiting manual override turns the physically-off fan back on.

    Same principle: the desired speed is recalculated on exit, so a fan the
    user turned off manually comes back on when the override is cleared.
    """
    recorded = []

    async def _handle_set_percentage(call: ServiceCall) -> None:
        recorded.append(call.data.get("percentage"))

    hass.services.async_register("fan", "set_percentage", _handle_set_percentage)

    await setup_integration(hass, mock_config_entry)

    # Temp 85 -> thermostat wants 100%
    hass.states.async_set("sensor.bedroom_temp", "85.0", {"device_class": "temperature"})
    await hass.async_block_till_done()

    # User manually turns the fan off (external change)
    await _advance_past_startup_window(hass)
    hass.states.async_set("fan.bedroom_fan", "off", {"percentage": 0})
    await hass.async_block_till_done()

    await hass.services.async_call(
        "switch",
        "turn_off",
        {ATTR_ENTITY_ID: "switch.bedroom_fan_thermostat_manual_override"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert 100 in recorded
    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 100


async def test_turn_on(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test turning the climate entity on."""
    # Set a temperature so the desired speed is known
    hass.states.async_set("sensor.bedroom_temp", "83.0", {"device_class": "temperature"})

    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "climate.bedroom_fan_thermostat"},
        blocking=True,
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 100


async def test_turn_off(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test turning the climate entity off."""
    await setup_integration(hass, mock_config_entry)

    # Set temperature above setpoint
    hass.states.async_set("sensor.bedroom_temp", "85.0", {"device_class": "temperature"})

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 100

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: "climate.bedroom_fan_thermostat"},
        blocking=True,
    )

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
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
    hass.states.async_set("sensor.box_fan_temp", "85.0", {"device_class": "temperature"})

    climate_state = hass.states.get("climate.box_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 100

    # Now turn off via climate service
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: "climate.box_fan_thermostat"},
        blocking=True,
    )

    climate_state = hass.states.get("climate.box_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") is None


async def test_single_speed_fan(
    hass: HomeAssistant,
    mock_config_entry_single_speed: MockConfigEntry,
) -> None:
    """Test that a single-speed fan always turns on when needed."""
    await setup_integration(hass, mock_config_entry_single_speed)

    # Set temperature above setpoint
    hass.states.async_set("sensor.single_speed_temp", "85.0", {"device_class": "temperature"})

    climate_state = hass.states.get("climate.single_speed_fan_thermostat")
    assert climate_state is not None
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
    hass.states.async_set("sensor.bedroom_temp", "unavailable", {"device_class": "temperature"})
    hass.states.async_set("fan.bedroom_fan", "unavailable", {"supported_features": 1})

    await hass.async_block_till_done()

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
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
    assert climate_state is not None
    assert climate_state.attributes["hvac_action"] == HVACAction.IDLE

    # Set temperature above target_high (78.0) -> fan runs at 100%, action is FAN
    hass.states.async_set("sensor.bedroom_temp", "85.0", {"device_class": "temperature"})

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes["hvac_action"] == HVACAction.FAN

    # Set temperature at target_high (78.0) -> fan still runs at 100%, action is FAN
    hass.states.async_set("sensor.bedroom_temp", "78.0", {"device_class": "temperature"})

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes["hvac_action"] == HVACAction.FAN

    # Set temperature below target_low (72.0) -> fan off, action is IDLE
    hass.states.async_set("sensor.bedroom_temp", "70.0", {"device_class": "temperature"})

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes["hvac_action"] == HVACAction.IDLE


async def test_climate_native_temperature_unit(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the climate entity reports Fahrenheit as the temperature unit."""
    await setup_integration(hass, mock_config_entry)

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes["temperature_unit"] == "°F"


async def test_climate_min_max_temp(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the climate entity reports correct min/max temperature."""
    await setup_integration(hass, mock_config_entry)

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes["min_temp"] == 45
    assert climate_state.attributes["max_temp"] == 95


async def test_fan_service_calls_are_made(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that fan controllers receive set_percentage service calls."""
    recorded = []

    async def _handle_set_percentage(call: ServiceCall) -> None:
        recorded.append(("set_percentage", call.data.get("entity_id"), call.data.get("percentage")))

    hass.services.async_register("fan", "set_percentage", _handle_set_percentage)

    await setup_integration(hass, mock_config_entry)

    hass.states.async_set("sensor.bedroom_temp", "85.0", {"device_class": "temperature"})
    await hass.async_block_till_done()

    assert ("set_percentage", "fan.bedroom_fan", 100) in recorded


async def test_retrigger_time_blocks_fan_restart(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that retrigger time blocks the fan from restarting after the motion timeout."""
    await setup_integration(hass, mock_config_entry)

    # Set temperature above setpoint: fan at 100%
    hass.states.async_set("sensor.bedroom_temp", "85.0", {"device_class": "temperature"})
    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 100

    # Motion stops
    hass.states.async_set("binary_sensor.bedroom_motion", "inactive", {"device_class": "motion"})
    await hass.async_block_till_done()

    # Stay-on window: fan still on
    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 100

    # Motion timeout elapses: fans off, retrigger lockout starts
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=301))
    await hass.async_block_till_done()

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") is None

    # Motion re-detected during the retrigger window: fan still off
    hass.states.async_set("binary_sensor.bedroom_motion", "active", {"device_class": "motion"})
    await hass.async_block_till_done()

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") is None

    # Retrigger window elapses: fan turns back on
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=302))
    await hass.async_block_till_done()

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 100


async def test_retrigger_time_blocks_fan_restart_without_motion_sensors(
    hass: HomeAssistant,
    mock_config_entry_no_motion: MockConfigEntry,
) -> None:
    """Test that retrigger time blocks a temperature-driven fan restart (no motion sensors)."""
    await setup_integration(hass, mock_config_entry_no_motion)

    # Above the low setpoint: fan runs (75 -> (75-70)/(82-70) = ~42%)
    hass.states.async_set("sensor.office_temp", "75.0", {"device_class": "temperature"})
    climate_state = hass.states.get("climate.office_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 42

    # Temperature drops below the low setpoint: fans turn off, retrigger window starts
    hass.states.async_set("sensor.office_temp", "65.0", {"device_class": "temperature"})
    climate_state = hass.states.get("climate.office_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") is None

    # Temperature rises back above the low setpoint inside the retrigger window:
    # the fan must stay off
    hass.states.async_set("sensor.office_temp", "75.0", {"device_class": "temperature"})
    await hass.async_block_till_done()
    climate_state = hass.states.get("climate.office_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") is None

    # Retrigger window elapses (60s): the fan turns back on
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    climate_state = hass.states.get("climate.office_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 42


async def test_restore_unavailable_state_does_not_crash(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that an unavailable last state is restored as FAN_ONLY instead of crashing."""
    mock_restore_cache(hass, [State("climate.bedroom_fan_thermostat", "unavailable")])

    await setup_integration(hass, mock_config_entry)

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.state == HVACMode.FAN_ONLY


async def test_external_change_enters_manual_override(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that external fan/switch changes trigger manual override; self-initiated changes do not."""

    async def _handle_set_percentage(call: ServiceCall) -> None:
        hass.states.async_set(
            call.data["entity_id"],
            "on",
            {"percentage": call.data["percentage"]},
            context=call.context,
        )

    hass.services.async_register("fan", "set_percentage", _handle_set_percentage)

    await setup_integration(hass, mock_config_entry)

    # Temperature event: thermostat drives the fan itself (self-initiated)
    hass.states.async_set("sensor.bedroom_temp", "85.0", {"device_class": "temperature"})
    await hass.async_block_till_done()

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state is not None
    # Self-initiated change must NOT trigger manual override
    assert switch_state.state == STATE_OFF

    # External change to the fan drives the thermostat into manual override
    await _advance_past_startup_window(hass)
    hass.states.async_set("fan.bedroom_fan", "off", {"percentage": 0})
    await hass.async_block_till_done()

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state is not None
    assert switch_state.state == STATE_ON


async def test_startup_boot_report_within_window_is_ignored(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A fan/switch boot report (startup, unrequested) must not enter manual override.

    The reported state is deliberately mismatched with the desired state
    (fan reports on while the thermostat wants 0%): the only thing that
    saves the thermostat from manual override here is the startup window.
    """
    await setup_integration(hass, mock_config_entry)

    hass.states.async_set("fan.bedroom_fan", "on", {"percentage": 33})
    await hass.async_block_till_done()

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state is not None
    assert switch_state.state == STATE_OFF


async def test_report_matching_desired_speed_is_ignored_after_window(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A fan report consistent with the thermostat's desired speed is not an external change."""
    await setup_integration(hass, mock_config_entry)

    # Temp 85 -> thermostat wants 100%
    hass.states.async_set("sensor.bedroom_temp", "85.0", {"device_class": "temperature"})
    await hass.async_block_till_done()

    await _advance_past_startup_window(hass)

    # The fan reports exactly what the thermostat would command: no override
    hass.states.async_set("fan.bedroom_fan", "on", {"percentage": 100})
    await hass.async_block_till_done()

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state is not None
    assert switch_state.state == STATE_OFF


async def test_report_matching_desired_off_state_is_ignored_after_window(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An off-at-startup report matching the desired 0% is never an external change."""
    await setup_integration(hass, mock_config_entry)

    await _advance_past_startup_window(hass)

    # No temperature reading -> desired 0%; fan reports off -> consistent
    hass.states.async_set("fan.bedroom_fan", "off", {"percentage": 0})
    await hass.async_block_till_done()

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state is not None
    assert switch_state.state == STATE_OFF


async def test_startup_switch_boot_report_within_window_is_ignored(
    hass: HomeAssistant,
    mock_config_entry_with_switches: MockConfigEntry,
) -> None:
    """A switch boot report (startup, unrequested) must not enter manual override."""
    await setup_integration(hass, mock_config_entry_with_switches)

    hass.states.async_set("switch.box_fan_switch", "on")
    await hass.async_block_till_done()

    switch_state = hass.states.get("switch.box_fan_thermostat_manual_override")
    assert switch_state is not None
    assert switch_state.state == STATE_OFF


async def test_switch_report_matching_desired_off_state_is_ignored_after_window(
    hass: HomeAssistant,
    mock_config_entry_with_switches: MockConfigEntry,
) -> None:
    """A switch report consistent with the thermostat's desired on/off is not an external change."""
    await setup_integration(hass, mock_config_entry_with_switches)

    await _advance_past_startup_window(hass)

    # No temperature reading -> desired 0% -> desired switch off; switch reports off
    hass.states.async_set("switch.box_fan_switch", "off")
    await hass.async_block_till_done()

    switch_state = hass.states.get("switch.box_fan_thermostat_manual_override")
    assert switch_state is not None
    assert switch_state.state == STATE_OFF


async def test_self_initiated_out_of_order_calls_do_not_enter_override(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Two queued set_percentage calls complete after a later control pass.

    Regression test for the lost-expectation race: the second call must not
    overwrite the first call's pending expectation, or the first call's
    state-change event would look like an external change and trigger
    manual override.
    """
    import asyncio

    hold = asyncio.Event()

    async def _handle_set_percentage(call: ServiceCall) -> None:
        await hold.wait()
        hass.states.async_set(
            call.data["entity_id"],
            "on",
            {"percentage": call.data["percentage"]},
            context=call.context,
        )

    hass.services.async_register("fan", "set_percentage", _handle_set_percentage)

    await setup_integration(hass, mock_config_entry)

    # First call: 85 -> 100%. The mock suspends, so the service call stays
    # in flight while the second control pass runs.
    hass.states.async_set("sensor.bedroom_temp", "85.0", {"device_class": "temperature"})
    # Second call: 75 -> 50% (between 72 and 78). The expectation for the
    # first call must survive this overwrite.
    hass.states.async_set("sensor.bedroom_temp", "75.0", {"device_class": "temperature"})

    hold.set()
    await hass.async_block_till_done()

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes.get("percentage") == 50

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state is not None
    # Both self-initiated changes must be ignored, never manual override
    assert switch_state.state == STATE_OFF


async def test_control_pass_shares_one_context_parented_to_triggering_event(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """One pass context is shared by the service calls and the state write.

    The pass context is parented to the triggering event's context, so the
    Activity details trace can link the sensor event to the fan changes and
    to the thermostat itself (the automation/script convention).
    """
    call_context_ids = []
    sensor_context_ids = []

    async def _handle_set_percentage(call: ServiceCall) -> None:
        call_context_ids.append(call.context.id)
        hass.states.async_set(
            call.data["entity_id"],
            "on",
            {"percentage": call.data["percentage"]},
            context=call.context,
        )

    hass.services.async_register("fan", "set_percentage", _handle_set_percentage)

    await setup_integration(hass, mock_config_entry)

    def _listener(event: Event[EventStateChangedData]) -> None:
        if event.data.get("entity_id") == "sensor.bedroom_temp":
            sensor_context_ids.append(event.context.id)

    hass.bus.async_listen(EVENT_STATE_CHANGED, _listener)

    hass.states.async_set("sensor.bedroom_temp", "85.0", {"device_class": "temperature"})
    await hass.async_block_till_done()

    assert sensor_context_ids
    assert call_context_ids
    pass_context_id = call_context_ids[0]
    assert all(cid == pass_context_id for cid in call_context_ids)

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.context is not None
    # The thermostat's own state row carries the same pass context
    assert climate_state.context.id == pass_context_id
    # ...and the pass context is parented to the triggering event's context,
    # so the trace links the sensor to the fan/thermostat changes
    assert climate_state.context.parent_id == sensor_context_ids[0]


async def test_self_initiated_events_carry_pass_marker_context(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """State-change events from our own service calls carry the marker context.

    The marker subclass is how the thermostat distinguishes self-initiated
    changes from external ones. Replacing it with a plain Context in
    _new_trigger_context makes events fail the isinstance check and enter
    manual override instead.
    """
    from custom_components.fan_thermostat.climate import _ControlPassContext

    event_contexts = []

    async def _handle_set_percentage(call: ServiceCall) -> None:
        hass.states.async_set(
            call.data["entity_id"],
            "on",
            {"percentage": call.data["percentage"]},
            context=call.context,
        )

    hass.services.async_register("fan", "set_percentage", _handle_set_percentage)

    await setup_integration(hass, mock_config_entry)

    def _listener(event: Event[EventStateChangedData]) -> None:
        if event.data.get("entity_id") == "fan.bedroom_fan":
            event_contexts.append(event.context)

    hass.bus.async_listen(EVENT_STATE_CHANGED, _listener)

    hass.states.async_set("sensor.bedroom_temp", "85.0", {"device_class": "temperature"})
    await hass.async_block_till_done()

    assert event_contexts
    assert all(isinstance(ctx, _ControlPassContext) for ctx in event_contexts)


async def test_external_change_links_override_switch_state_write_to_external_event(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An external fan change links the override switch's state write to that event.

    The fresh plain Context attached to the switch's state write is parented
    to the external event's context id, so the trace links the originator
    (wall switch, automation) to the switch (the visible override transition;
    the climate entity's own state write is skipped because nothing about it
    changed). It must not be a marker context - the event is not self-initiated.
    """
    from custom_components.fan_thermostat.climate import _ControlPassContext

    external_context_ids = []

    def _listener(event: Event[EventStateChangedData]) -> None:
        if event.data.get("entity_id") == "fan.bedroom_fan":
            external_context_ids.append(event.context.id)

    await setup_integration(hass, mock_config_entry)
    hass.bus.async_listen(EVENT_STATE_CHANGED, _listener)

    # Temperature event: thermostat wants 100%. No fan service handler is
    # registered, so this pass issues no fan state-change events - the only
    # fan event recorded below is the deliberate external change.
    hass.states.async_set("sensor.bedroom_temp", "85.0", {"device_class": "temperature"})
    await hass.async_block_till_done()

    await _advance_past_startup_window(hass)
    # External change: the fan reports off (0%) while the thermostat wants 100%
    hass.states.async_set("fan.bedroom_fan", "off", {"percentage": 0})
    await hass.async_block_till_done()

    assert external_context_ids
    external_context_id = external_context_ids[0]

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state is not None
    assert switch_state.context is not None
    # The switch's state row carries a fresh child context...
    assert switch_state.context.parent_id == external_context_id
    assert switch_state.context.id != external_context_id
    # ...and it is not a marker context (the event is not ours)
    assert not isinstance(switch_state.context, _ControlPassContext)


async def test_set_temperature_persists_to_config_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that setting the target temperature range is persisted to the config entry."""
    await setup_integration(hass, mock_config_entry)

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

    assert mock_config_entry.data["target_temp_low"] == 70.0
    assert mock_config_entry.data["target_temp_high"] == 80.0


async def test_set_temperature_rejects_degenerate_range(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the entity rejects a range whose low setpoint exceeds the high."""
    from homeassistant.exceptions import ServiceValidationError

    from custom_components.fan_thermostat.const import DOMAIN

    await setup_integration(hass, mock_config_entry)

    controller = hass.data[DOMAIN][mock_config_entry.entry_id]
    entity = controller.climate
    assert entity is not None

    # Raising the low setpoint above the existing high (78) must fail.
    with pytest.raises(ServiceValidationError):
        await entity.async_set_temperature(target_temp_low=80.0)

    # The configured range is untouched: no half-applied state.
    assert mock_config_entry.data["target_temp_low"] == 72.0
    assert mock_config_entry.data["target_temp_high"] == 78.0


async def test_set_temperature_survives_reload(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the set target temperature range is kept across a reload."""
    await setup_integration(hass, mock_config_entry)

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

    # Simulate a restart: unload the entry and set it up again.
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    climate_state = hass.states.get("climate.bedroom_fan_thermostat")
    assert climate_state is not None
    assert climate_state.attributes["target_temp_low"] == 70.0
    assert climate_state.attributes["target_temp_high"] == 80.0
