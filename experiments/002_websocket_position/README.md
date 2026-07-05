# Experiment 002 — Moonraker WebSocket Position Streaming

## Objective

Evaluate real-time machine motion telemetry through the Moonraker WebSocket API.

## Scope

This experiment establishes a persistent WebSocket connection with Moonraker and subscribes to Klipper printer objects.

The experiment is read-only and does not request machine motion.

## Subscribed objects

- `gcode_move.position`
- `toolhead.position`
- `motion_report.live_position`
- `motion_report.live_velocity`

## Validated operations

- Establish a persistent Moonraker WebSocket connection.
- Subscribe to Klipper printer objects.
- Receive the initial printer state.
- Receive asynchronous status updates.
- Monitor G-code position.
- Monitor toolhead position.
- Monitor estimated live machine position.
- Monitor estimated live machine velocity.

## Result

Status: **PASSED**

Live position and velocity updates were successfully received while the machine was moved through Mainsail.

The `motion_report` object provided responsive real-time motion telemetry suitable for future CNC interface visualization and jog supervision.

## Architectural conclusion

The final Moonraker communication layer should use WebSocket subscriptions for live machine telemetry.

HTTP requests may still be used for discrete operations, while persistent WebSocket communication will maintain the live application state.

## Next experiment

Experiment 003 will validate controlled machine motion initiated directly from Python through Moonraker.
