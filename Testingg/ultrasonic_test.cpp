// ultrasonic_test.ino
// Simple test sketch for ultrasonic sensor

#define TRIG_PIN 9
#define ECHO_PIN 10

void setup() {
  Serial.begin(115200);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  
  Serial.println("Ultrasonic Sensor Test Ready");
  Serial.println("Commands: READ, CALIBRATE, STOP");
}

void loop() {
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    
    if (cmd.equalsIgnoreCase("READ")) {
      readSensorContinuous();
    }
    else if (cmd.equalsIgnoreCase("CALIBRATE")) {
      calibrateSensor();
    }
    else if (cmd.equalsIgnoreCase("STOP")) {
      Serial.println("Stopped continuous reading");
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
  float distance = duration * 0.034 / 2;
  
  return distance;
}

void readSensorContinuous() {
  Serial.println("Starting continuous reading...");
  unsigned long startTime = millis();
  
  while (millis() - startTime < 30000) { // 30 seconds
    if (Serial.available()) {
      String cmd = Serial.readStringUntil('\n');
      if (cmd.equalsIgnoreCase("STOP")) break;
    }
    
    float distance = readDistance();
    Serial.print("Distance: ");
    Serial.print(distance);
    Serial.println(" cm");
    
    // Detection logic
    if (distance > 0 && distance < 15.0) {
      Serial.println("*** OBJECT DETECTED ***");
    }
    
    delay(500);
  }
  Serial.println("Continuous reading finished");
}

void calibrateSensor() {
  Serial.println("Calibration mode - take 10 readings");
  float readings[10];
  
  for (int i = 0; i < 10; i++) {
    readings[i] = readDistance();
    Serial.print("Reading ");
    Serial.print(i+1);
    Serial.print(": ");
    Serial.print(readings[i]);
    Serial.println(" cm");
    delay(1000);
  }
  
  // Calculate average
  float sum = 0;
  for (int i = 0; i < 10; i++) {
    sum += readings[i];
  }
  float average = sum / 10;
  
  Serial.print("Average distance: ");
  Serial.print(average);
  Serial.println(" cm");
  Serial.print("Recommended threshold: ");
  Serial.print(average * 0.8); // 80% of average
  Serial.println(" cm");
}