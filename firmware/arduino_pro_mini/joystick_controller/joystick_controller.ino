/*
==================================================
Klipper CNC Assistant
Joystick Controller Firmware
Version 0.1.0
==================================================
*/

const byte PIN_X = A2;
const byte PIN_Y = A1;

const byte PIN_JOYSTICK = 2;
const byte PIN_BUTTON   = 3;
const byte PIN_PROBE    = 4;

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
    bool up    = x < 250;
    bool down  = x > 750;

    bool right = y < 250;
    bool left  = y > 750;

    if(up && left) return UP_LEFT;
    if(up && right) return UP_RIGHT;

    if(down && left) return DOWN_LEFT;
    if(down && right) return DOWN_RIGHT;

    if(up) return UP;
    if(down) return DOWN;

    if(left) return LEFT;
    if(right) return RIGHT;

    return CENTER;
}

byte buildFlags()
{
    byte flags = 0;

    if(digitalRead(PIN_JOYSTICK) == LOW)
        flags |= (1 << 0);

    if(digitalRead(PIN_BUTTON) == LOW)
        flags |= (1 << 1);

    if(digitalRead(PIN_PROBE) == LOW)
        flags |= (1 << 2);

    return flags;
}

void sendPacket()
{
    int x = analogRead(PIN_X);
    int y = analogRead(PIN_Y);

    Direction direction = getDirection(x, y);

    byte packet[8];

    packet[0] = HEADER;
    packet[1] = direction;
    packet[2] = buildFlags();

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
}

void setup()
{
    pinMode(PIN_JOYSTICK, INPUT_PULLUP);
    pinMode(PIN_BUTTON, INPUT_PULLUP);
    pinMode(PIN_PROBE, INPUT_PULLUP);

    Serial.begin(115200);

    while(!Serial);
}

void loop()
{
    sendPacket();

    delay(20);
}
