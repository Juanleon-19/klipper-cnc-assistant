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
