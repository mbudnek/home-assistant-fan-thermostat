"""Tests for the Fan Thermostat switch platform."""

from pytest_homeassistant.common import MockConfigEntry

from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import setup_integration


async def test_switch_entity_exists(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test that the switch entity is created."""
    await setup_integration(hass, mock_config_entry)

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state is not None
    assert switch_state.state == STATE_OFF

    # Check entity registry
    entity_entry = entity_registry.async_get(
        "switch.bedroom_fan_thermostat_manual_override"
    )
    assert entity_entry is not None
    assert entity_entry.unique_id == "bedroom_fan_thermostat_override"


async def test_switch_turn_on(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test turning the switch on."""
    await setup_integration(hass, mock_config_entry)

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state.state == STATE_OFF

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "switch.bedroom_fan_thermostat_manual_override"},
        blocking=True,
    )

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state.state == STATE_ON


async def test_switch_turn_off(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test turning the switch off."""
    await setup_integration(hass, mock_config_entry)

    # Turn on first
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "switch.bedroom_fan_thermostat_manual_override"},
        blocking=True,
    )

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state.state == STATE_ON

    # Turn off
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: "switch.bedroom_fan_thermostat_manual_override"},
        blocking=True,
    )

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state.state == STATE_OFF


async def test_switch_available(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the switch entity is always available."""
    await setup_integration(hass, mock_config_entry)

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state.attributes.get("available", True) is True
