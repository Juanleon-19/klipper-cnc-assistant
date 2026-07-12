/*
 * ============================================================
 * Klipper CNC Assistant
 * Arduino Pro Mini Interface
 * Firmware 001
 * ============================================================
 *
 * Objective:
 * Validate serial communication between the Arduino Pro Mini
 * and the Linux host.
 */

void setup()
{
    Serial.begin(115200);

    Serial.println();
    Serial.println("========================================");
    Serial.println("KLIPPER CNC ASSISTANT");
    Serial.println("ARDUINO PRO MINI");
    Serial.println("Firmware 001");
    Serial.println("Serial communication OK");
    Serial.println("========================================");
}

void loop()
{
    Serial.println("ALIVE");

    delay(1000);
}
