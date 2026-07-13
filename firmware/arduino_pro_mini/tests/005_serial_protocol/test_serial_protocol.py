#!/usr/bin/env python3

import serial

HEADER = 0xAA

PORT = "/dev/ttyUSB0"
BAUDRATE = 115200

DIRECTIONS = {
    0: "CENTER",
    1: "UP",
    2: "DOWN",
    3: "LEFT",
    4: "RIGHT",
    5: "UP_LEFT",
    6: "UP_RIGHT",
    7: "DOWN_LEFT",
    8: "DOWN_RIGHT",
}


def checksum(packet):
    c = 0

    for b in packet[:7]:
        c ^= b

    return c


print()
print("========================================")
print("KLIPPER CNC ASSISTANT")
print("TEST 005 - SERIAL PROTOCOL")
print("========================================")
print()

print(f"Opening {PORT}")

ser = serial.Serial(
    port=PORT,
    baudrate=BAUDRATE,
    timeout=1,
)

print("Connected.")
print("Waiting packets...")
print()

while True:

    header = ser.read(1)

    if len(header) == 0:
        continue

    if header[0] != HEADER:
        continue

    payload = ser.read(7)

    if len(payload) != 7:
        continue

    packet = bytes([HEADER]) + payload

    if checksum(packet) != packet[7]:
        print("Checksum ERROR")
        continue

    direction = packet[1]
    flags = packet[2]

    x = packet[3] | (packet[4] << 8)
    y = packet[5] | (packet[6] << 8)

    joystick = bool(flags & 0x01)
    button = bool(flags & 0x02)
    probe = bool(flags & 0x04)

    print("----------------------------------------")

    print(f"Direction : {DIRECTIONS.get(direction,'UNKNOWN')}")
    print(f"X         : {x}")
    print(f"Y         : {y}")

    print()

    print(f"Joystick  : {'PRESSED' if joystick else 'RELEASED'}")
    print(f"Button    : {'PRESSED' if button else 'RELEASED'}")
    print(f"Probe     : {'TRIGGERED' if probe else 'OPEN'}")
