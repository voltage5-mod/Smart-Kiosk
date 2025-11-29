/*
 * SimpleCoinTest.ino
 * Basic coin detection test with clear output
 */

#define COIN_PIN 2

void setup() {
  Serial.begin(115200);
  pinMode(COIN_PIN, INPUT_PULLUP);
  
  // Attach interrupt for coin detection
  attachInterrupt(digitalPinToInterrupt(COIN_PIN), coinPulse, FALLING);
  
  delay(2000); // Wait for serial
  Serial.println("COIN_TEST_STARTED");
  Serial.println("Pin 2 configured for coin input");
  Serial.println("READY - Insert coins to test");
  Serial.println("--------------------------------");
}

volatile int pulseCount = 0;
volatile unsigned long lastPulseTime = 0;

void coinPulse() {
  unsigned long now = millis();
  // Simple debounce - ignore pulses within 50ms
  if (now - lastPulseTime > 50) {
    pulseCount++;
    lastPulseTime = now;
    Serial.print("PULSE_DETECTED: ");
    Serial.println(pulseCount);
  }
}

void loop() {
  // Process completed coins (no pulses for 500ms)
  static unsigned long lastProcessTime = 0;
  
  if (pulseCount > 0 && (millis() - lastPulseTime > 500)) {
    int coins = pulseCount;
    pulseCount = 0;
    
    Serial.println("=== COIN PROCESSING ===");
    Serial.print("Total pulses: ");
    Serial.println(coins);
    
    // Determine coin value
    if (coins == 1 || coins == 2) {
      Serial.println("COIN: 1 PESO");
      Serial.println("COIN_INSERTED 1");
      Serial.println("COIN_WATER 50");
    }
    else if (coins == 3 || coins == 4) {
      Serial.println("COIN: 5 PESO");
      Serial.println("COIN_INSERTED 5");
      Serial.println("COIN_WATER 250");
    }
    else if (coins >= 5) {
      Serial.println("COIN: 10 PESO");
      Serial.println("COIN_INSERTED 10");
      Serial.println("COIN_WATER 500");
    }
    
    Serial.println("======================");
    lastProcessTime = millis();
  }
  
  // Heartbeat every 3 seconds
  static unsigned long lastHeartbeat = 0;
  if (millis() - lastHeartbeat > 3000) {
    Serial.println("COIN_ARDUINO_ALIVE - Waiting for coins...");
    lastHeartbeat = millis();
  }
  
  delay(10);
}