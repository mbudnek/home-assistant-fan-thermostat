"""Standalone test fixtures for Fan Thermostat integration."""

from collections.abc import Iterator

import pytest
from fan_thermostat.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import IMPERIAL_SYSTEM
from pytest_homeassistant.common import MockConfigEntry


# Fail fast instead of hanging when asyncio's wakeup self-pipe is blocked.
# Sandboxed environments (e.g. the Codex sandbox) block AF_UNIX socketpair
# sends with EPERM. asyncio uses a socketpair to wake the loop from worker
# threads; when the send fails silently, `run_in_executor` futures never
# resolve and tests hang forever (setup, teardown, and runner shutdown).
# When we detect the blocked send, bound the two executor-shutdown waits so
# tests fail instead of hanging. Outside the sandbox none of this is applied
# and behavior is unchanged.
def _self_pipe_send_blocked() -> bool:
    import socket

    try:
        pair = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError:
        return True
    try:
        pair[0].send(b"\0")
        return False
    except OSError:
        return True
    finally:
        pair[0].close()
        pair[1].close()


if _self_pipe_send_blocked():
    import asyncio.base_events
    import asyncio.constants

    # Intentionally overridden for the sandbox workaround. THREAD_JOIN_TIMEOUT
    # is unannotated in the stdlib (`THREAD_JOIN_TIMEOUT = 300`), so ty infers
    # its type as the literal `Literal[300]` and flags the assignment. Suppress
    # that diagnostic.
    asyncio.constants.THREAD_JOIN_TIMEOUT = 1.0  # ty: ignore[invalid-assignment]

    _DEFAULT_EXECUTOR_SHUTDOWN_TIMEOUT = 1.0
    _orig_shutdown_default_executor = asyncio.base_events.BaseEventLoop.shutdown_default_executor

    async def _bounded_shutdown_default_executor(self: asyncio.BaseEventLoop, timeout: float | None = None) -> None:
        """Bound the executor shutdown wait when no timeout is given.

        pytest-homeassistant's autouse ``verify_cleanup`` fixture calls
        ``loop.shutdown_default_executor()`` without a timeout; in a
        sandbox where the loop cannot wake from the shutdown thread that
        wait would hang forever.
        """
        if timeout is None:
            timeout = _DEFAULT_EXECUTOR_SHUTDOWN_TIMEOUT
        return await _orig_shutdown_default_executor(self, timeout)

    asyncio.base_events.BaseEventLoop.shutdown_default_executor = _bounded_shutdown_default_executor


@pytest.fixture(autouse=True)
def _setup_default_motion_sensors(hass: HomeAssistant) -> Iterator[None]:
    """Auto-set motion sensors to active state before each test."""
    # Set common motion sensor states to active by default
    hass.states.async_set("binary_sensor.bedroom_motion", "active")
    yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Bedroom Fan Thermostat",
        data={
            "name": "Bedroom Fan Thermostat",
            "temperature_sensors": ["sensor.bedroom_temp"],
            "fan_controllers": ["fan.bedroom_fan"],
            "motion_sensors": ["binary_sensor.bedroom_motion"],
            "switches": [],
            "target_temp_low": 72.0,
            "target_temp_high": 78.0,
            "retrigger_time": 300,
            "motion_timeout": 300,
            "manual_override_time": 300,
        },
        unique_id="bedroom_fan_thermostat",
    )


@pytest.fixture
def mock_config_entry_multiple_sensors() -> MockConfigEntry:
    """Return a mock config entry with multiple temperature sensors."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Living Room Fan Thermostat",
        data={
            "name": "Living Room Fan Thermostat",
            "temperature_sensors": [
                "sensor.living_room_temp_1",
                "sensor.living_room_temp_2",
            ],
            "fan_controllers": ["fan.living_room_fan"],
            "motion_sensors": [],
            "switches": ["switch.living_room_switch"],
            "target_temp_low": 70.0,
            "target_temp_high": 78.0,
            "retrigger_time": 120,
            "motion_timeout": 180,
            "manual_override_time": 600,
        },
        unique_id="living_room_fan_thermostat",
    )


@pytest.fixture
def mock_config_entry_no_motion() -> MockConfigEntry:
    """Return a mock config entry without motion sensors."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Office Fan Thermostat",
        data={
            "name": "Office Fan Thermostat",
            "temperature_sensors": ["sensor.office_temp"],
            "fan_controllers": ["fan.office_fan"],
            "motion_sensors": [],
            "switches": [],
            "target_temp_low": 70.0,
            "target_temp_high": 82.0,
            "retrigger_time": 60,
            "motion_timeout": 0,
            "manual_override_time": 300,
        },
        unique_id="office_fan_thermostat",
    )


@pytest.fixture
def mock_config_entry_with_switches() -> MockConfigEntry:
    """Return a mock config entry with switches but no fan controllers."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Box Fan Thermostat",
        data={
            "name": "Box Fan Thermostat",
            "temperature_sensors": ["sensor.box_fan_temp"],
            "fan_controllers": [],
            "motion_sensors": [],
            "switches": ["switch.box_fan_switch"],
            "target_temp_low": 72.0,
            "target_temp_high": 78.0,
            "retrigger_time": 300,
            "motion_timeout": 300,
            "manual_override_time": 300,
        },
        unique_id="box_fan_thermostat",
    )


@pytest.fixture
def mock_config_entry_single_speed() -> MockConfigEntry:
    """Return a mock config entry for a single-speed fan."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Single Speed Fan Thermostat",
        data={
            "name": "Single Speed Fan Thermostat",
            "temperature_sensors": ["sensor.single_speed_temp"],
            "fan_controllers": ["fan.single_speed_fan"],
            "motion_sensors": [],
            "switches": [],
            "target_temp_low": 72.0,
            "target_temp_high": 78.0,
            "retrigger_time": 300,
            "motion_timeout": 300,
            "manual_override_time": 300,
        },
        unique_id="single_speed_fan_thermostat",
    )


@pytest.fixture
def caplog(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """Fixture to capture logs."""
    yield caplog


@pytest.fixture
def hass(hass: HomeAssistant) -> Iterator[HomeAssistant]:
    """Override the hass fixture to use IMPERIAL_SYSTEM (Fahrenheit)."""
    # Set the temperature unit to Fahrenheit
    hass.config.units = IMPERIAL_SYSTEM
    yield hass


def pytest_configure(config: pytest.Config) -> None:
    """Fail fast when sockets/network are blocked (e.g. sandboxed environments).

    Without this, every test would error out one-by-one with 5s+ of timeout
    noise instead of aborting the session up front.
    """
    if _self_pipe_send_blocked():
        pytest.exit(
            "Fan-Thermostat tests cannot run here: socket/network access is blocked "
            "in this environment (asyncio's loop wakeup is blocked). "
            "Re-run with network access enabled."
        )
