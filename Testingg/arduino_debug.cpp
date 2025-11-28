/*
 * Arduino Water System - DEBUG VERSION
 * This will help identify where the issue is
 */

#include <EEPROM.h>

// Pin Definitions
#define COIN_PIN          2
#define FLOW_SENSOR_PIN   3
#define CUP_TRIG_PIN      9
#define CUP_ECHO_PIN      10
#define PUMP_PIN          8
#define VALVE_PIN         7

// Constants
#define CUP_DISTANCE_CM   15.0
#define CUP_REMOVED_GRACE_MS 3000

// Global Variables
int creditML = 0;
bool dispensing = false;
unsigned long flowPulseCount = 0;
unsigned long startFlowCount = 0;
unsigned long targetPulses = 0;
float pulsesPerLiter = 450.0;

// Cup detection
unsigned long cupRemovedTime = 0;
bool cupRemovedFlag = false;

void setup() {
  Serial.begin(115200);
  
  pinMode(COIN_PIN, INPUT_PULLUP);
  pinMode(FLOW_SENSOR_PIN, INPUT_PULLUP);
  pinMode(CUP_TRIG_PIN, OUTPUT);
  pinMode(CUP_ECHO_PIN, INPUT);
  pinMode(PUMP_PIN, OUTPUT);
  pinMode(VALVE_PIN, OUTPUT);

  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);

  attachInterrupt(digitalPinToInterrupt(FLOW_SENSOR_PIN), flowISR, RISING);

  Serial.println("=== WATER SYSTEM DEBUG MODE ===");
  Serial.println("Commands: ADD100, ADD500, CUP_ON, CUP_OFF, START, STOP, STATUS");
  Serial.println("=================================");
}

void flowISR() {
  flowPulseCount++;
}

void loop() {
  handleSerialCommand();
  handleCup();
  handleDispensing();
  delay(100);
}

