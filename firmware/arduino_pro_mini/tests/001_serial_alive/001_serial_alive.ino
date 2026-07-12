/*
 * Test 001 - Serial Alive
 *
 * Objective:
 * Validate firmware compilation, upload and serial communication.
 */

void setup()
{
    Serial.begin(115200);

    while (!Serial)
    {
    }

    Serial.println();
    Serial.println("========================================");
    Serial.println("KLIPPER CNC ASSISTANT");
    Serial.println("TEST 001 - SERIAL ALIVE");
    Serial.println("========================================");
}

void loop()
{
    Serial.println("ALIVE");
    delay(1000);
}
