/*
 * WaterArduino.ino
 * Handles ultrasonic cup detection and water dispensing
 * Connected to USB Port 4 (Bottom Right)
 */

#include <EEPROM.h>

// ---------------- PIN DEFINITIONS ----------------
#define COIN_PIN          2     // NOT USED - Coin handled by separate Arduino
#define FLOW_SENSOR_PIN   3     // YF-S201 flow sensor (interrupt)
#define CUP_TRIG_PIN      9     // Ultrasonic trigger
#define CUP_ECHO_PIN      10    // Ultrasonic echo
#define PUMP_PIN          8     // Pump relay
#define VALVE_PIN         7     // Solenoid valve relay

// ---------------- CONSTANTS ----------------
#define COIN_DEBOUNCE_MS  50
#define COIN_TIMEOUT_MS   800
#define INACTIVITY_TIMEOUT 300000 // 5 minutes
#define CUP_DISTANCE_CM   10.0
#define CUP_REMOVED_GRACE_MS 3000  // 3 seconds grace period when cup is removed
#define WATER_MODE 1
#define CHARGE_MODE 2

// ---------------- GLOBAL VARIABLES ----------------
int currentMode = WATER_MODE; // Default mode (Pi can change this)
float pulsesPerLiter = 4305.0; // Flow calibration (YF-S201 ~450/L)

// Coin settings (EEPROM stored)
int coin1P_pulses = 1;
int coin5P_pulses = 3;
int coin10P_pulses = 5;

// Coin credits
int creditML_1P = 50;
int creditML_5P = 250;
int creditML_10P = 500;

// Volatiles
volatile unsigned long lastCoinPulseTime = 0;
volatile int coinPulseCount = 0;
volatile unsigned long flowPulseCount = 0;

// System state
bool dispensing = false;
int creditML = 0;
unsigned long targetPulses = 0;
unsigned long startFlowCount = 0;
unsigned long lastActivity = 0;

// Cup detection variables
unsigned long cupRemovedTime = 0;
bool cupRemovedFlag = false;
bool lastCupDetected = false;
unsigned int cupConsecutiveReadings = 0;

// ---------------- INTERRUPTS ----------------
void coinISR() {
  // NOT USED - Coin handled by separate Arduino
}

void flowISR() {
  flowPulseCount++;
}

// ---------------- SETUP ----------------
void setup() {
  Serial.begin(115200);

  // NOTE: COIN_PIN not used - handled by separate Arduino
  pinMode(FLOW_SENSOR_PIN, INPUT_PULLUP);
  pinMode(CUP_TRIG_PIN, OUTPUT);
  pinMode(CUP_ECHO_PIN, INPUT);
  pinMode(PUMP_PIN, OUTPUT);
  pinMode(VALVE_PIN, OUTPUT);

  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);

  // Only attach flow sensor interrupt (no coin interrupt)
  attachInterrupt(digitalPinToInterrupt(FLOW_SENSOR_PIN), flowISR, RISING);

  EEPROM.get(0, coin1P_pulses);
  EEPROM.get(4, coin5P_pulses);
  EEPROM.get(8, coin10P_pulses);
  EEPROM.get(12, pulsesPerLiter);

  if (isnan(pulsesPerLiter) || pulsesPerLiter < 200 || pulsesPerLiter > 1000)
    pulsesPerLiter = 450.0;

  // Initialize cup detection variables
  cupRemovedFlag = false;
  cupRemovedTime = 0;
  lastCupDetected = false;
  cupConsecutiveReadings = 0;

  Serial.println("WATER_ARDUINO_READY");
  Serial.println("System Ready. Waiting for Pi commands...");
  lastActivity = millis();
}

// ---------------- LOOP ----------------
void loop() {
  handleSerialCommand();
  
  // Only handle cup detection in WATER mode
  if (currentMode == WATER_MODE) {
    handleCup();
  }
  
  handleDispensing();

  if (millis() - lastActivity > INACTIVITY_TIMEOUT && !dispensing) {
    resetSystem();
  }

  delay(50);
}

// ---------------- HELPER FUNCTIONS ----------------
float pulsesToML(unsigned long pulses) {
  return (pulses / pulsesPerLiter) * 1000.0;
}

