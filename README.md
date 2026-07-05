# Klipper CNC Assistant

Klipper CNC Assistant is an experimental CNC control and PCB surface compensation application designed for machines adapted to run on Klipper.

The project explores the use of a 3D printer mechanically converted for light CNC machining while keeping the original Klipper controller architecture unchanged.

## Project Goals

- Communicate with Klipper through Moonraker.
- Monitor machine position in real time.
- Implement analog joystick-based CNC jogging.
- Use an external Arduino Nano as a human-machine input controller.
- Implement an independent electrical contact probe.
- Establish a PCB work coordinate system.
- Correct PCB rotational misalignment.
- Generate an automatic PCB surface height map.
- Visualize the measured PCB surface.
- Apply surface interpolation.
- Generate height-compensated G-code.
- Send corrected machining jobs to Klipper.

## Planned Architecture

```text
Arduino Nano
    |
    | USB Serial
    v
Klipper CNC Assistant
    |
    | HTTP / WebSocket
    v
Moonraker
    |
    v
Klipper
    |
    v
CNC-adapted machine
