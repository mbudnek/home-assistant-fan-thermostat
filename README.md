# Fan Thermostat

A Home Assistant custom component that controls ceiling fans based on temperature and motion sensors, ported from the Hubitat Fan Thermostat app.

## Features

- **Temperature-based fan control**: Automatically adjusts fan speed based on temperature readings using dual setpoints (cooling low/high)
- **Motion sensor integration**: Fans only run when motion is detected
- **Manual override**: Temporary disable automatic control via a switch
- **Temperature aggregation**: Combine multiple temperature sensors using minimum, maximum (default), or average
- **Retrigger time**: Prevents rapid fan cycling after a fan turn-off (motion or temperature triggered)
- **Multiple fan controllers**: Control multiple fans simultaneously
- **Switch support**: Control on/off switches in addition to fans

## Installation

### HACS (Home Assistant Community Store)

1. Open HACS in Home Assistant
2. Go to "Integrations"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add this repository URL: `https://github.com/mbudnek/fan-thermostat`
6. Select "Integration" as the category
7. Click "Add"
8. Find "Fan Thermostat" in the list and install it

### Manual Installation

1. Download the latest release or clone this repository
2. Copy the `custom_components/fan_thermostat` folder to your Home Assistant's `custom_components` directory
3. Restart Home Assistant

## Configuration

After installation, go to **Settings → Devices & Services → Add Integration** and search for "Fan Thermostat".

You'll need to configure:

- **Name**: A name for this thermostat instance (used as the device identifier)
- **Temperature Sensors**: One or more temperature sensors (e.g., `sensor.bedroom_temp`). With more than one, use the **Temperature Operation** select entity to choose how they are combined: Minimum, Maximum (default), or Average
- **Fan Controllers** (optional): One or more fan entities to control (e.g., `fan.living_room_fan`). At least one of Fan Controllers or Switches is required
- **Motion Sensors** (optional): Motion sensors to gate fan operation
- **Switches** (optional): On/off switches to control alongside fans

### Temperature Setpoints

Unlike the original Hubitat app which used a single setpoint with a "Temperature Step", this integration uses dual cooling setpoints:

- **Cooling Low**: Fans begin running when temperature exceeds this value. Default: `72°F`
- **Cooling High**: Fan runs at maximum speed when temperature reaches this value. Default: `78°F`

Fan speed is linearly interpolated between these two values (0% to 100%).

### Runtime Configuration (Number Entities)

Timer settings are no longer configured during setup. Instead, they are exposed as **Number entities** on the device after installation:

- **Retrigger Time**: Seconds before fan can turn back on after turning off (motion or temperature triggered)
- **Motion Timeout**: Seconds fan stays on after motion stops
- **Manual Override Time**: Seconds before auto-control resumes after manual override

You can adjust these values at any time via the Home Assistant UI.

## Fan Speed Calculation

The fan speed is calculated using linear interpolation across your configured setpoints:

1. If `current_temp <= target_temp_low`: Fans are off (0%)
2. If `target_temp_low < current_temp < target_temp_high`: 
   `percentage = ((current_temp - low) / (high - low)) * 100` (clamped to 0-100%)
3. If `current_temp >= target_temp_high`: Fans are at maximum speed (100%)

## Entities Created

Each Fan Thermostat instance creates a single device with:

- **Climate entity**: The thermostat with dual temperature control and fan support
- **Switch entity**: Manual override toggle
- **Select entity** (when multiple temperature sensors are configured):
  - Temperature Operation (`min`, `max`, `avg`; default `max`)
- **Number entities**: 
  - Retrigger Time (seconds; default 0 — disabled)
  - Motion Timeout (seconds; default 0 — fans turn off immediately when motion stops)
  - Manual Override Time (seconds; default 0 — automatic override disabled)

## Usage

### Automatic Mode

The thermostat automatically controls fan speed based on:

1. **Temperature**: When temperature exceeds the cooling setpoint, fans turn on at appropriate speed
2. **Motion**: If motion sensors are configured, fans only run when motion is detected (`on` or `active`)
3. **Retrigger**: After fans turn off (motion or temperature triggered), fans can automatically turn back on once the retrigger time has elapsed if temperature and motion warrant it

### Manual Override

Use the manual override switch to temporarily disable automatic control. The override expires automatically after the configured time (default 0 — disabled; the override stays until the switch is turned off). While active, automatic fan control is paused entirely.

## Migration from Hubitat

If you are migrating from the original Hubitat Fan Thermostat app:

- **Dual Setpoints**: Replace your single setpoint with `target_temp_low` and `target_temp_high`. The interpolation logic provides smoother control.
- **Timer Settings**: These were previously in the setup flow; they are now Number entities on the device. You can copy values from your old configuration if needed.
- **Motion States**: This integration standardizes motion detection to Home Assistant's binary sensor states (`on`/`off`).

## Troubleshooting

- **Fans not responding**: Ensure fan entities support `set_percentage` service
- **Temperature not updating**: Check that temperature sensors are available and reporting correctly
- **Motion not detected**: Verify motion sensors are reporting `on` (or `active`) states
