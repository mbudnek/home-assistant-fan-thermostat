"""Simple tests for Fan Thermostat integration that don't require full HA test infrastructure."""

import sys
import os

# Add the custom component to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'custom_components'))


def test_manifest():
    """Test that the manifest.json is valid."""
    import json
    manifest_path = os.path.join(
        os.path.dirname(__file__), '..', 'custom_components', 'fan_thermostat', 'manifest.json'
    )
    with open(manifest_path) as f:
        manifest = json.load(f)
    
    assert manifest['domain'] == 'fan_thermostat'
    assert manifest['name'] == 'Fan Thermostat'
    assert manifest['config_flow'] is True
    # integration_type should be valid (defaults to "integration")
    assert manifest.get('integration_type', 'integration') in ('integration', None)


def test_const():
    """Test that the const.py exports the expected values."""
    from fan_thermostat.const import (
        DOMAIN,
        DEFAULT_RETRIGGER_TIME,
        DEFAULT_MOTION_TIMEOUT,
        DEFAULT_MANUAL_OVERRIDE_TIME,
        DEFAULT_TARGET_TEMP_LOW,
        DEFAULT_TARGET_TEMP_HIGH,
    )
    
    assert DOMAIN == 'fan_thermostat'
    assert DEFAULT_RETRIGGER_TIME == 300
    assert DEFAULT_MOTION_TIMEOUT == 300
    assert DEFAULT_MANUAL_OVERRIDE_TIME == 300
    assert DEFAULT_TARGET_TEMP_LOW == 72.0
    assert DEFAULT_TARGET_TEMP_HIGH == 78.0


def test_climate_module():
    """Test that the climate module can be imported."""
    from fan_thermostat import climate
    assert hasattr(climate, 'FanThermostatClimate')


def test_config_flow_module():
    """Test that the config_flow module can be imported."""
    from fan_thermostat import config_flow
    assert hasattr(config_flow, 'FanThermostatConfigFlow')


def test_switch_module():
    """Test that the switch module can be imported."""
    from fan_thermostat import switch
    assert hasattr(switch, 'FanThermostatManualOverride')


def test_init_module():
    """Test that the __init__ module can be imported."""
    import fan_thermostat
    assert hasattr(fan_thermostat, 'async_setup')
    assert hasattr(fan_thermostat, 'async_setup_entry')
    assert hasattr(fan_thermostat, 'async_unload_entry')


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
