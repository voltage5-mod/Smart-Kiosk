/*
 * CoinPinTest.ino
 * Simple test for coin pin only
 */

#define COIN_PIN 2

volatile unsigned long pulseCount = 0;
volatile unsigned long lastPulseTime = 0;
bool lastPinState = HIGH;

void setup() {
  Serial.begin(115200);
  pinMode(COIN_PIN, INPUT_PULLUP);
  
  // No interrupts, we'll poll manually for testing
  // attachInterrupt(digitalPinToInterrupt(COIN_PIN), coinISR, FALLING);
  
  delay(2000);
  Serial.println("COIN_PIN_TEST_READY");
  Serial.println("Testing Pin 2 with manual polling");
  Serial.println("Pin state changes will be printed");
  Serial.println("PIN_STATE,HIGH|LOW,TIME");
}

void loop() {
  // Manual polling of pin state
  bool currentState = digitalRead(COIN_PIN);
  
  if (currentState != lastPinState) {
    unsigned long now = millis();
    
    if (currentState == LOW) {
      // Falling edge - coin pulse!
      pulseCount++;
      Serial.print("COIN_PULSE,");
      Serial.print(pulseCount);
      Serial.print(",");
      Serial.println(now);
    } else {
      // Rising edge
      Serial.print("PIN_RISING,");
      Serial.print(pulseCount);
      Serial.print(",");
      Serial.println(now);
    }
    
    lastPinState = currentState;
  }
  
  // Print status every 5 seconds
  static unsigned long lastStatus = 0;
  if (millis() - lastStatus > 5000) {
    Serial.print("STATUS,Pulses:");
    Serial.print(pulseCount);
    Serial.print(",PinState:");
    Serial.print(lastPinState ? "HIGH" : "LOW");
    Serial.print(",Time:");
    Serial.println(millis());
    lastStatus = millis();
  }
  
  delay(10); // Small delay for stability
}