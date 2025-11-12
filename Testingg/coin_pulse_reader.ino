// coin_pulse_reader.ino
// Simple Arduino sketch that listens for a coin acceptor pulse on a digital pin
// and prints "PULSE" over Serial for each pulse. Use this with coin_calibrator.py

const int COIN_PIN = 2; // attach coin acceptor output to digital pin 2 (interrupt)
volatile unsigned long lastPulse = 0;

void coinISR() {
  unsigned long now = millis();
  // basic debounce: ignore pulses that are too close
  if (now - lastPulse > 20) {
    lastPulse = now;
    Serial.println("PULSE");
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(COIN_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(COIN_PIN), coinISR, FALLING);
  Serial.println("Coin pulse reader ready");
}

void loop() {
  // nothing to do here; ISR prints pulses
}
