/*
 * ==========================================
 * TEST 005 - SERIAL PROTOCOL
 * Klipper CNC Assistant
 * ==========================================
 *
 * A2 -> UP / DOWN
 * A1 -> LEFT / RIGHT
 *
 * D2 -> Joystick Button
 * D3 -> External Button
 * D4 -> Probe
 *
 * Packet (8 bytes)
 *
 * Byte0  0xAA
 * Byte1  Direction
 * Byte2  Flags
 * Byte3  X Low
 * Byte4  X High
 * Byte5  Y Low
 * Byte6  Y High
 * Byte7  Checksum
 *
 */

const byte PIN_X = A2;
const byte PIN_Y = A1;

const byte PIN_SW = 2;
const byte PIN_BTN = 3;
const byte PIN_PROBE = 4;

const byte HEADER = 0xAA;

enum Direction
{
    CENTER = 0,
    UP,
    DOWN,
    LEFT,
    RIGHT,
    UP_LEFT,
    UP_RIGHT,
    DOWN_LEFT,
    DOWN_RIGHT
};

Direction getDirection(int x, int y)
{
    bool up = (x < 250);
    bool down = (x > 750);

    bool right = (y < 250);
    bool left = (y > 750);

    if (up && left) return UP_LEFT;
    if (up && right) return UP_RIGHT;

    if (down && left) return DOWN_LEFT;
    if (down && right) return DOWN_RIGHT;

    if (up) return UP;
    if (down) return DOWN;

    if (left) return LEFT;
    if (right) return RIGHT;

    return CENTER;
}

void setup()
{
    pinMode(PIN_SW, INPUT_PULLUP);
    pinMode(PIN_BTN, INPUT_PULLUP);
    pinMode(PIN_PROBE, INPUT_PULLUP);

    Serial.begin(115200);

    while (!Serial);
}

void loop()
{
    int x = analogRead(PIN_X);
    int y = analogRead(PIN_Y);

    Direction dir = getDirection(x, y);

    byte flags = 0;

    if (digitalRead(PIN_SW) == LOW)
        flags |= (1 << 0);

    if (digitalRead(PIN_BTN) == LOW)
        flags |= (1 << 1);

    if (digitalRead(PIN_PROBE) == LOW)
        flags |= (1 << 2);

    byte packet[8];

    packet[0] = HEADER;
    packet[1] = (byte)dir;
    packet[2] = flags;

    packet[3] = lowByte(x);
    packet[4] = highByte(x);

    packet[5] = lowByte(y);
    packet[6] = highByte(y);

    packet[7] =
        packet[0] ^
        packet[1] ^
        packet[2] ^
        packet[3] ^
        packet[4] ^
        packet[5] ^
        packet[6];

    Serial.write(packet, sizeof(packet));

    delay(20);
}
