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

## Next experiment

Experiment 004 will characterize continuous jog strategies and evaluate command horizon, motion queue behavior, velocity continuity, and observed stopping response.
