/*
 * TEST 004 - JOYSTICK CALIBRATION
 *
 * X -> A2 (UP / DOWN)
 * Y -> A1 (LEFT / RIGHT)
 * SW -> D2
 */

const byte PIN_X = A2;
const byte PIN_Y = A1;
const byte PIN_SW = 2;

void waitRelease()
{
    while (digitalRead(PIN_SW) == LOW);
    delay(250);
}

void capture(const char *title)
{
    long sumX = 0;
    long sumY = 0;

    long samples = 0;

    int minX = 1023;
    int maxX = 0;

    int minY = 1023;
    int maxY = 0;

    Serial.println();
    Serial.println("========================================");
    Serial.println(title);
    Serial.println("Move joystick to the requested position.");
    Serial.println("Press joystick button to finish capture.");
    Serial.println("========================================");

    while (digitalRead(PIN_SW) == HIGH)
    {
        int x = analogRead(PIN_X);
        int y = analogRead(PIN_Y);

        if (x < minX) minX = x;
        if (x > maxX) maxX = x;

        if (y < minY) minY = y;
        if (y > maxY) maxY = y;

        sumX += x;
        sumY += y;
        samples++;

        Serial.print("\rX=");
        Serial.print(x);
        Serial.print("   Y=");
        Serial.print(y);
        Serial.print("      ");

        delay(20);
    }

    waitRelease();

    Serial.println();
    Serial.println();

    Serial.print("Samples : ");
    Serial.println(samples);

    Serial.print("Average X : ");
    Serial.println((float)sumX / samples, 2);

    Serial.print("Average Y : ");
    Serial.println((float)sumY / samples, 2);

    Serial.print("Minimum X : ");
    Serial.println(minX);

    Serial.print("Maximum X : ");
    Serial.println(maxX);

    Serial.print("Minimum Y : ");
    Serial.println(minY);

    Serial.print("Maximum Y : ");
    Serial.println(maxY);
}

void setup()
{
    pinMode(PIN_SW, INPUT_PULLUP);

    Serial.begin(115200);

    while (!Serial);

    Serial.println();
    Serial.println("========================================");
    Serial.println("TEST 004 - JOYSTICK CALIBRATION");
    Serial.println("========================================");
    Serial.println();
    Serial.println("Detected mapping:");
    Serial.println("A2 -> UP / DOWN");
    Serial.println("A1 -> LEFT / RIGHT");

    capture("STEP 1 - CENTER");
    capture("STEP 2 - LEFT");
    capture("STEP 3 - RIGHT");
    capture("STEP 4 - UP");
    capture("STEP 5 - DOWN");

    Serial.println();
    Serial.println("========================================");
    Serial.println("CALIBRATION COMPLETE");
    Serial.println("========================================");
}

void loop()
{
}
