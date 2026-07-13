# Experiment 007 — Arduino Discrete Manual Controller

## Objective

Validate discrete manual X/Y jog requests from the Arduino controller through the approved product architecture:

```text
Arduino -> SerialDriver -> CommandMapper -> ManualJogController
-> JogController -> MoonrakerClient -> Moonraker -> Klipper
```

`MoonrakerTelemetry` updates the shared `MachineState` through the Moonraker WebSocket.

## Scope

- X+, X-, Y+, and Y- only.
- `JogMode.FINE`: 0.100 mm at 2.000 mm/s.
- One move only for each `CENTER -> direction` transition.
- A new move requires a subsequent `CENTER` packet.
- Joystick button events, external-button edges, and probe-triggered events are logged only.

No continuous jog, Horizon, TrapQ, probing routine, compensation, or Z motion is implemented.

## Safety

This experiment sends physical motion commands after explicit operator confirmation.

Before every movement, `JogController` requires the requested axis to be homed, validates the FINE speed against the discovered machine maximum velocity, validates a positive discovered maximum acceleration, and clamps the target to discovered axis limits. Klipper applies its configured acceleration to the `G1` command.

The experiment aborts on a Moonraker HTTP/WebSocket failure, serial failure, invalid serial checksum, or handled runtime exception. Do not run it unless the machine is clear and the operator can stop it safely.

## Requirements

- Klipper state: `ready`.
- Moonraker HTTP: `http://localhost:7126`.
- Moonraker WebSocket: `ws://localhost:7126/websocket`.
- Arduino controller: `/dev/ttyUSB0` at the `SerialDriver` default of 115200 baud.
- X and Y homed before requesting their respective movements.

The endpoints and serial port can be overridden with `MOONRAKER_URL`, `MOONRAKER_WS`, and `SERIAL_PORT`.

## Run

```bash
source .venv/bin/activate
python experiments/007_manual_controller/test_controller.py
```

Center the joystick, verify clearance, and confirm at the prompt. Keep a direction held to confirm that only one move is sent. Return to center before requesting another move.

## Acceptance criteria

- The startup sequence discovers the machine, checks Klipper `ready`, starts telemetry, and opens the serial driver.
- `CENTER -> RIGHT`, `LEFT`, `UP`, and `DOWN` each send exactly one FINE X/Y move.
- Holding a direction sends no additional moves.
- A direction change without an intervening `CENTER` sends no move.
- Button and probe inputs only produce log records.
- Any defined communication or protocol failure stops the experiment.

## Result and status

Status: `SUPERSEDED BY EXPERIMENT 008`

Experiment 007 established the Arduino -> telemetry -> discrete jog integration path. Current physical validation effort continues in Experiment 008, which preserves this manual jog behavior and adds the supervised probe sequence.
