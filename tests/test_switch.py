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
    from pytest_homeassistant.common import async_fire_time_changed, mock_restore_cache_with_extra_data

    # Override was turned on 240s before shutdown; manual_override_time is 300s,
    # so the persisted end time is 60s from now.
    end = dt_util.utcnow() + timedelta(seconds=60)
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("switch.bedroom_fan_thermostat_manual_override", "on"),
                {"override_end": end.isoformat()},
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
    from pytest_homeassistant.common import mock_restore_cache_with_extra_data

    # The override's end time was 100s ago (it expired while the instance was down).
    end = dt_util.utcnow() - timedelta(seconds=100)
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("switch.bedroom_fan_thermostat_manual_override", "on"),
                {"override_end": end.isoformat()},
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
            State("switch.no_expiry_manual_override", "on"),
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


async def test_switch_restore_uses_persisted_end_time(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that a restored override is re-enabled with its persisted end time."""
    from datetime import timedelta

    from homeassistant.core import State
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant.common import (
        async_fire_time_changed,
        mock_restore_cache_with_extra_data,
    )

    # Persisted at shutdown: the override is on with an end time of 120s
    # from now. The entity must honor the persisted end time, not the stale
    # last_changed.
    end = dt_util.utcnow() + timedelta(seconds=120)
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(
                    "switch.bedroom_fan_thermostat_manual_override",
                    "on",
                    last_changed=dt_util.utcnow() - timedelta(seconds=240),
                ),
                {"override_end": end.isoformat()},
            )
        ],
    )

    hass.states.async_set("sensor.bedroom_temp", "72.0", {"device_class": "temperature"})
    await setup_integration(hass, mock_config_entry)

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state is not None
    assert switch_state.state == STATE_ON

    # The restored entity keeps the persisted end time (not a freshly
    # derived one) so it can be re-persisted verbatim on the next shutdown.
    from custom_components.fan_thermostat.const import DOMAIN

    controller = hass.data[DOMAIN][mock_config_entry.entry_id]
    assert controller.switch is not None
    extra = controller.switch.extra_restore_state_data
    assert extra is not None
    assert extra.as_dict()["override_end"] == end.isoformat()

    # Only the 120s remaining (not the full 300s) is re-armed.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=121))
    await hass.async_block_till_done()

    switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
    assert switch_state is not None
    assert switch_state.state == STATE_OFF


async def test_switch_restore_persisted_end_time_survives_multiple_restarts(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that re-restarting does not extend the override beyond its end time.

    Regression test: the entity platform writes the restored state with a
    fresh last_changed on every restart, which made the elapsed-based
    restore re-arm the full duration each time. The persisted end time must
    be honored across restarts instead.
    """
    from datetime import timedelta

    from freezegun import freeze_time
    from homeassistant.helpers import restore_state as rs
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant.common import async_fire_time_changed

    # Run 1: enable the override at 12:00:00 (300s duration, so ends 12:05:00).
    with freeze_time("2026-06-01 12:00:00+00:00"):
        hass.states.async_set("sensor.bedroom_temp", "74.0", {"device_class": "temperature"})
        await setup_integration(hass, mock_config_entry)

        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: "switch.bedroom_fan_thermostat_manual_override"},
            blocking=True,
        )

        switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
        assert switch_state is not None
        assert switch_state.state == STATE_ON

    # Shutdown and restart #1 after 3 minutes: the restored entity re-arms
    # the remaining 2 minutes, and the fresh state write (fresh last_changed)
    # must not move the persisted end time.
    with freeze_time("2026-06-01 12:03:00+00:00"):
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
        assert switch_state is not None
        assert switch_state.state == STATE_ON

        # The persisted end time still points at the original expiry.
        data = rs.async_get(hass)
        stored = data.async_get_stored_state("switch.bedroom_fan_thermostat_manual_override")
        assert stored is not None
        assert stored.extra_data is not None
        assert stored.extra_data.as_dict()["override_end"] == "2026-06-01T12:05:00+00:00"

    # Shutdown and restart #2 after one more minute: a stale last_changed
    # from restart #1 would re-arm a fresh 300s; the persisted end time must
    # re-arm only the 1 remaining minute (ends 12:05:00).
    with freeze_time("2026-06-01 12:04:00+00:00"):
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
        assert switch_state is not None
        assert switch_state.state == STATE_ON

        # Firing 121s into restart #2 must clear the override (re-armed with
        # the 60s remaining at restart #2), proving the fresh last_changed
        # from restart #1 did not reset the re-armed duration.
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=121))
        await hass.async_block_till_done()

        switch_state = hass.states.get("switch.bedroom_fan_thermostat_manual_override")
        assert switch_state is not None
        assert switch_state.state == STATE_OFF
