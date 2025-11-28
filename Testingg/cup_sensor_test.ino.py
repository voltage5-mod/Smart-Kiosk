/*
 * Cup Sensor Test - HC-SR04 Ultrasonic Sensor Diagnostic
 * Upload this to Arduino to test if the sensor is working
 */

#define TRIG_PIN 9
#define ECHO_PIN 10
#define TEST_DISTANCE_CM 15.0  // Adjust this for your desired detection distance

void setup() {
  Serial.begin(115200);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  
  Serial.println("====================================");
  Serial.println("   CUP SENSOR TEST - HC-SR04");
  Serial.println("====================================");
  Serial.println("Place object in front of sensor to test");
  Serial.print("Detection distance: ");
  Serial.print(TEST_DISTANCE_CM);
  Serial.println(" cm");
  Serial.println("====================================");
  Serial.println();
}

void loop() {
  float distance = readDistance();
  
  Serial.print("Distance: ");
  Serial.print(distance);
  Serial.print(" cm - ");
  
  if (distance <= 0) {
    Serial.println("ERROR: No sensor reading");
  } else if (distance < TEST_DISTANCE_CM) {
    Serial.println("CUP DETECTED ✓");
  } else {
    Serial.println("No cup detected");
  }
  
  // Check sensor health
  checkSensorHealth(distance);
  
  delay(1000); // Wait 1 second between readings
}

float readDistance() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  long duration = pulseIn(ECHO_PIN, HIGH, 30000); // 30ms timeout
  
  if (duration == 0) {
    return -1; // Error code for timeout
  }
  
  float distance = duration * 0.034 / 2;
  return distance;
}

void checkSensorHealth(float distance) {
  static unsigned long lastReading = 0;
  static int errorCount = 0;
  
  if (distance <= 0) {
    errorCount++;
    Serial.print("  [WARNING] Sensor error #");
    Serial.println(errorCount);
    
    if (errorCount >= 5) {
      Serial.println("  [CRITICAL] Sensor may be disconnected or faulty!");
      Serial.println("  Check wiring: VCC=5V, GND=GND, TRIG=D9, ECHO=D10");
    }
  } else {
    errorCount = 0; // Reset error count on successful reading
  }
  
  // Print detailed info every 10 readings
  static int readingCount = 0;
  readingCount++;
  if (readingCount % 10 == 0) {
    Serial.println("--- Detailed Sensor Info ---");
    Serial.println("Expected readings:");
    Serial.println("  - No object: >30 cm");
    Serial.println("  - Close object: 2-20 cm"); 
    Serial.println("  - Error: 0 or negative values");
    Serial.println("----------------------------");
  }
}