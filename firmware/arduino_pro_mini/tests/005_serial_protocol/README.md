# Test 005 - Binary Serial Protocol

## Overview

This test validates the binary communication protocol between the Arduino Pro Mini firmware and the Python software running on the host computer.

Unlike the previous hardware validation tests, this experiment verifies the complete communication path from the handheld controller to the PC.

This test represents the final validation step before integrating the controller with the Klipper CNC Assistant software stack.

---

# Objectives

The purpose of this test is to verify:

- Reliable USB serial communication.
- Binary packet generation.
- Packet reception in Python.
- Checksum validation.
- Direction decoding.
- Digital input transmission.
- Analog value transmission.

---

# Hardware Configuration

| Device | Arduino Pin |
|----------|-------------|
| Joystick X Axis | A2 |
| Joystick Y Axis | A1 |
| Joystick Button | D2 |
| External Button | D3 |
| PCB Probe | D4 |

Communication:

- USB-TTL Serial
- 115200 baud

---

# Packet Format

Each packet contains exactly **8 bytes**.

| Byte | Description |
|------|-------------|
| 0 | Header (0xAA) |
| 1 | Direction |
| 2 | Input Flags |
| 3 | X Axis (LSB) |
| 4 | X Axis (MSB) |
| 5 | Y Axis (LSB) |
| 6 | Y Axis (MSB) |
| 7 | XOR Checksum |

---

# Direction Encoding

| Value | Direction |
|------:|-----------|
| 0 | CENTER |
| 1 | UP |
| 2 | DOWN |
| 3 | LEFT |
| 4 | RIGHT |
| 5 | UP_LEFT |
| 6 | UP_RIGHT |
| 7 | DOWN_LEFT |
| 8 | DOWN_RIGHT |

---

# Input Flags

Bit layout:

| Bit | Function |
|-----|----------|
| 0 | Joystick Button |
| 1 | External Button |
| 2 | PCB Probe |

Unused bits are reserved for future extensions.

---

# Checksum

The checksum is calculated as the XOR of the first seven bytes.

```
checksum =
byte0 ^
byte1 ^
byte2 ^
byte3 ^
byte4 ^
byte5 ^
byte6
```

The Python receiver validates every packet before decoding its contents.

---

# Files

| File | Description |
|------|-------------|
| `005_serial_protocol.ino` | Arduino firmware that generates binary packets |
| `test_serial_protocol.py` | Python receiver used to decode and validate incoming packets |

---

# Validation Procedure

1. Upload the firmware to the Arduino Pro Mini.
2. Connect the controller using the USB-TTL adapter.
3. Execute the Python receiver.
4. Move the joystick.
5. Press the joystick button.
6. Press the external button.
7. Trigger the PCB probe.
8. Verify that every event is decoded correctly.

---

# Expected Output

The Python application should display:

- Current direction
- Raw X value
- Raw Y value
- Joystick button state
- External button state
- Probe state

Checksum errors should not occur during normal operation.

---

# Result

Status:

**PASSED**

The binary protocol has been successfully validated between the Arduino firmware and the Python receiver.

This protocol becomes the communication interface used by the production firmware in the next development stage.
