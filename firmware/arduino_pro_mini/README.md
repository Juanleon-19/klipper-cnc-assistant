# Arduino Pro Mini Interface

## Overview

This directory contains the firmware developed for the Arduino Pro Mini used by Klipper CNC Assistant.

The Arduino provides the physical interface between the operator and the Linux computer running Klipper.

At this stage, the firmware has been validated to ensure that programs can be compiled, uploaded and executed successfully.

---

# Hardware

## Microcontroller

- Arduino Pro Mini
- ATmega328P
- 5 V
- 16 MHz

## USB Interface

USB to TTL adapter

Current development serial port:

```
/dev/ttyUSB1
```

---

# Development Environment

Operating System

- Ubuntu 24.04 LTS

Compiler

- Arduino CLI

Development

- SSH

---

# Current Status

The following items have been successfully validated.

- Arduino CLI installation
- AVR toolchain installation
- Firmware compilation
- Firmware upload
- USB serial communication
- Manual reset upload procedure

---

# Directory Structure

```
firmware/
└── arduino_pro_mini
    ├── docs
    ├── include
    ├── joystick_controller
    ├── src
    └── README.md
```

---

# Project Philosophy

The firmware is developed as an independent component of Klipper CNC Assistant.

The Arduino is responsible only for interacting with the physical hardware.

Machine control remains outside the microcontroller.
