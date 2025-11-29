/*
 * CoinArduino.ino
 * Dedicated coin detection only
 * Connected to USB Port 1 (Top Left)
 */

#define COIN_PIN 2

volatile unsigned long lastCoinTime = 0;
volatile int pulseCount = 0;

void coinISR() {
  unsigned long now = millis();
  if (now - lastCoinTime > 50) { // 50ms debounce
    pulseCount++;
    lastCoinTime = now;
    Serial.print("[COIN] Pulse detected: ");
    Serial.println(pulseCount);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(COIN_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(COIN_PIN), coinISR, FALLING);
  
  delay(2000); // Wait for serial connection
  Serial.println("COIN_ARDUINO_READY");
  Serial.println("DEBUG: Coin system active on Pin 2");
}

void loop() {
  // Process completed coin sequences (after 500ms of no pulses)
  if (pulseCount > 0 && (millis() - lastCoinTime > 500)) {
    int pulses = pulseCount;
    pulseCount = 0; // Reset for next coin
    
    Serial.print("[COIN] Processing ");
    Serial.print(pulses);
    Serial.println(" pulses");
    
    // Coin identification - send clear events for Python to parse
    if (pulses == 1) {
      Serial.println("COIN_INSERTED 1");
      Serial.println("COIN_WATER 50");
    } 
    else if (pulses == 2) {
      Serial.println("COIN_INSERTED 1");
      Serial.println("COIN_WATER 50");
    }
    else if (pulses == 3) {
      Serial.println("COIN_INSERTED 5");
      Serial.println("COIN_WATER 250");
    }
    else if (pulses == 4) {
      Serial.println("COIN_INSERTED 5");
      Serial.println("COIN_WATER 250");
    }
    else if (pulses >= 5 && pulses <= 7) {
      Serial.println("COIN_INSERTED 10");
      Serial.println("COIN_WATER 500");
    }
    else {
      Serial.print("COIN_UNKNOWN ");
      Serial.println(pulses);
    }
  }
  
  delay(10);
}