/*
=====================================================================
 arduinocode.cpp - Smart Solar Kiosk Water Vending Subsystem
 CLEAN VERSION - No Ultrasonic Spam
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
#define INACTIVITY_TIMEOUT 300000
#define CUP_DISTANCE_CM   15.0    // Increased distance
#define WATER_MODE 1
#define CHARGE_MODE 2

// ---------------- GLOBAL VARIABLES ----------------
int currentMode = WATER_MODE;
float pulsesPerLiter = 450.0;

// Coin settings
int coin1P_pulses = 1;
int coin5P_pulses = 3;
int coin10P_pulses = 5;

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

// Cup detection - SIMPLIFIED
bool cupDetected = false;
unsigned long lastCupCheck = 0;
#define CUP_CHECK_INTERVAL 1000  // Check only once per second

// ---------------- INTERRUPTS ----------------
void coinISR() {
  unsigned long now = millis();
  if (now - lastCoinPulseTime > COIN_DEBOUNCE_MS) {
    coinPulseCount++;
    lastCoinPulseTime = now;
    Serial.print("[COIN] Pulse count: ");
    Serial.println(coinPulseCount);
  }
}

void flowISR() {
  flowPulseCount++;
}

// ---------------- SETUP ----------------
void setup() {
  Serial.begin(115200);
  while (!Serial) {
    ; // Wait for serial port to connect
  }

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

  Serial.println("=== WATER VENDO SYSTEM READY ===");
  Serial.println("Send commands: MODE WATER, MODE CHARGE, START, STOP, STATUS");
  lastActivity = millis();
}

// ---------------- LOOP ----------------
void loop() {
  handleSerialCommand();
  handleCoin();
  
  // CUP DETECTION - Only check occasionally
  if (millis() - lastCupCheck > CUP_CHECK_INTERVAL) {
    checkCup();
    lastCupCheck = millis();
  }
  
  handleDispensing();

  if (millis() - lastActivity > INACTIVITY_TIMEOUT && !dispensing) {
    resetSystem();
  }

  delay(100); // Slow down the loop
}

// ---------------- CUP DETECTION - SIMPLIFIED ----------------
bool readUltrasonic() {
  digitalWrite(CUP_TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(CUP_TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(CUP_TRIG_PIN, LOW);

  long duration = pulseIn(CUP_ECHO_PIN, HIGH, 30000); // 30ms timeout
  
  if (duration == 0) {
    return false; // No echo received
  }
  
  float distance = duration * 0.034 / 2;
  return (distance > 2.0 && distance < CUP_DISTANCE_CM);
}

void checkCup() {
  bool currentCupState = readUltrasonic();
  
  if (currentCupState != cupDetected) {
    cupDetected = currentCupState;
    if (cupDetected) {
      Serial.println("CUP_DETECTED");
      // Auto-start if we have credit
      if (creditML > 0 && !dispensing) {
        startDispense(creditML);
      }
    } else {
      Serial.println("CUP_REMOVED");
      if (dispensing) {
        stopDispenseEarly();
      }
    }
  }
}

// ---------------- COIN HANDLER ----------------
void handleCoin() {
  if (coinPulseCount > 0 && (millis() - lastCoinPulseTime > COIN_TIMEOUT_MS)) {
    int pulses = coinPulseCount;
    coinPulseCount = 0; // Reset immediately
    
    Serial.print("[COIN] Processing ");
    Serial.print(pulses);
    Serial.println(" pulses");
    
    // Simple coin detection
    if (pulses == 1 || pulses == 2) {
      processCoin(1, 50);
    } 
    else if (pulses == 3 || pulses == 4) {
      processCoin(5, 250);
    }
    else if (pulses >= 5 && pulses <= 7) {
      processCoin(10, 500);
    }
    else {
      Serial.print("[COIN] Unknown pulse pattern: ");
      Serial.println(pulses);
    }
  }
}

void processCoin(int peso, int ml) {
  Serial.print("COIN_INSERTED ");
  Serial.println(peso);

  if (currentMode == WATER_MODE) {
    creditML += ml;
    Serial.print("COIN_WATER ");
    Serial.println(ml);
    Serial.print("[BALANCE] Total: ");
    Serial.print(creditML);
    Serial.println(" mL");
  } 
  else if (currentMode == CHARGE_MODE) {
    Serial.print("COIN_CHARGE ");
    Serial.println(peso);
  }
  
  lastActivity = millis();
}

// ---------------- DISPENSING ----------------
void startDispense(int ml) {
  if (dispensing) return;
  
  startFlowCount = flowPulseCount;
  targetPulses = (unsigned long)((ml / 1000.0) * pulsesPerLiter);
  digitalWrite(PUMP_PIN, HIGH);
  digitalWrite(VALVE_PIN, HIGH);
  dispensing = true;

  Serial.println("DISPENSE_START");
  Serial.print("[DISPENSE] Target: ");
  Serial.print(targetPulses);
  Serial.println(" pulses");
}

void handleDispensing() {
  if (!dispensing) return;

  unsigned long dispensedPulses = flowPulseCount - startFlowCount;
  
  if (dispensedPulses >= targetPulses) {
    stopDispense();
  }
}

void stopDispense() {
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  dispensing = false;

  unsigned long dispensedPulses = flowPulseCount - startFlowCount;
  float dispensedML = (dispensedPulses / pulsesPerLiter) * 1000.0;
  
  Serial.print("DISPENSE_DONE ");
  Serial.println(dispensedML, 1);
  Serial.println("[DISPENSE] Completed");

  creditML = 0;
}

void stopDispenseEarly() {
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  dispensing = false;

  unsigned long dispensedPulses = flowPulseCount - startFlowCount;
  float dispensedML = (dispensedPulses / pulsesPerLiter) * 1000.0;
  float remaining = creditML - dispensedML;
  
  if (remaining < 0) remaining = 0;
  
  Serial.print("CREDIT_LEFT ");
  Serial.println(remaining, 1);

  creditML = remaining;
}

// ---------------- SERIAL COMMAND HANDLER ----------------
void handleSerialCommand() {
  if (!Serial.available()) return;
  
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();

  if (cmd.equalsIgnoreCase("MODE WATER")) {
    currentMode = WATER_MODE;
    Serial.println("MODE: WATER");
    resetSystem();
  }
  else if (cmd.equalsIgnoreCase("MODE CHARGE")) {
    currentMode = CHARGE_MODE;
    Serial.println("MODE: CHARGE");
    resetSystem();
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
      Serial.println("ERROR: Cannot start");
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
  else if (cmd.equalsIgnoreCase("RESET")) {
    resetSystem();
  }
  else if (cmd.equalsIgnoreCase("PING")) {
    Serial.println("PONG");
  }
  else if (cmd.length() > 0) {
    Serial.print("UNKNOWN COMMAND: ");
    Serial.println(cmd);
  }
}

// ---------------- RESET ----------------
void resetSystem() {
  creditML = 0;
  dispensing = false;
  cupDetected = false;
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  Serial.println("SYSTEM RESET");
  lastActivity = millis();
}