bool detectCup() {
  digitalWrite(CUP_TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(CUP_TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(CUP_TRIG_PIN, LOW);

  long duration = pulseIn(CUP_ECHO_PIN, HIGH, 30000);
  
  if (duration == 0) {
    Serial.println("ULTRASONIC: No echo");
    return false;
  }
  
  float distance = duration * 0.034 / 2;
  Serial.print("ULTRASONIC: ");
  Serial.print(distance);
  Serial.println(" cm");
  
  return (distance > 0 && distance < CUP_DISTANCE_CM);
}

void handleCup() {
  static bool lastCupState = false;
  bool cupDetected = detectCup();
  
  // Only print when state changes
  if (cupDetected != lastCupState) {
    if (cupDetected) {
      Serial.println("CUP_DETECTED");
    } else {
      Serial.println("CUP_REMOVED");
    }
    lastCupState = cupDetected;
  }
  
  if (cupDetected && creditML > 0 && !dispensing) {
    Serial.println("DEBUG: Cup detected with credit - STARTING DISPENSE");
    startDispense(creditML);
  } 
  else if (!cupDetected && dispensing) {
    if (!cupRemovedFlag) {
      cupRemovedFlag = true;
      cupRemovedTime = millis();
      Serial.println("DEBUG: Cup removed - starting grace period");
    } else if (millis() - cupRemovedTime > CUP_REMOVED_GRACE_MS) {
      Serial.println("DEBUG: Grace period expired - STOPPING");
      stopDispenseEarly();
      cupRemovedFlag = false;
    }
  }
  else if (cupDetected && dispensing && cupRemovedFlag) {
    cupRemovedFlag = false;
    Serial.println("DEBUG: Cup replaced - continuing");
  }
}

void startDispense(int ml) {
  Serial.print("DEBUG: Starting dispense for ");
  Serial.print(ml);
  Serial.println(" mL");
  
  startFlowCount = flowPulseCount;
  targetPulses = (unsigned long)((ml / 1000.0) * pulsesPerLiter);
  
  Serial.print("DEBUG: Turning on PUMP and VALVE pins ");
  Serial.print(PUMP_PIN);
  Serial.print(" and ");
  Serial.println(VALVE_PIN);
  
  digitalWrite(PUMP_PIN, HIGH);
  digitalWrite(VALVE_PIN, HIGH);
  dispensing = true;
  cupRemovedFlag = false;

  Serial.println("DISPENSE_START");
  Serial.print("DEBUG: Target pulses: ");
  Serial.println(targetPulses);
}

void handleDispensing() {
  if (!dispensing) return;

  if (cupRemovedFlag && (millis() - cupRemovedTime > CUP_REMOVED_GRACE_MS)) {
    stopDispenseEarly();
    return;
  }

  unsigned long dispensedPulses = flowPulseCount - startFlowCount;
  float dispensedML = (dispensedPulses / pulsesPerLiter) * 1000.0;

  // Print progress every 100 pulses
  if (dispensedPulses % 100 == 0) {
    Serial.print("DISPENSE_PROGRESS ml=");
    Serial.print(dispensedML, 1);
    Serial.print(" remaining=");
    Serial.println(creditML - dispensedML, 1);
  }

  if (dispensedPulses >= targetPulses) {
    Serial.println("DEBUG: Target reached - stopping");
    stopDispense();
  }
}

void stopDispense() {
  Serial.println("DEBUG: Stopping dispense - COMPLETE");
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  dispensing = false;

  unsigned long dispensedPulses = flowPulseCount - startFlowCount;
  float dispensedML = (dispensedPulses / pulsesPerLiter) * 1000.0;
  
  Serial.print("DISPENSE_DONE ");
  Serial.println(dispensedML, 1);

  creditML = 0;
}

void stopDispenseEarly() {
  Serial.println("DEBUG: Stopping dispense - EARLY");
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  dispensing = false;

  unsigned long dispensedPulses = flowPulseCount - startFlowCount;
  float dispensedML = (dispensedPulses / pulsesPerLiter) * 1000.0;
  float remaining = creditML - dispensedML;
  
  Serial.print("CREDIT_LEFT ");
  Serial.println(remaining, 1);

  creditML = remaining;
}

void handleSerialCommand() {
  if (!Serial.available()) return;
  
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  Serial.print("DEBUG: Received command: ");
  Serial.println(cmd);

  if (cmd == "ADD100") {
    creditML += 100;
    Serial.print("DEBUG: Added 100mL, total: ");
    Serial.println(creditML);
  }
  else if (cmd == "ADD500") {
    creditML += 500;
    Serial.print("DEBUG: Added 500mL, total: ");
    Serial.println(creditML);
  }
  else if (cmd == "CUP_ON") {
    // Simulate cup detection
    Serial.println("DEBUG: Simulating cup detection");
    if (creditML > 0 && !dispensing) {
      startDispense(creditML);
    }
  }
  else if (cmd == "CUP_OFF") {
    // Simulate cup removal
    Serial.println("DEBUG: Simulating cup removal");
  }
  else if (cmd == "START") {
    // Force start dispensing
    if (creditML > 0) {
      startDispense(creditML);
    } else {
      Serial.println("DEBUG: No credit to start");
    }
  }
  else if (cmd == "STOP") {
    stopDispenseEarly();
  }
  else if (cmd == "STATUS") {
    Serial.print("STATUS: creditML=");
    Serial.print(creditML);
    Serial.print(" dispensing=");
    Serial.print(dispensing);
    Serial.print(" flowPulses=");
    Serial.print(flowPulseCount);
    Serial.print(" pumpPin=");
    Serial.print(digitalRead(PUMP_PIN));
    Serial.print(" valvePin=");
    Serial.println(digitalRead(VALVE_PIN));
  }
  else if (cmd == "RESET") {
    creditML = 0;
    dispensing = false;
    digitalWrite(PUMP_PIN, LOW);
    digitalWrite(VALVE_PIN, LOW);
    Serial.println("DEBUG: System reset");
  }
}