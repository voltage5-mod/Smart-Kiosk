// sensor_test.ino
// Simple debug sketch for sensor testing

#define TRIG_PIN 9
#define ECHO_PIN 10
#define COIN_PIN 2

void setup() {
  Serial.begin(115200);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(COIN_PIN, INPUT_PULLUP);
  
  Serial.println("SENSOR TEST READY");
  Serial.println("Commands: READ, DISTANCE, COIN, STATUS");
  Serial.println("Auto-sending distance every 2 seconds");
}

void loop() {
  // Auto-send distance every 2 seconds
  static unsigned long lastSend = 0;
  if (millis() - lastSend > 2000) {
    lastSend = millis();
    float dist = readDistance();
    Serial.print("AUTO Distance: ");
    Serial.print(dist);
    Serial.print(" cm - Detected: ");
    Serial.println(dist > 0 && dist < 15.0 ? "YES" : "NO");
  }
  
  // Handle serial commands
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    
    if (cmd.equalsIgnoreCase("READ")) {
      readContinuous();
    }
    else if (cmd.equalsIgnoreCase("DISTANCE")) {
      float dist = readDistance();
      Serial.print("Distance: ");
      Serial.print(dist);
      Serial.println(" cm");
    }
    else if (cmd.equalsIgnoreCase("COIN")) {
      int coinState = digitalRead(COIN_PIN);
      Serial.print("Coin pin state: ");
      Serial.println(coinState);
    }
    else if (cmd.equalsIgnoreCase("STATUS")) {
      Serial.println("=== STATUS ===");
      Serial.println("Sensor: Ultrasonic HC-SR04");
      Serial.println("Trig: Pin 9");
      Serial.println("Echo: Pin 10");
      Serial.println("Coin: Pin 2");
      Serial.println("==============");
    }
    else if (cmd.equalsIgnoreCase("PING")) {
      Serial.println("PONG");
    }
    else if (cmd.equalsIgnoreCase("RESET")) {
      Serial.println("RESETTING...");
      delay(100);
      setup();
    }
  }
}

float readDistance() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  
  long duration = pulseIn(ECHO_PIN, HIGH, 30000);
  if (duration == 0) {
    return -1.0; // No reading
  }
  
  float distance = duration * 0.034 / 2;
  return distance;
}

void readContinuous() {
  Serial.println("STARTING CONTINUOUS READING");
  unsigned long start = millis();
  
  while (millis() - start < 10000) { // 10 seconds
    if (Serial.available()) {
      String cmd = Serial.readStringUntil('\n');
      if (cmd.equalsIgnoreCase("STOP")) break;
    }
    
    float dist = readDistance();
    Serial.print("CONT Distance: ");
    Serial.print(dist);
    Serial.print(" cm - ");
    
    if (dist < 0) {
      Serial.println("NO READING");
    } else if (dist < 5.0) {
      Serial.println("VERY CLOSE");
    } else if (dist < 10.0) {
      Serial.println("CLOSE");
    } else if (dist < 15.0) {
      Serial.println("MEDIUM");
    } else if (dist < 20.0) {
      Serial.println("FAR");
    } else {
      Serial.println("VERY FAR");
    }
    
    delay(500);
  }
  Serial.println("CONTINUOUS READING ENDED");
}