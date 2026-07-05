# Experiment 003 — Basic Machine Motion

## Objective

Validate controlled machine motion initiated from Python through Moonraker and observed through real-time Klipper motion telemetry.

## Scope

The experiment requests a single relative X-axis movement.

Machine motion is initiated through the Moonraker G-code API and observed through a persistent WebSocket connection.

## Test parameters

- Relative X displacement: 10 mm
- Requested velocity: 20 mm/s
- Motion telemetry: `motion_report`

## Validated operations

- Send G-code commands through Moonraker.
- Preserve the existing G-code state.
- Execute relative machine motion.
- Observe live machine position.
- Observe live machine velocity.
- Detect observed motion start.
- Detect observed motion completion.
- Measure commanded displacement against reported displacement.

## Results

Status: **PASSED**

Observed test results:

- Requested displacement: 10.000 mm
- Measured displacement: 10.000 mm
- Requested velocity: 20.000 mm/s
- Maximum live velocity: 20.000 mm/s
- Observed motion duration: 0.503 s
- Command-to-observed-motion latency: 580.35 ms

## Important observation

Moonraker WebSocket status notifications contain partial state updates.

The local client must preserve the last known state of subscribed fields and merge incoming updates instead of assuming that omitted fields have returned to zero or a default value.

## Architectural conclusion

Moonraker can be used to initiate controlled Klipper motion while a WebSocket telemetry channel independently observes the resulting movement.

The measured command-to-observed-motion latency must not be interpreted as direct motor or machine latency. It includes command transmission, Klipper scheduling, motion reporting, WebSocket notification, and client observation delays.


## Why continuous jog requires further research

The final CNC application is intended to provide analog joystick control.

A joystick naturally represents a desired movement direction and velocity. Klipper, however, executes planned motion commands.

A naive implementation could continuously send many small G-code movements while the joystick is active. This may cause motion commands to accumulate in the Klipper motion queue.

If the user releases the joystick while several movements remain queued, the machine may continue moving after the input has returned to zero.

This behavior is undesirable for manual CNC positioning, especially when approaching a PCB reference point or positioning a cutting tool close to the workpiece.

The next stage of the project therefore investigates a short motion-horizon strategy.

Instead of planning a long movement, the jog controller will only provide Klipper with a limited amount of future motion. The controller will periodically evaluate the desired joystick velocity and extend the planned movement only while motion is still requested.

The objective is to find a practical balance between:

- Smooth continuous motion.
- Low command frequency.
- Limited queued motion.
- Fast stopping response.
- Predictable manual positioning.
