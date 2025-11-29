/*
 * CoinArduino_Test.ino
 * Simple test for coin slot - sends raw data for debugging
 */

#define COIN_PIN 2

volatile unsigned long lastCoinTime = 0;
volatile int pulseCount = 0;
volatile bool newPulse = false;

void coinISR() {
  unsigned long now = millis();
  if (now - lastCoinTime > 50) { // 50ms debounce
    pulseCount++;
    lastCoinTime = now;
    newPulse = true;
    Serial.print("[ISR] Pulse! Total: ");
    Serial.println(pulseCount);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(COIN_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(COIN_PIN), coinISR, FALLING);
  
  delay(2000); // Wait for serial connection
  Serial.println("COIN_TEST_READY");
  Serial.println("Coin test started on Pin 2");
  Serial.println("Format: [ISR] Pulse! Total: X");
  Serial.println("Waiting for coins...");
}

void loop() {
  // Check for new pulses
  if (newPulse) {
    newPulse = false;
    Serial.print("[LOOP] Processing pulse count: ");
    Serial.println(pulseCount);
  }
  
  // Process completed coin sequences (after 500ms of no pulses)
  if (pulseCount > 0 && (millis() - lastCoinTime > 500)) {
    int pulses = pulseCount;
    pulseCount = 0; // Reset for next coin
    
    Serial.print("=== COIN DETECTED ===");
    Serial.print("Pulses: ");
    Serial.println(pulses);
    
    // Simple coin identification
    if (pulses == 1) {
      Serial.println("COIN_TYPE: 1 Peso");
      Serial.println("COIN_INSERTED 1");
      Serial.println("COIN_WATER 50");
    } 
    else if (pulses == 2) {
      Serial.println("COIN_TYPE: 1 Peso (double pulse)");
      Serial.println("COIN_INSERTED 1");
      Serial.println("COIN_WATER 50");
    }
    else if (pulses == 3) {
      Serial.println("COIN_TYPE: 5 Peso");
      Serial.println("COIN_INSERTED 5");
      Serial.println("COIN_WATER 250");
    }
    else if (pulses == 4) {
      Serial.println("COIN_TYPE: 5 Peso (double pulse)");
      Serial.println("COIN_INSERTED 5");
      Serial.println("COIN_WATER 250");
    }
    else if (pulses >= 5 && pulses <= 7) {
      Serial.println("COIN_TYPE: 10 Peso");
      Serial.println("COIN_INSERTED 10");
      Serial.println("COIN_WATER 500");
    }
    else {
      Serial.print("COIN_TYPE: Unknown (");
      Serial.print(pulses);
      Serial.println(" pulses)");
      Serial.print("COIN_UNKNOWN ");
      Serial.println(pulses);
    }
    Serial.println("=====================");
  }
  
  // Send heartbeat every 10 seconds
  static unsigned long lastHeartbeat = 0;
  if (millis() - lastHeartbeat > 10000) {
    Serial.println("[HEARTBEAT] Coin Arduino running");
    lastHeartbeat = millis();
  }
  
  delay(10);
}