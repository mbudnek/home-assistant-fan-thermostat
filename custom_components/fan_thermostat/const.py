"""Constants for the Fan Thermostat integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

DOMAIN = "fan_thermostat"

# Default configuration values
DEFAULT_RETRIGGER_TIME = 0
DEFAULT_MOTION_TIMEOUT = 0
DEFAULT_MANUAL_OVERRIDE_TIME = 0

# Default target temperatures for the dual-setpoint range
DEFAULT_TARGET_TEMP_LOW = 72.0  # Default lower setpoint (fan starts here)
DEFAULT_TARGET_TEMP_HIGH = 78.0  # Default upper setpoint (fan at full speed)

# How long after the climate entity is set up to ignore external fan/switch
# state events. Fan/switch integrations report their own state when they boot;
# those boot reports were not requested by the thermostat and must not be
# mistaken for a user-initiated change (which would flip the manual override
# switch on at startup).
STARTUP_GRACE_SECONDS = 10


def get_device_info(config_entry: ConfigEntry, name: str) -> DeviceInfo:
    """Get device info for a Fan Thermostat config entry."""
    return DeviceInfo(
        identifiers={(DOMAIN, config_entry.entry_id)},
        name=name,
        manufacturer="Fan Thermostat",
        model="Fan Thermostat",
        sw_version="1.0",
    )
