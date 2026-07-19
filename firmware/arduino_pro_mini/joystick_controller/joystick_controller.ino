/*
==================================================
Klipper CNC Assistant
Joystick Controller Firmware
Version 0.1.1

Cambio:
- Filtro temporal para la sonda D4.
- Mantiene exactamente el protocolo binario anterior.
- No cambia joystick, botones, baudios ni estructura del paquete.
==================================================
*/

const byte PIN_X = A2;
const byte PIN_Y = A1;

const byte PIN_JOYSTICK = 2;
const byte PIN_BUTTON   = 3;
const byte PIN_PROBE    = 4;

const byte HEADER = 0xAA;

/*
 * El protocolo conserva aproximadamente un paquete cada 20 ms.
 */
const uint32_t PACKET_INTERVAL_MS = 20UL;

/*
 * D4 usa INPUT_PULLUP: LOW significa contacto y HIGH significa OPEN.
 * El filtro es bidireccional y no conserva contactos históricos.
 */
const uint32_t PROBE_TRIGGER_FILTER_MS = 20UL;
const uint32_t PROBE_RELEASE_FILTER_MS = 40UL;

bool probeCandidate = false;
bool probeFiltered = false;

uint32_t probeCandidateSinceMs = 0;
uint32_t probeChangedAtMs = 0;
uint32_t lastPacketAtMs = 0;

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

    if (up && left)
        return UP_LEFT;

    if (up && right)
        return UP_RIGHT;

    if (down && left)
        return DOWN_LEFT;

    if (down && right)
        return DOWN_RIGHT;

    if (up)
        return UP;

    if (down)
        return DOWN;

    if (left)
        return LEFT;

    if (right)
        return RIGHT;

    return CENTER;
}

/*
 * Actualiza la única fuente del bit de sonda del paquete.
 * La resta unsigned tolera el rollover de millis().
 */
void updateProbeFilter()
{
    const uint32_t nowMs = millis();
    const bool rawTriggered = digitalRead(PIN_PROBE) == LOW;

    if (rawTriggered != probeCandidate)
    {
        probeCandidate = rawTriggered;
        probeCandidateSinceMs = nowMs;
    }

    const uint32_t requiredMs =
        probeCandidate
            ? PROBE_TRIGGER_FILTER_MS
            : PROBE_RELEASE_FILTER_MS;

    if (
        probeFiltered != probeCandidate &&
        static_cast<uint32_t>(nowMs - probeCandidateSinceMs) >= requiredMs
    )
    {
        probeFiltered = probeCandidate;
        probeChangedAtMs = nowMs;
    }
}

byte buildFlags()
{
    byte flags = 0;

    if (digitalRead(PIN_JOYSTICK) == LOW)
    {
        flags |= (1 << 0);
    }

    if (digitalRead(PIN_BUTTON) == LOW)
    {
        flags |= (1 << 1);
    }

    /*
     * IMPORTANTE:
     * Se envía la señal filtrada, no la lectura directa de D4.
     */
    if (probeFiltered)
    {
        flags |= (1 << 2);
    }

    return flags;
}

void sendPacket()
{
    const int x = analogRead(PIN_X);
    const int y = analogRead(PIN_Y);

    const Direction direction = getDirection(x, y);

    byte packet[8];

    packet[0] = HEADER;
    packet[1] = static_cast<byte>(direction);
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

    /* El primer paquete refleja el nivel físico actual de D4. */
    const uint32_t nowMs = millis();
    const bool rawTriggered = digitalRead(PIN_PROBE) == LOW;
    probeCandidate = rawTriggered;
    probeFiltered = rawTriggered;
    probeCandidateSinceMs = nowMs;
    probeChangedAtMs = nowMs;
    lastPacketAtMs = nowMs;
}

void loop()
{
    /*
     * No usar delay(20), porque necesitamos vigilar D4
     * continuamente para filtrar correctamente los pulsos.
     */
    updateProbeFilter();

    const uint32_t nowMs = millis();

    if (static_cast<uint32_t>(nowMs - lastPacketAtMs) >= PACKET_INTERVAL_MS)
    {
        lastPacketAtMs = nowMs;
        sendPacket();
    }
}
