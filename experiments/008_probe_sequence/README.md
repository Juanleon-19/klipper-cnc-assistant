# Experiment 008 — Manual Jog With Discrete Probe Sequence

## Objective

Extend Experiment 007 without changing the approved architecture:

```text
Arduino -> SerialDriver -> CommandMapper -> ManualJogController
-> JogController -> MoonrakerClient -> Moonraker -> Klipper
```

The experiment keeps discrete manual X/Y jog and adds:

- Joystick-button rising-edge mode cycling: `FINE -> NORMAL -> COARSE -> FINE`
- External-button rising-edge probe start
- A discrete Z probing sequence implemented as a state machine

`MoonrakerTelemetry` continues to update the shared `MachineState` through the Moonraker WebSocket.

## Scope

- Preserve the Experiment 007 discrete manual jog behavior.
- Manual jog remains cardinal and discrete only: X+, X-, Y+, Y-.
- Initial manual mode: `JogMode.FINE`.
- One jog only for each `CENTER -> direction` transition.
- No new move is sent until the joystick returns to `CENTER`.
- Probe routine states:
  - `IDLE`
  - `PREPARE`
  - `LOWERING`
  - `CONTACT_DETECTED`
  - `RETRACTING`
  - `COMPLETE`
  - `ABORTED`
- The probe routine stores X/Y at sequence start, lowers Z in safe discrete steps, captures contact Z when `probe_triggered=True`, retracts Z by a safe distance, reports the captured point, and returns to `IDLE` on success.

Out of scope:

- Continuous jog
- Klipper queue flooding
- 50 Hz motion submission
- Diagonal atomic jog
- Product migration to `src/`
- Automatic probing retries, debounce filtering, or work offset persistence

## Relevant behavior

### Manual jog

- The joystick button is handled on rising edge only.
- `ManualJogController.set_mode(...)` selects the next existing jog profile.
- The active mode is always printed with distance and speed.
- Manual X/Y movement is ignored while the probe state machine is active.

### Probe sequence

- The external button is handled on rising edge only.
- A new probe sequence cannot start while another is active.
- `discover_machine(...)` is reused at `PREPARE` to refresh homing, limits, and current position before lowering Z.
- `JogController.move_relative(...)` is reused for every Z step and for the final retract.
- Only one Z move is outstanding at a time; the next move waits for telemetry confirmation of the previous target.
- If the probe input activates during descent, the controller stops sending more downward moves, records the contact point after the in-flight step settles, retracts Z, and reports `X`, `Y`, and `Z`.

## Configuration

Defaults:

- `MOONRAKER_URL=http://localhost:7126`
- `MOONRAKER_WS=ws://localhost:7126/websocket`
- `SERIAL_PORT=/dev/ttyUSB0`
- `PROBE_STEP_DISTANCE=0.050`
- `PROBE_LOWER_SPEED=1.000`
- `PROBE_RETRACT_DISTANCE=1.000`
- `PROBE_RETRACT_SPEED=2.000`
- `PROBE_SETTLE_TOLERANCE=0.020`
- `PROBE_VELOCITY_TOLERANCE=0.050`
- `PROBE_MOVE_TIMEOUT=5.000`

These values can be overridden with environment variables before running the script.

## Safety

This experiment sends physical motion commands only after explicit operator confirmation.

Safety properties implemented in this experiment:

- No continuous jog.
- No repeated 50 Hz movement submission.
- Only one outstanding probe move at a time.
- `JogController` remains the only layer that generates movement G-code.
- `JogController` validates axis support, positive speed, discovered maximum velocity, positive maximum acceleration, axis homing, and discovered travel limits.
- Z descent stops requesting further downward motion as soon as the probe input becomes active.
- The probe sequence aborts if:
  - the serial layer fails,
  - the serial checksum is invalid,
  - Moonraker HTTP fails,
  - Moonraker telemetry stops or times out during a probe move,
  - Z is not homed,
  - discovered Z limits are invalid,
  - the minimum Z limit is reached before probe contact,
  - an unexpected runtime exception occurs.
- `SerialDriver.close()` and `MoonrakerTelemetry.stop()` are always called in `finally`.

## Requirements

