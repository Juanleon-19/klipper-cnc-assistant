# Experiment 004 — Continuous Jog Motion Horizon

## Problem

The final CNC application requires continuous manual machine movement.

A joystick or keyboard naturally represents a desired movement direction and velocity. Klipper, however, executes planned motion commands.

A naive implementation may continuously send small G-code movements while an input remains active.

These movements can accumulate as future planned motion.

If the operator releases the movement control while commands remain pending, the machine may continue moving after zero velocity has been requested.

For manual CNC positioning, this behavior is undesirable.

The problem becomes particularly important when positioning a tool near a PCB reference point or workpiece.

## Purpose of this experiment

This experiment investigates how short sequential movement segments behave when used to approximate continuous jog motion through Moonraker and Klipper.

The objective is not to implement the final joystick controller.

The objective is to understand how much motion may remain after the input controller stops requesting additional movement.

## What is the motion horizon?

In this project, the term `motion horizon` describes the amount of future motion intentionally provided to Klipper before the desired velocity is evaluated again.

The motion horizon is not a native Klipper feature or configuration parameter.

It is an experimental control strategy used by this project.

The idea is inspired by finite and receding-horizon planning concepts, but this experiment does not implement Model Predictive Control or a formal receding-horizon controller.

For a desired velocity `v` and a selected time horizon `T`, the commanded movement segment is calculated using:

d = v × T

where:

- `d` is the movement segment distance.
- `v` is the desired jog velocity.
- `T` is the selected motion horizon.

For example:

v = 20 mm/s

T = 0.250 s

d = 20 × 0.250 = 5 mm

The controller therefore sends 5 mm movement segments while movement remains requested.

## Test configuration

The first benchmark used:

- Jog velocity: 20 mm/s
- Active input time: 2 seconds
- Motion horizon: 250 ms
- Segment distance: 5 mm
- Movement axis: positive X

The expected nominal displacement was:

20 mm/s × 2 s = 40 mm

## Results

Status: **PASSED WITH IMPORTANT OBSERVATION**

Measured results:

- Commands sent: 8
- Expected displacement: 40 mm
- Measured displacement: 40.000 mm
- Observed stopping delay: 750.62 ms
- Additional displacement after input release: 11.248 mm
- Final observed velocity: 0.000 mm/s

## Interpretation

The segmented movement strategy reproduced the expected total displacement accurately.

Eight 5 mm segments produced exactly 40 mm of reported movement.

However, when the simulated joystick input returned to zero, the machine continued moving for an additional observed 11.248 mm before the reported velocity returned to zero.

This indicates that stopping the generation of new G-code segments does not imply immediate machine stopping.

Previously submitted motion may remain planned or executing inside the Klipper motion pipeline.

## Architectural conclusion

A continuous jog controller must distinguish between:

- Desired operator velocity.
- Commands already submitted to Klipper.
- Observed machine velocity.
- Planned or pending movement.

The final jog controller should not assume that stopping command generation is equivalent to commanding an immediate stop.

The 250 ms motion-horizon strategy is therefore not accepted as the final jog implementation.

Further jog control development must explicitly address bounded stopping behavior.

## Machine independence

The jog architecture must not assume fixed machine dimensions or axis limits.

The final application targets machines controlled by Klipper and Moonraker.

Machine-specific information should be obtained dynamically from Klipper whenever possible.

Relevant toolhead information includes:

- Homed axes.
- Minimum axis coordinates.
- Maximum axis coordinates.
- Current position.
- Live position.
- Live velocity.

This allows the same application architecture to operate with different Klipper machines without hardcoded X, Y, or Z travel limits.

## Contribution to the project

This experiment demonstrated that continuous manual control cannot be implemented safely by simply generating movement segments until the input returns to zero.

The result defines a new requirement for the final jog controller:

The amount of pending motion and the stopping response must be explicitly controlled and characterized.
