# Klipper CNC Assistant — Tests

This directory contains validation tests for features implemented in the Klipper CNC Assistant core.

Unlike the files stored in `experiments/`, these tests are not intended to investigate open architectural questions.

The distinction is:

- `experiments/` contains exploratory technical investigations.
- `tests/` validates behavior implemented in the application core.
- `src/` contains the actual Klipper CNC Assistant software.

Some tests in this directory require a physical Klipper-controlled machine and must therefore be executed manually.

---

## Manual machine tests

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

### Validated configuration

The initial physical validation used:

- Axis: X
- Requested distance: +5.000 mm
- Jog speed: 10.000 mm/s

Initial position:

```text
X = 0.000 mm
---

### `manual_control_test.py`

Validates the manual positioning abstraction implemented by the `ManualJogController`.

The test provides an interactive command interface for requesting machine movement on the X, Y, and Z axes.

Supported motion commands:

```text
x+
x-
y+
y-
z+
z----

### `manual_control_test.py`

Validates the manual positioning abstraction implemented by the `ManualJogController`.

The test provides an interactive command interface for requesting machine movement on the X, Y, and Z axes.

Supported motion commands:

```text
x+
x-
y+
y-
z+
z-

The test also validates the three manual jog modes:

COARSE
NORMAL
FINE

Each mode selects a different jog profile.

Mode	Distance	Speed
COARSE	10.0 mm	40.0 mm/s
NORMAL	1.0 mm	10.0 mm/s
FINE	0.1 mm	2.0 mm/s

The manual controller translates operator intent into a relative motion request.

Conceptually:

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

For example:

Mode    : FINE
Command : x+

is translated into:

Axis     : X
Distance : +0.100 mm
Speed    : 2.000 mm/s

The ManualJogController does not generate G-code directly.

Motion generation and machine limit validation remain the responsibility of the JogController.

Physical validation

Manual movement was successfully validated on all configured machine axes:

X+  PASSED
X-  PASSED

Y+  PASSED
Y-  PASSED

Z+  PASSED
Z-  PASSED

All jog profiles were also physically validated:

COARSE  PASSED
NORMAL  PASSED
FINE    PASSED

Result:

PASSED
