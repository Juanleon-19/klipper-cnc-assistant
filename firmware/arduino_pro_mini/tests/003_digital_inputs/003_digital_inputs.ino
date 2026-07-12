/*
 * Test 003 - Digital Inputs
 *
 * SW     -> D2
 * BUTTON -> D3
 * PROBE  -> D4
 */

const byte PIN_SW    = 2;
const byte PIN_BTN   = 3;
const byte PIN_PROBE = 4;

bool lastSW = HIGH;
bool lastBTN = HIGH;
bool lastPROBE = HIGH;

void setup()
{
    pinMode(PIN_SW, INPUT_PULLUP);
    pinMode(PIN_BTN, INPUT_PULLUP);
    pinMode(PIN_PROBE, INPUT_PULLUP);

    Serial.begin(115200);

    while (!Serial);

    Serial.println();
    Serial.println("========================================");
    Serial.println("TEST 003 - DIGITAL INPUTS");
    Serial.println("========================================");
    Serial.println();
    Serial.println("Waiting for events...");
    Serial.println();
}

void loop()
{
    bool sw = digitalRead(PIN_SW);
    bool btn = digitalRead(PIN_BTN);
    bool probe = digitalRead(PIN_PROBE);

    if(sw != lastSW)
    {
        Serial.print("Joystick Button : ");

        if(sw == LOW)
            Serial.println("PRESSED");
        else
            Serial.println("RELEASED");

        lastSW = sw;
    }

    if(btn != lastBTN)
    {
        Serial.print("External Button : ");

        if(btn == LOW)
            Serial.println("PRESSED");
        else
            Serial.println("RELEASED");

        lastBTN = btn;
    }

    if(probe != lastPROBE)
    {
        Serial.print("Probe : ");

        if(probe == LOW)
            Serial.println("TRIGGERED");
        else
            Serial.println("OPEN");

        lastPROBE = probe;
    }

    delay(10);
}
