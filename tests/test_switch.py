"""Tests for the Fan Thermostat switch platform."""

from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
)
from homeassistant.components.switch import (
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

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
    entity_entry = entity_registry.async_get("switch.bedroom_fan_thermostat_manual_override")
    assert entity_entry is not None
    assert entity_entry.unique_id == "bedroom_fan_thermostat_override"


async def test_switch_turn_on(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test turning the switch on."""
    await setup_integration(hass, mock_config_entry)

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state is not None
    assert switch_state.state == STATE_OFF

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "switch.bedroom_fan_thermostat_manual_override"},
        blocking=True,
    )

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state is not None
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
    assert switch_state is not None
    assert switch_state.state == STATE_ON

    # Turn off
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: "switch.bedroom_fan_thermostat_manual_override"},
        blocking=True,
    )

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state is not None
    assert switch_state.state == STATE_OFF


async def test_switch_available(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the switch entity is always available."""
    await setup_integration(hass, mock_config_entry)

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state is not None
    assert switch_state.attributes.get("available", True) is True


async def test_switch_restore_re_arms_remaining_time(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that a restored override re-arms only the time remaining at shutdown."""
    from datetime import timedelta

    from homeassistant.core import State
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant.common import async_fire_time_changed, mock_restore_cache

    # Override was turned on 240s before shutdown; manual_override_time is 300s.
    mock_restore_cache(
        hass,
        [
            State(
                "switch.bedroom_fan_thermostat_manual_override",
                "on",
                last_changed=dt_util.utcnow() - timedelta(seconds=240),
            )
        ],
    )

    hass.states.async_set("sensor.bedroom_temp", "72.0", {"device_class": "temperature"})
    await setup_integration(hass, mock_config_entry)

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state is not None
    assert switch_state.state == STATE_ON

    # The remaining 60s (not the full 300s) is re-armed.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state is not None
    assert switch_state.state == STATE_OFF


async def test_switch_restore_expired_override_not_restored(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that an override that expired while the instance was down is not restored."""
    from datetime import timedelta

    from homeassistant.core import State
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant.common import mock_restore_cache

    # Override was turned on 400s before shutdown; manual_override_time is 300s.
    mock_restore_cache(
        hass,
        [
            State(
                "switch.bedroom_fan_thermostat_manual_override",
                "on",
                last_changed=dt_util.utcnow() - timedelta(seconds=400),
            )
        ],
    )

    hass.states.async_set("sensor.bedroom_temp", "72.0", {"device_class": "temperature"})
    await setup_integration(hass, mock_config_entry)

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state is not None
    assert switch_state.state == STATE_OFF


async def test_switch_restore_no_expiry_restores_indefinitely(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that a restored override with auto-expiry disabled stays on."""
    from datetime import timedelta

    from homeassistant.core import State
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant.common import async_fire_time_changed, mock_restore_cache

    mock_config_entry_no_expiry = MockConfigEntry(
        domain=mock_config_entry.domain,
        title="No Expiry",
        data={
            **mock_config_entry.data,
            "name": "No Expiry",
            "manual_override_time": 0,
        },
        unique_id="no_expiry",
    )

    mock_restore_cache(
        hass,
        [
            State(
                "switch.no_expiry_manual_override",
                "on",
                last_changed=dt_util.utcnow() - timedelta(seconds=30),
            )
        ],
    )

    hass.states.async_set("sensor.bedroom_temp", "72.0", {"device_class": "temperature"})
    await setup_integration(hass, mock_config_entry_no_expiry)

    switch_state = hass.states.get("switch.no_expiry_manual_override")
    assert switch_state is not None
    assert switch_state.state == STATE_ON

    # No auto-expiry is armed.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3600))
    await hass.async_block_till_done()

    switch_state = hass.states.get("switch.no_expiry_manual_override")
    assert switch_state is not None
    assert switch_state.state == STATE_ON