// ---------------- CUP DETECTION ----------------
bool detectCup() {
  digitalWrite(CUP_TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(CUP_TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(CUP_TRIG_PIN, LOW);

  long duration = pulseIn(CUP_ECHO_PIN, HIGH, 30000);
  
  if (duration == 0) {
    // Timeout - no echo received
    return false;
  }
  
  float distance = duration * 0.034 / 2;
  
  // More reliable cup detection with hysteresis
  bool currentCupState = (distance > 0 && distance < CUP_DISTANCE_CM);
  
  // Require multiple consistent readings to avoid false triggers
  if (currentCupState == lastCupDetected) {
    cupConsecutiveReadings++;
  } else {
    cupConsecutiveReadings = 0;
  }
  
  // Only return true after 3 consistent readings
  bool reliableDetection = (cupConsecutiveReadings >= 3 && currentCupState);
  
  // Debug output (less frequent)
  static unsigned long lastDebug = 0;
  if (millis() - lastDebug > 1000) {
    Serial.print("[CUP_DEBUG] Distance: ");
    Serial.print(distance);
    Serial.print("cm, State: ");
    Serial.print(currentCupState ? "YES" : "NO");
    Serial.print(", Reliable: ");
    Serial.print(reliableDetection ? "YES" : "NO");
    Serial.print(", Consecutive: ");
    Serial.println(cupConsecutiveReadings);
    lastDebug = millis();
  }
  
  return reliableDetection;
}

void handleCup() {
  bool cupDetected = detectCup();
  
  // Only send events when state changes
  if (cupDetected && !lastCupDetected) {
    Serial.println("CUP_DETECTED");
    lastCupDetected = true;
    cupRemovedFlag = false;  // Reset the flag
    
    // Auto-start dispensing if credit available
    if (creditML > 0 && !dispensing) {
      Serial.println("AUTO_START_DISPENSE");
      startDispense(creditML);
    }
  } 
  else if (!cupDetected && lastCupDetected) {
    // Cup removed during dispensing
    if (!cupRemovedFlag) {
      // First time detecting cup removal - start grace period
      cupRemovedFlag = true;
      cupRemovedTime = millis();
      Serial.println("CUP_REMOVED - Grace period started (3 seconds)");
    } else {
      // Cup already removed, check if grace period expired
      unsigned long timeSinceRemoval = millis() - cupRemovedTime;
      
      if (timeSinceRemoval > CUP_REMOVED_GRACE_MS) {
        Serial.println("CUP_REMOVED - Grace period expired, stopping dispensing");
        stopDispenseEarly();
        cupRemovedFlag = false;
      }
    }
    lastCupDetected = false;
  }
  else if (cupDetected && dispensing && cupRemovedFlag) {
    // Cup placed back during grace period - resume normally
    cupRemovedFlag = false;
    Serial.println("CUP_DETECTED - Cup replaced, continuing dispensing");
  }
  else if (!cupDetected && !dispensing && cupRemovedFlag) {
    // Cup still removed but not dispensing - reset flag
    cupRemovedFlag = false;
  }
}

// ---------------- DISPENSING ----------------
void startDispense(int ml) {
  startFlowCount = flowPulseCount;
  targetPulses = (unsigned long)((ml / 1000.0) * pulsesPerLiter);
  digitalWrite(PUMP_PIN, HIGH);
  digitalWrite(VALVE_PIN, HIGH);
  dispensing = true;
  cupRemovedFlag = false;  // Ensure flag is reset when starting
  lastActivity = millis();

  Serial.println("DISPENSE_START");
  Serial.print("DISPENSE_TARGET ");
  Serial.println(ml);
}

void handleDispensing() {
  if (!dispensing) return;

  // Check if cup has been removed for too long (only in WATER mode)
  if (currentMode == WATER_MODE && cupRemovedFlag && (millis() - cupRemovedTime > CUP_REMOVED_GRACE_MS)) {
    Serial.println("[DEBUG] Cup removal grace period expired in handleDispensing");
    stopDispenseEarly();
    return;
  }

  unsigned long dispensedPulses = flowPulseCount - startFlowCount;
  float dispensedML = pulsesToML(dispensedPulses);
  float remainingML = creditML - dispensedML;

  // Send progress updates
  static unsigned long lastProgress = 0;
  if (millis() - lastProgress > 1000) { // Send progress every second
    Serial.print("DISPENSE_PROGRESS ml=");
    Serial.print(dispensedML, 1);
    Serial.print(" remaining=");
    Serial.println(remainingML, 1);
    lastProgress = millis();
  }

  if (dispensedPulses >= targetPulses) {
    Serial.println("[DEBUG] Target pulses reached, stopping dispense");
    stopDispense();
  }
}

void stopDispense() {
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  dispensing = false;
  cupRemovedFlag = false;

  unsigned long dispensedPulses = flowPulseCount - startFlowCount;
  float dispensedML = pulsesToML(dispensedPulses);
  
  Serial.print("DISPENSE_DONE ");
  Serial.println(dispensedML, 1);

  creditML = 0;  // All credit used
  lastActivity = millis();
}

void stopDispenseEarly() {
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  dispensing = false;
  cupRemovedFlag = false;

  unsigned long dispensedPulses = flowPulseCount - startFlowCount;
  float dispensedML = pulsesToML(dispensedPulses);
  float remaining = creditML - dispensedML;
  
  // Ensure we don't have negative remaining
  if (remaining < 0) remaining = 0;
  
  Serial.print("CREDIT_LEFT ");
  Serial.println(remaining, 1);

  creditML = remaining;  // Save remaining credit for next time
  lastActivity = millis();
}

// ---------------- COIN HANDLER ----------------
void handleCoin() {
  // NOT USED - Coin handled by separate Arduino
}

// ---------------- SERIAL COMMAND HANDLER ----------------
void handleSerialCommand() {
  if (!Serial.available()) return;
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();

  if (cmd.equalsIgnoreCase("CAL")) calibrateCoins();
  else if (cmd.equalsIgnoreCase("FLOWCAL")) calibrateFlow();
  else if (cmd.equalsIgnoreCase("RESET")) resetSystem();
  else if (cmd.equalsIgnoreCase("MODE WATER")) {
    currentMode = WATER_MODE;
    Serial.println("MODE: WATER");
  }
  else if (cmd.equalsIgnoreCase("MODE CHARGE")) {
    currentMode = CHARGE_MODE;
    Serial.println("MODE: CHARGE");
  }
  else if (cmd.equalsIgnoreCase("START")) {
    if (currentMode == WATER_MODE && creditML > 0 && !dispensing) {
      startDispense(creditML);
      Serial.println("MANUAL_START");
    } else {
      Serial.println("ERROR: Cannot start - check mode, credit, or dispensing status");
    }
  }
  else if (cmd.equalsIgnoreCase("STOP")) {
    if (dispensing) {
      stopDispenseEarly();
      Serial.println("MANUAL_STOP");
    }
  }
  else if (cmd.equalsIgnoreCase("ADD100")) {
    if (currentMode == WATER_MODE) {
      creditML += 100;
      Serial.print("ADDED_CREDIT ");
      Serial.println(creditML);
    }
  }
  else if (cmd.equalsIgnoreCase("ADD500")) {
    if (currentMode == WATER_MODE) {
      creditML += 500;
      Serial.print("ADDED_CREDIT ");
      Serial.println(creditML);
    }
  }
  else if (cmd.equalsIgnoreCase("STATUS")) {
    Serial.print("STATUS_MODE "); Serial.println(currentMode == WATER_MODE ? "WATER" : "CHARGE");
    Serial.print("STATUS_CREDIT_ML "); Serial.println(creditML);
    Serial.print("STATUS_DISPENSING "); Serial.println(dispensing ? "YES" : "NO");
    Serial.print("STATUS_FLOW_PULSES "); Serial.println(flowPulseCount);
    Serial.print("STATUS_CUP_REMOVED_FLAG "); Serial.println(cupRemovedFlag ? "YES" : "NO");
    Serial.print("STATUS_CUP_DETECTED "); Serial.println(lastCupDetected ? "YES" : "NO");
    if (cupRemovedFlag) {
      Serial.print("STATUS_TIME_SINCE_REMOVAL "); 
      Serial.println(millis() - cupRemovedTime);
    }
  }
}

// ---------------- CALIBRATION ----------------
void calibrateCoins() {
  Serial.println("Calibrating coins...");

  coinPulseCount = 0;
  Serial.println("Insert 1 Peso...");
  waitForCoinPulse();
  coin1P_pulses = coinPulseCount;
  EEPROM.put(0, coin1P_pulses);

  coinPulseCount = 0;
  Serial.println("Insert 5 Peso...");
  waitForCoinPulse();
  coin5P_pulses = coinPulseCount;
  EEPROM.put(4, coin5P_pulses);

  coinPulseCount = 0;
  Serial.println("Insert 10 Peso...");
  waitForCoinPulse();
  coin10P_pulses = coinPulseCount;
  EEPROM.put(8, coin10P_pulses);

  Serial.print("CAL_DONE 1="); Serial.print(coin1P_pulses);
  Serial.print(" 5="); Serial.print(coin5P_pulses);
  Serial.print(" 10="); Serial.println(coin10P_pulses);
}

void waitForCoinPulse() {
  unsigned long start = millis();
  while (millis() - start < 10000) {
    if (coinPulseCount > 0 && millis() - lastCoinPulseTime > COIN_TIMEOUT_MS) return;
  }
  Serial.println("Timeout. Skipped coin.");
}

void calibrateFlow() {
  Serial.println("FLOW CALIBRATION: Collect exactly 1000 ml and type DONE when ready.");

  flowPulseCount = 0;
  digitalWrite(PUMP_PIN, HIGH);
  digitalWrite(VALVE_PIN, HIGH);
  while (true) {
    if (Serial.available()) {
      String cmd = Serial.readStringUntil('\n');
      cmd.trim();
      if (cmd.equalsIgnoreCase("DONE")) break;
    }
  }
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);

  pulsesPerLiter = flowPulseCount;
  EEPROM.put(12, pulsesPerLiter);
  Serial.print("New calibration saved: ");
  Serial.print(pulsesPerLiter);
  Serial.println(" pulses per liter.");
}

// ---------------- RESET ----------------
void resetSystem() {
  creditML = 0;
  dispensing = false;
  cupRemovedFlag = false;
  lastCupDetected = false;
  cupConsecutiveReadings = 0;
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  Serial.println("System reset.");
  lastActivity = millis();
}