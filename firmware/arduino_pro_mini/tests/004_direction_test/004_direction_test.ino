/*
 * Test 004 - Direction Test
 *
 * X  -> A2
 * Y  -> A1
 * SW -> D2
 * BTN-> D3
 * PROBE -> D4
 */

const byte PIN_X = A2;
const byte PIN_Y = A1;

const byte PIN_SW = 2;
const byte PIN_BTN = 3;
const byte PIN_PROBE = 4;

String lastState = "";

String direction(int x, int y)
{
    bool left  = x < 300;
    bool right = x > 700;

    bool up    = y < 300;
    bool down  = y > 700;

    if(!left && !right && !up && !down)
        return "CENTER";

    if(left && up)
        return "UP_LEFT";

    if(right && up)
        return "UP_RIGHT";

    if(left && down)
        return "DOWN_LEFT";

    if(right && down)
        return "DOWN_RIGHT";

    if(left)
        return "LEFT";

    if(right)
        return "RIGHT";

    if(up)
        return "UP";

    return "DOWN";
}

void setup()
{
    pinMode(PIN_SW, INPUT_PULLUP);
    pinMode(PIN_BTN, INPUT_PULLUP);
    pinMode(PIN_PROBE, INPUT_PULLUP);

    Serial.begin(115200);

    while(!Serial);

    Serial.println();
    Serial.println("========================================");
    Serial.println("TEST 004 - DIRECTION TEST");
    Serial.println("========================================");
    Serial.println();
}

void loop()
{
    int x = analogRead(PIN_X);
    int y = analogRead(PIN_Y);

    String state = direction(x,y);

    if(state != lastState)
    {
        Serial.println(state);
        lastState = state;
    }

    static bool lastSW = HIGH;
    static bool lastBTN = HIGH;
    static bool lastPROBE = HIGH;

    bool sw = digitalRead(PIN_SW);
    bool btn = digitalRead(PIN_BTN);
    bool probe = digitalRead(PIN_PROBE);

    if(sw != lastSW)
    {
        if(sw == LOW)
            Serial.println("JOY_BUTTON");

        lastSW = sw;
    }

    if(btn != lastBTN)
    {
        if(btn == LOW)
            Serial.println("EXT_BUTTON");

        lastBTN = btn;
    }

    if(probe != lastPROBE)
    {
        if(probe == LOW)
            Serial.println("PROBE");

        lastPROBE = probe;
    }

    delay(20);
}
