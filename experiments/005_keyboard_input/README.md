# Experiment 005 — Continuous Keyboard Input

## Problem

The final Klipper CNC Assistant requires a manual positioning system.

The operator must be able to move the machine tool toward physical reference points on the workpiece.

These reference points will later be captured and used to define the relationship between machine coordinates and PCB work coordinates.

The final manual positioning device is expected to use analog joysticks connected to an Arduino Nano.

Before integrating the hardware, this experiment investigates how a continuous directional input can be represented as desired X and Y jog velocities.

## Purpose of this experiment

The keyboard temporarily represents the future joystick input.

The experiment converts directional keyboard commands into desired velocity values:

- `vx`
- `vy`

The mapping is:

- `W` requests positive Y motion.
- `S` requests negative Y motion.
- `A` requests negative X motion.
- `D` requests positive X motion.

No machine motion is generated.

The experiment operates in dry-run mode.

## Relation to the final CNC workflow

Manual jog control is not the final objective of the application.

Jog control is a positioning tool.

The operator will use manual movement to physically align the CNC tool with important workpiece locations.

Examples include:

- PCB work origin.
- PCB X-axis reference.
- PCB corners.
- Probe boundaries.
- Tool Z reference.
- Custom reference points.

When the tool reaches a desired physical location, the application will capture the current Klipper machine position.

Example:

Machine position:

X = 73.241 mm  
Y = 42.815 mm  
Z = 5.000 mm

The operator may assign this position as the PCB work origin.

The application can then associate:

Machine coordinates:

X = 73.241 mm  
Y = 42.815 mm

with:

Work coordinates:

X = 0.000 mm  
Y = 0.000 mm

These captured reference points will later support PCB alignment and coordinate transformation.

## Why keyboard input was tested first

The keyboard provides a simple temporary input source.

It allows the desired velocity interface to be explored before integrating:

- Arduino serial communication.
- Analog joystick calibration.
- Dead zones.
- Input filtering.
- Joystick scaling.

The intended architecture separates the input device from the motion controller.

Conceptually:

Input Device -> Desired Velocity -> Jog Controller

The input device may later be:

- Keyboard.
- Arduino joystick.
- Graphical interface.
- Other control hardware.

The jog controller should not depend on the specific input source.

## Experiment result

Status: **PARTIALLY VALIDATED**

Single-axis keyboard commands were successfully converted into desired velocity values.

However, simultaneous key combinations such as `W + D` were not represented reliably through the SSH terminal input mechanism.

This limitation is related to terminal keyboard event handling.

A terminal does not provide the same persistent key-down and key-up state model available to a graphical application.

## Architectural conclusion

SSH terminal keyboard input is not suitable as the final continuous multidirectional input source.

The experiment successfully validated the concept of separating directional input from desired jog velocity.

Future keyboard control should use an input system capable of maintaining simultaneous key states.

The final Arduino joystick implementation will provide independent X and Y analog values and will not depend on terminal keyboard behavior.

## Machine independence

The input controller must not contain machine dimensions or fixed axis limits.

The same desired velocity interface should be usable with different Klipper machines.

Machine-specific limits and state should be obtained from Klipper through Moonraker.

The final application should dynamically identify information such as:

- Homed axes.
- Axis minimum coordinates.
- Axis maximum coordinates.
- Current machine position.
- Live machine position.
- Live machine velocity.

## Contribution to the project

This experiment established the desired velocity interface between an input device and the future jog controller.

It also demonstrated that terminal-based keyboard handling is not appropriate for simultaneous multidirectional control.

The result supports a modular architecture where the input device can be replaced without redesigning the motion controller.git add experiments/005_keyboard_input/

