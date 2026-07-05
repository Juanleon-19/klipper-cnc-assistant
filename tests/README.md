# Klipper CNC Assistant — Tests

This directory contains validation tests for features implemented in the Klipper CNC Assistant core.

Unlike the files stored in `experiments/`, these tests are not intended to investigate open architectural questions.

The distinction is:

- `experiments/` contains exploratory technical investigations.
- `tests/` validates behavior implemented in the application core.
- `src/` contains the actual Klipper CNC Assistant software.

Some tests in this directory require a physical Klipper-controlled machine and must therefore be executed manually.

---

## Manual Machine Tests

### `manual_jog_test.py`

Validates the first implementation of the `JogController`.

The test verifies that the application can:

- Discover the connected Klipper machine.
- Read the current machine position.
- Read machine axis limits dynamically.
- Request a relative jog movement.
- Convert jog speed from millimeters per second to G-code feedrate.
- Limit the requested target to the machine coordinate range.
- Send the generated G-code through Moonraker.
- Query the machine after motion and verify the resulting position.

#### Validated configuration

The initial physical validation used:

- Axis: X
- Requested distance: +5.000 mm
- Jog speed: 10.000 mm/s

Initial position:

```text
X = 0.000 mm
```

Requested target:

```text
X = 5.000 mm
```

Final reported position:

```text
X = 5.000 mm
```

The requested movement was completed successfully.

No software limit correction was required during this test.

**Result: PASSED**

---

### `manual_control_test.py`

Validates the manual positioning abstraction implemented by the `ManualJogController`.

The test provides an interactive command interface for requesting machine movement on the X, Y, and Z axes.

#### Supported motion commands

```text
x+
x-
y+
y-
z+
z-
```

#### Jog modes

The test validates three manual jog modes:

| Mode | Distance | Speed |
|---|---:|---:|
| `COARSE` | 10.0 mm | 40.0 mm/s |
| `NORMAL` | 1.0 mm | 10.0 mm/s |
| `FINE` | 0.1 mm | 2.0 mm/s |

Each mode selects a different jog profile.

The manual controller translates operator intent into a relative motion request.

Conceptually:

```text
Operator command
       |
       v
ManualJogController
       |
       v
Jog profile selection
       |
       v
JogController
       |
       v
Moonraker
       |
       v
Klipper
```

For example:

```text
Mode    : FINE
Command : x+
```

is translated into:

```text
Axis     : X
Distance : +0.100 mm
Speed    : 2.000 mm/s
```

The `ManualJogController` does not generate G-code directly.

Motion generation and machine limit validation remain the responsibility of the `JogController`.

#### Physical validation

Manual movement was successfully validated on all configured machine axes:

| Command | Status |
|---|---|
| `X+` | PASSED |
| `X-` | PASSED |
| `Y+` | PASSED |
| `Y-` | PASSED |
| `Z+` | PASSED |
| `Z-` | PASSED |

All jog profiles were also physically validated:

| Jog mode | Status |
|---|---|
| `COARSE` | PASSED |
| `NORMAL` | PASSED |
| `FINE` | PASSED |

**Result: PASSED**

---

## Jog Target Limiting

The `JogController` calculates the requested target before sending motion to Klipper.

Conceptually:

```text
Current position
       |
       v
Requested distance
       |
       v
Requested target
       |
       v
Machine coordinate limits
       |
       v
Applied target
```

For example:

```text
Current X = 150 mm
Maximum X = 160 mm
Requested = +20 mm
```

The nominal requested target is:

```text
150 + 20 = 170 mm
```

Because the configured maximum X coordinate is 160 mm, the controller limits the applied target to:

```text
X = 160 mm
```

The effective movement becomes:

```text
+10 mm
```

This software validation does not replace Klipper homing, endstops, machine configuration, or physical safety mechanisms.

---

## Running the Manual Tests

Activate the project virtual environment before executing the tests.

### Relative jog test

```bash
PYTHONPATH=src \
MOONRAKER_URL=http://192.168.x.x:7125 \
python tests/manual_jog_test.py
```

### Interactive manual control test

```bash
PYTHONPATH=src \
MOONRAKER_URL=http://192.168.x.x:7125 \
python tests/manual_control_test.py
```

Replace the example Moonraker host and port with the configuration of the target Klipper instance.

Manual motion tests require operator supervision.

Before requesting physical movement, verify that the selected axis has sufficient free travel.

---

## Current Validation Status

| Test | Feature | Status |
|---|---|---|
| `manual_jog_test.py` | Relative machine jog | PASSED |
| `manual_control_test.py` | Manual multi-axis jog control | PASSED |