- Klipper state: `ready`
- Moonraker HTTP reachable
- Moonraker WebSocket reachable
- Arduino controller on the configured serial port
- X and Y homed for manual jog
- Z homed before starting the probe sequence
- Probe input wired so `probe_triggered=True` represents contact
- Clear machine envelope and operator supervision

## Run

```bash
source .venv/bin/activate
PYTHONPATH=src python experiments/008_probe_sequence/test_controller.py
```

Recommended operator procedure:

1. Confirm Klipper is `ready`.
2. Home the required axes before starting the script.
3. Verify the tool and probe wiring on a safe test target.
4. Keep the joystick centered when confirming the prompt.
5. Validate manual X/Y jog first.
6. Press the joystick button to verify the mode cycle.
7. Press the external button once to start the probe sequence.
8. Be ready to stop the machine physically if the observed behavior differs from expectations.

## Acceptance criteria

- Startup discovers the machine, checks Klipper `ready`, starts telemetry, and opens the serial driver.
- Manual jog remains discrete and cardinal, matching Experiment 007 behavior.
- The joystick button cycles `FINE -> NORMAL -> COARSE -> FINE` on rising edge only.
- The active manual mode is printed with its distance and speed.
- The external button starts one probe sequence on rising edge only.
- A second external-button press during probing is ignored.
- While probing, manual X/Y commands are ignored and the joystick button does not change mode.
- Z lowering is performed as discrete safe steps through `JogController`.
- The descent stops issuing further downward moves once `probe_triggered=True`.
- The captured `X`, `Y`, `Z` point is printed.
- The probe sequence retracts Z safely and returns to `IDLE` on success.
- Any defined failure path aborts the experiment safely.

## Manual validation record

Status: `PARTIALLY VALIDATED`

Physical validation executed under operator supervision on 2026-07-13.

- Machine / controller revision: Arduino manual controller + Klipper host experiment rig
- Klipper and Moonraker endpoints used: `http://localhost:7126` and `ws://localhost:7126/websocket`
- Serial port used: `/dev/ttyUSB0`
- Homed axes before start: Z homed before probing; X/Y position available for point capture
- Active mode cycle observed: not recorded in this run log
- Probe step distance and retract distance configured: `0.050 mm` descent, `1.000 mm` retract
- Test surface / electrical probe condition: electrical probe contact on supervised test target
- Observed captured X/Y/Z: `X=25.200`, `Y=10.700`, `Z=17.900`
- Whether retract completed as expected: yes, safe retract completed to `Z=18.900`
- Whether each failure condition aborted safely: not fully exercised in this run

Observed run excerpt:

```text
[PROBE][MOVE] Lowering step: Z -0.050 mm at 1.000 mm/s -> target 17.900
[PROBE][INPUT] Probe signal rising edge
[PROBE][STATE] LOWERING -> CONTACT_DETECTED
[PROBE][SETTLED] Lowering step: Z=17.900
[PROBE][CONTACT] Captured point: X=25.200 Y=10.700 Z=17.900
[PROBE][STATE] CONTACT_DETECTED -> RETRACTING
[PROBE][MOVE] Safe retract: Z +1.000 mm at 2.000 mm/s -> target 18.900
[PROBE][SETTLED] Safe retract: Z=18.900
[PROBE][STATE] RETRACTING -> COMPLETE
[PROBE][COMPLETE] Point saved: X=25.200 Y=10.700 Z=17.900
[PROBE][STATE] COMPLETE -> IDLE
[STOPPED] Experiment stopped safely
```

Conclusion from this run:

- The external-button-triggered probe routine completed successfully in one supervised physical run.
- Contact was detected, the point was captured, and the sequence returned to `IDLE` without unsafe follow-up motion.
- Additional validation is still required for repeatability, probe-signal bounce characterization, and failure-path coverage.

## Limitations

- Homing state is refreshed when the probe sequence starts, but manual jog still relies on the originally discovered runtime state between explicit refreshes.
- There is no persistence of the captured point.
- Probe bounce filtering is not implemented in this experiment.
- In-flight Z motion is not cancelled at Klipper level; safety depends on very small discrete downward steps and on not issuing a second downward step after contact.

## Next step

Repeat the same point capture multiple times to measure Z repeatability, characterize the repeated probe-edge logs during contact/retract, and only then decide whether any reusable behavior belongs in `src/`.
