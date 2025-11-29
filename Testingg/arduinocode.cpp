/*
=====================================================================
 arduinocode.cpp - Smart Solar Kiosk Water Vending Subsystem
 FIXED VERSION - Coin Detection & Cup Stability
=====================================================================
*/

#include <EEPROM.h>

// ---------------- PIN DEFINITIONS ----------------
#define COIN_PIN          2     // Coin slot signal pin (interrupt)
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
int currentMode = WATER_MODE;
float pulsesPerLiter = 450.0; // Default flow calibration

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
bool cupDetected = false;
bool lastCupState = false;
unsigned long cupCheckTime = 0;
#define CUP_CHECK_INTERVAL 500  // Check cup every 500ms

// ---------------- INTERRUPTS ----------------
void coinISR() {
  unsigned long now = millis();
  if (now - lastCoinPulseTime > COIN_DEBOUNCE_MS) {
    coinPulseCount++;
    lastCoinPulseTime = now;
    Serial.print("[DEBUG] Coin pulse detected: ");
    Serial.println(coinPulseCount);
  }
}

void flowISR() {
  flowPulseCount++;
}

// ---------------- SETUP ----------------
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

  attachInterrupt(digitalPinToInterrupt(COIN_PIN), coinISR, FALLING);
  attachInterrupt(digitalPinToInterrupt(FLOW_SENSOR_PIN), flowISR, RISING);

  // Load calibration from EEPROM
  EEPROM.get(0, coin1P_pulses);
  EEPROM.get(4, coin5P_pulses);
  EEPROM.get(8, coin10P_pulses);
  EEPROM.get(12, pulsesPerLiter);

  if (isnan(pulsesPerLiter) || pulsesPerLiter < 200 || pulsesPerLiter > 1000)
    pulsesPerLiter = 450.0;

  Serial.println("System Ready. Mode: WATER");
  lastActivity = millis();
}

// ---------------- LOOP ----------------
void loop() {
  handleSerialCommand();
  handleCoin();
  
  // Only handle cup detection in WATER mode
  if (currentMode == WATER_MODE) {
    if (millis() - cupCheckTime > CUP_CHECK_INTERVAL) {
      handleCup();
      cupCheckTime = millis();
    }
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
    return false;
  }
  
  float distance = duration * 0.034 / 2;
  
  // DEBUG: Print distance for monitoring
  Serial.print("[DEBUG] Ultrasonic distance: ");
  Serial.println(distance);
  
  return (distance > 2.0 && distance < CUP_DISTANCE_CM); // Added minimum distance to avoid false positives
}

void handleCup() {
  bool currentCupState = detectCup();
  
  // Only send events when state changes
  if (currentCupState != lastCupState) {
    if (currentCupState) {
      Serial.println("CUP_DETECTED");
      cupDetected = true;
      
      // Auto-start if we have credit and not already dispensing
      if (creditML > 0 && !dispensing) {
        Serial.println("[DEBUG] Auto-starting dispense due to cup detection");
        startDispense(creditML);
      }
    } else {
      Serial.println("CUP_REMOVED");
      cupDetected = false;
      
      // Stop dispensing if cup is removed
      if (dispensing) {
        Serial.println("[DEBUG] Stopping dispense due to cup removal");
        stopDispenseEarly();
      }
    }
    lastCupState = currentCupState;
  }
}

// ---------------- DISPENSING ----------------
void startDispense(int ml) {
  if (dispensing) return; // Already dispensing
  
  startFlowCount = flowPulseCount;
  targetPulses = (unsigned long)((ml / 1000.0) * pulsesPerLiter);
  digitalWrite(PUMP_PIN, HIGH);
  digitalWrite(VALVE_PIN, HIGH);
  dispensing = true;
  lastActivity = millis();

  Serial.println("DISPENSE_START");
  Serial.print("[DEBUG] Starting dispense - Target: ");
  Serial.print(targetPulses);
  Serial.println(" pulses");
}

void handleDispensing() {
  if (!dispensing) return;

  unsigned long dispensedPulses = flowPulseCount - startFlowCount;
  float dispensedML = pulsesToML(dispensedPulses);
  float remainingML = creditML - dispensedML;

  // Send progress updates every 50 pulses
  if (dispensedPulses % 50 == 0) {
    Serial.print("DISPENSE_PROGRESS ml=");
    Serial.print(dispensedML, 1);
    Serial.print(" remaining=");
    Serial.println(remainingML, 1);
  }

  if (dispensedPulses >= targetPulses) {
    stopDispense();
  }
}

void stopDispense() {
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  dispensing = false;

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

  unsigned long dispensedPulses = flowPulseCount - startFlowCount;
  float dispensedML = pulsesToML(dispensedPulses);
  float remaining = creditML - dispensedML;
  
  if (remaining < 0) remaining = 0;
  
  Serial.print("CREDIT_LEFT ");
  Serial.println(remaining, 1);

  creditML = remaining;
  lastActivity = millis();
}

