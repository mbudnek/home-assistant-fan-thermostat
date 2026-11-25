"""Constants for the Fan Thermostat integration."""

DOMAIN = "fan_thermostat"

# Default configuration values
DEFAULT_RETRIGGER_TIME = 0
DEFAULT_MOTION_TIMEOUT = 0
DEFAULT_MANUAL_OVERRIDE_TIME = 0

# Default target temperatures for the dual-setpoint range
DEFAULT_TARGET_TEMP_LOW = 72.0  # Default lower setpoint (fan starts here)
DEFAULT_TARGET_TEMP_HIGH = 78.0  # Default upper setpoint (fan at full speed)

def get_device_info(config_entry, name: str):
    """Get device info for a Fan Thermostat config entry."""
    from homeassistant.helpers import device_registry as dr
    
    return dr.DeviceInfo(
        identifiers={(DOMAIN, config_entry.entry_id)},
        name=name,
        manufacturer="Fan Thermostat",
        model="Fan Thermostat",
        sw_version="1.0",
    )
