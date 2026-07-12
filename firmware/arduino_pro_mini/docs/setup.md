# Development Environment Setup

Version: 1.0

Last Updated: 2026-07-12

Status

✅ Validated

---

# Overview

This document describes the complete procedure used to prepare the development environment for the Arduino Pro Mini firmware.

The procedure has been physically validated on the development machine running Ubuntu and is the recommended setup for this project.

---

# Operating System

Ubuntu 24.04 LTS

---

# Arduino CLI Installation

Download and install the official Arduino CLI.

```bash
curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh

sudo mv bin/arduino-cli /usr/local/bin/
```

Verify the installation.

```bash
arduino-cli version
```

Example output

```
arduino-cli Version: 1.5.1
```

---

# Refresh the Shell Cache

If the system still references a previous installation, refresh the shell cache.

```bash
hash -r
```

Verify the executable location.

```bash
which arduino-cli
```

Expected result

```
/usr/local/bin/arduino-cli
```

---

# Install Arduino AVR Boards

Update the package index.

```bash
arduino-cli core update-index
```

Install the AVR platform.

```bash
arduino-cli core install arduino:avr
```

Verify the installation.

```bash
arduino-cli core list
```

---

# Project Location

The firmware project is located in

```text
firmware/arduino_pro_mini/joystick_controller
```

---

# Compile the Firmware

Enter the firmware directory.

```bash
cd ~/klipper-cnc-assistant/firmware/arduino_pro_mini/joystick_controller
```

Compile the project.

```bash
arduino-cli compile \
--fqbn arduino:avr:pro:cpu=16MHzatmega328 \
.
```

Successful compilation reports the program size and SRAM usage without errors.

---

# Upload the Firmware

Connect the USB-TTL adapter.

Upload the firmware.

```bash
arduino-cli upload \
-p /dev/ttyUSB1 \
--fqbn arduino:avr:pro:cpu=16MHzatmega328 \
-v \
.
```

This project uses a USB-TTL adapter without DTR.

The Arduino Pro Mini must be reset manually during the upload process.

The validated procedure is:

1. Execute the upload command.
2. Wait until the programmer starts communicating with the board.
3. Press the RESET button once.
4. Wait until the upload completes.

A successful upload finishes with a message similar to

```
1834 bytes of flash written

Avrdude done.

Thank you.
```

---

# Serial Monitor

Open the serial monitor.

```bash
arduino-cli monitor \
-p /dev/ttyUSB1 \
-c baudrate=115200
```

The current firmware prints a startup banner followed by periodic heartbeat messages.

Example

```
========================================
KLIPPER CNC ASSISTANT
ARDUINO PRO MINI
Firmware 001
Serial communication OK
========================================

ALIVE
ALIVE
ALIVE
```

---

# Hardware Validation

The following items have been successfully validated.

- Arduino CLI installation
- AVR platform installation
- Firmware compilation
- Firmware upload
- Manual reset procedure
- USB serial communication
- Serial monitor operation