// ---------------- COIN HANDLER ----------------
void handleCoin() {
  if (coinPulseCount > 0 && (millis() - lastCoinPulseTime > COIN_TIMEOUT_MS)) {
    int pulses = coinPulseCount;
    coinPulseCount = 0; // Reset immediately
    
    Serial.print("[DEBUG] Processing coin pulses: ");
    Serial.println(pulses);
    
    int peso = 0;
    int ml = 0;
    bool validCoin = false;

    // Coin validation with tolerance
    if (abs(pulses - coin1P_pulses) <= 1) { 
      peso = 1; ml = 50; validCoin = true; 
      Serial.println("[DEBUG] Identified as 1 Peso coin");
    }
    else if (abs(pulses - coin5P_pulses) <= 1) { 
      peso = 5; ml = 250; validCoin = true; 
      Serial.println("[DEBUG] Identified as 5 Peso coin");
    }
    else if (abs(pulses - coin10P_pulses) <= 1) { 
      peso = 10; ml = 500; validCoin = true; 
      Serial.println("[DEBUG] Identified as 10 Peso coin");
    }

    if (validCoin) {
      Serial.print("COIN_INSERTED "); 
      Serial.println(peso);

      if (currentMode == WATER_MODE) {
        creditML += ml;
        Serial.print("COIN_WATER "); 
        Serial.println(ml);
        Serial.print("[DEBUG] Total credit: ");
        Serial.print(creditML);
        Serial.println(" mL");
        
        // Auto-start if cup is present
        if (cupDetected && !dispensing) {
          Serial.println("[DEBUG] Auto-starting dispense after coin insert");
          startDispense(creditML);
        }
      } 
      else if (currentMode == CHARGE_MODE) {
        Serial.print("COIN_CHARGE "); 
        Serial.println(peso);
      }
      lastActivity = millis();
    } else {
      Serial.print("[DEBUG] Invalid coin pattern: ");
      Serial.println(pulses);
    }
  }
}

// ---------------- SERIAL COMMAND HANDLER ----------------
void handleSerialCommand() {
  if (!Serial.available()) return;
  
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  Serial.print("[DEBUG] Received command: ");
  Serial.println(cmd);

  if (cmd.equalsIgnoreCase("CAL")) {
    calibrateCoins();
  }
  else if (cmd.equalsIgnoreCase("FLOWCAL")) {
    calibrateFlow();
  }
  else if (cmd.equalsIgnoreCase("RESET")) {
    resetSystem();
  }
  else if (cmd.equalsIgnoreCase("MODE WATER")) {
    currentMode = WATER_MODE;
    Serial.println("MODE: WATER");
    resetSystem(); // Reset when changing modes
  }
  else if (cmd.equalsIgnoreCase("MODE CHARGE")) {
    currentMode = CHARGE_MODE;
    Serial.println("MODE: CHARGE");
    resetSystem(); // Reset when changing modes
  }
  else if (cmd.equalsIgnoreCase("START")) {
    if (currentMode == WATER_MODE && creditML > 0 && !dispensing) {
      if (cupDetected) {
        startDispense(creditML);
        Serial.println("MANUAL_START");
      } else {
        Serial.println("ERROR: No cup detected");
      }
    } else {
      Serial.println("ERROR: Cannot start - check mode, credit, or cup");
    }
  }
  else if (cmd.equalsIgnoreCase("STOP")) {
    if (dispensing) {
      stopDispenseEarly();
      Serial.println("MANUAL_STOP");
    }
  }
  else if (cmd.equalsIgnoreCase("STATUS")) {
    Serial.print("STATUS: MODE=");
    Serial.print(currentMode == WATER_MODE ? "WATER" : "CHARGE");
    Serial.print(" CREDIT=");
    Serial.print(creditML);
    Serial.print("mL DISPENSING=");
    Serial.print(dispensing ? "YES" : "NO");
    Serial.print(" CUP=");
    Serial.println(cupDetected ? "YES" : "NO");
  }
  else if (cmd.equalsIgnoreCase("PING")) {
    Serial.println("PONG");
  }
}

// ---------------- CALIBRATION ----------------
void calibrateCoins() {
  Serial.println("CALIBRATING COINS...");

  // 1 Peso
  coinPulseCount = 0;
  Serial.println("INSERT 1 PESO COIN...");
  waitForCoinPulse();
  coin1P_pulses = coinPulseCount;
  EEPROM.put(0, coin1P_pulses);

  // 5 Peso
  coinPulseCount = 0;
  Serial.println("INSERT 5 PESO COIN...");
  waitForCoinPulse();
  coin5P_pulses = coinPulseCount;
  EEPROM.put(4, coin5P_pulses);

  // 10 Peso
  coinPulseCount = 0;
  Serial.println("INSERT 10 PESO COIN...");
  waitForCoinPulse();
  coin10P_pulses = coinPulseCount;
  EEPROM.put(8, coin10P_pulses);

  Serial.print("CAL_DONE 1P=");
  Serial.print(coin1P_pulses);
  Serial.print(" 5P=");
  Serial.print(coin5P_pulses);
  Serial.print(" 10P=");
  Serial.println(coin10P_pulses);
}

void waitForCoinPulse() {
  unsigned long start = millis();
  while (millis() - start < 15000) { // 15 second timeout
    if (coinPulseCount > 0 && millis() - lastCoinPulseTime > COIN_TIMEOUT_MS) {
      Serial.print("Detected ");
      Serial.print(coinPulseCount);
      Serial.println(" pulses");
      return;
    }
    delay(100);
  }
  Serial.println("TIMEOUT - No coin detected");
}

// ---------------- RESET ----------------
void resetSystem() {
  creditML = 0;
  dispensing = false;
  cupDetected = false;
  lastCupState = false;
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  Serial.println("SYSTEM RESET");
  lastActivity = millis();
}