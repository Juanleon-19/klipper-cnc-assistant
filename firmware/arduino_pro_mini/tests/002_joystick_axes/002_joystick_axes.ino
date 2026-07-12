/*
 * Test 002 - Joystick Analog Axes
 *
 * Objective:
 * Validate analog readings from the HW-504 joystick.
 */

const uint8_t JOYSTICK_X_PIN = A2;
const uint8_t JOYSTICK_Y_PIN = A1;

void setup()
{
    Serial.begin(115200);

    while (!Serial)
    {
    }

    Serial.println();
    Serial.println("========================================");
    Serial.println("TEST 002 - JOYSTICK AXES");
    Serial.println("========================================");
    Serial.println("X=A2   Y=A1");
    Serial.println();
}

void loop()
{
    int x = analogRead(JOYSTICK_X_PIN);
    int y = analogRead(JOYSTICK_Y_PIN);

    Serial.print("X=");
    Serial.print(x);

    Serial.print("    Y=");
    Serial.println(y);

    delay(100);
}
