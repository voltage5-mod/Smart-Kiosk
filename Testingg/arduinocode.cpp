/*
=====================================================================
 arduinocode.cpp - Smart Solar Kiosk Water Vending Subsystem
 Version: Final Integrated Build (Arduino–Raspberry Pi)
 Date: November 2025
=====================================================================

⚙️ PURPOSE:
This firmware controls the water vending subsystem of the Smart Solar Kiosk.
It interfaces with the Raspberry Pi via Serial (USB), and manages:
 - Coin acceptor pulse input
 - Water flow sensor (YF-S201)
 - Ultrasonic cup detection
 - Pump and solenoid valve relays
 - EEPROM calibration for coins and flow rate
 - Real-time feedback via Serial for Pi UI updates

=====================================================================
🔄 NEW CHANGES FROM PREVIOUS VERSION:
=====================================================================
🆕 1. Added "COIN_INSERTED" event
    → Sent immediately when a coin is recognized (before crediting).
    → Allows Raspberry Pi to trigger instant popup window.

🆕 2. Added "currentMode" variable and Serial control:
    → Pi can send "MODE WATER" or "MODE CHARGE".
    → Controls logic for credit computation and messaging.

🆕 3. Expanded serial protocol:
    → All system messages standardized into clear event types.
    → Example events:
       - COIN_INSERTED 5
       - COIN_WATER 500
       - COIN_CHARGE 10
       - CUP_DETECTED
       - DISPENSE_START / DISPENSE_DONE
       - CREDIT_LEFT 150

🆕 4. Improved calibration reporting:
    → Outputs "CAL_DONE 1=1 5=3 10=5" for verification.

🆕 5. Enhanced safety and clarity:
    → Ensures solenoid/pump off during idle/reset.
    → Flow and coin timeouts for noise immunity.

=====================================================================
🔗 SERIAL COMMUNICATION SUMMARY:
=====================================================================
Arduino → Pi messages (examples):

  - COIN_INSERTED 5          → physical coin detected
  - COIN_WATER 500           → +500mL credit (WATER mode)
  - COIN_CHARGE 10           → ₱10 for charging (CHARGE mode)
  - CUP_DETECTED             → cup placed under nozzle
  - DISPENSE_START           → water dispensing started
  - DISPENSE_PROGRESS ml=300 remaining=200
  - DISPENSE_DONE 500.0      → complete
  - CREDIT_LEFT 150          → unused mL balance after removal
  - MODE: WATER              → confirmation after Pi command
  - SYSTEM_RESET             → after RESET

Pi → Arduino commands:

  - MODE WATER
  - MODE CHARGE
  - RESET
  - STATUS
  - CAL
  - FLOWCAL

=====================================================================
WIRING SUMMARY:
=====================================================================
Arduino Pin  →  Component              →  Notes
---------------------------------------------------------------------
D2           →  Coin Acceptor Signal   →  5V logic pulse (interrupt)
D3           →  Flow Sensor (YF-S201)  →  5V pulse output (interrupt)
D7           →  Solenoid Valve Relay   →  Active HIGH
D8           →  Pump Relay             →  Active HIGH
D9           →  Ultrasonic Trigger     →  HC-SR04 TRIG
D10          →  Ultrasonic Echo        →  HC-SR04 ECHO
GND          →  Common Ground with Pi  →  Required for serial logic
VIN (5V)     →  Relay module VCC       →  Shared with Pi 5V or external

=====================================================================
 Author: VoltageV (Cedrick L.)
 Collaborators: R4A_EUC / Smart Kiosk Group 2
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

// Serial change detection
int last_creditML = -1;
bool last_dispensing = false;
unsigned long last_flowCount = 0;

// ---------------- INTERRUPTS ----------------
void coinISR() {
  unsigned long now = millis();
  if (now - lastCoinPulseTime > COIN_DEBOUNCE_MS) {
    coinPulseCount++;
    lastCoinPulseTime = now;
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

  EEPROM.get(0, coin1P_pulses);
  EEPROM.get(4, coin5P_pulses);
  EEPROM.get(8, coin10P_pulses);
  EEPROM.get(12, pulsesPerLiter);

  if (isnan(pulsesPerLiter) || pulsesPerLiter < 200 || pulsesPerLiter > 1000)
    pulsesPerLiter = 450.0;

  // Initialize cup detection variables
  cupRemovedFlag = false;
  cupRemovedTime = 0;

  Serial.println("System Ready. Waiting for Pi signal...");
  lastActivity = millis();
}

// ---------------- LOOP ----------------
void loop() {
  handleSerialCommand();
  handleCoin();
  handleCup();
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
  
  // DEBUG: Print raw sensor reading
  Serial.print("[DEBUG] Ultrasonic duration: ");
  Serial.println(duration);
  
  if (duration == 0) {
    // Timeout - no echo received
    Serial.println("[DEBUG] No echo received - sensor issue");
    return false;
  }
  
  float distance = duration * 0.034 / 2;
  
  // DEBUG: Print calculated distance
  Serial.print("[DEBUG] Calculated distance: ");
  Serial.print(distance);
  Serial.println(" cm");
  
  return (distance > 0 && distance < CUP_DISTANCE_CM);
}

void handleCup() {
  bool cupDetected = detectCup();
  
  // DEBUG: Print cup status for monitoring
  Serial.print("[DEBUG] Cup detected: ");
  Serial.println(cupDetected ? "YES" : "NO");
  Serial.print("[DEBUG] Credit ML: ");
  Serial.println(creditML);
  Serial.print("[DEBUG] Dispensing: ");
  Serial.println(dispensing ? "YES" : "NO");
  Serial.print("[DEBUG] Cup removed flag: ");
  Serial.println(cupRemovedFlag ? "YES" : "NO");
  
  if (cupDetected && creditML > 0 && !dispensing) {
    // Cup placed with credit - start dispensing
    Serial.println("CUP_DETECTED");
    cupRemovedFlag = false;  // Reset the flag
    startDispense(creditML);
  } 
  else if (!cupDetected && dispensing) {
    // Cup removed during dispensing
    if (!cupRemovedFlag) {
      // First time detecting cup removal - start grace period
      cupRemovedFlag = true;
      cupRemovedTime = millis();
      Serial.println("CUP_REMOVED - Grace period started (3 seconds)");
    } else {
      // Cup already removed, check if grace period expired
      unsigned long timeSinceRemoval = millis() - cupRemovedTime;
      Serial.print("[DEBUG] Time since cup removal: ");
      Serial.print(timeSinceRemoval);
      Serial.println(" ms");
      
      if (timeSinceRemoval > CUP_REMOVED_GRACE_MS) {
        Serial.println("CUP_REMOVED - Grace period expired, stopping dispensing");
        stopDispenseEarly();
        cupRemovedFlag = false;
      }
    }
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
  Serial.print("[DEBUG] Target pulses: ");
  Serial.println(targetPulses);
  Serial.print("[DEBUG] Starting flow count: ");
  Serial.println(startFlowCount);
}

void handleDispensing() {
  if (!dispensing) return;

  // Check if cup has been removed for too long
  if (cupRemovedFlag && (millis() - cupRemovedTime > CUP_REMOVED_GRACE_MS)) {
    Serial.println("[DEBUG] Cup removal grace period expired in handleDispensing");
    stopDispenseEarly();
    return;
  }

  unsigned long dispensedPulses = flowPulseCount - startFlowCount;
  float dispensedML = pulsesToML(dispensedPulses);
  float remainingML = creditML - dispensedML;

  // Send progress updates more frequently for better monitoring
  if (dispensedPulses % 30 == 0) {  // More frequent updates
    Serial.print("DISPENSE_PROGRESS ml=");
    Serial.print(dispensedML, 1);
    Serial.print(" remaining=");
    Serial.println(remainingML, 1);
    
    // Also show pulse info for debugging
    Serial.print("[DEBUG] Pulses: ");
    Serial.print(dispensedPulses);
    Serial.print("/");
    Serial.println(targetPulses);
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
  Serial.print("[DEBUG] Actual dispensed: ");
  Serial.print(dispensedML);
  Serial.println(" mL");

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
  Serial.print("[DEBUG] Dispensed so far: ");
  Serial.print(dispensedML);
  Serial.print(" mL, Remaining: ");
  Serial.print(remaining);
  Serial.println(" mL");

  creditML = remaining;  // Save remaining credit for next time
  lastActivity = millis();
}

// ---------------- COIN HANDLER ----------------
void handleCoin() {
  if (coinPulseCount > 0 && (millis() - lastCoinPulseTime > COIN_TIMEOUT_MS)) {
    int pulses = coinPulseCount;
    
    // STRICTER VALIDATION - only accept known coin patterns
    int peso = 0;
    int ml = 0;
    bool validCoin = false;

    // Tighter validation with smaller tolerance
    if (pulses >= coin1P_pulses-1 && pulses <= coin1P_pulses+1) { 
      peso = 1; ml = 50; validCoin = true; 
    }
    else if (pulses >= coin5P_pulses-1 && pulses <= coin5P_pulses+1) { 
      peso = 5; ml = 250; validCoin = true; 
    }
    else if (pulses >= coin10P_pulses-1 && pulses <= coin10P_pulses+1) { 
      peso = 10; ml = 500; validCoin = true; 
    }

    // ONLY process valid coins
    if (validCoin) {
      coinPulseCount = 0;  // Reset only for valid coins
      
      Serial.print("COIN_INSERTED "); 
      Serial.println(peso);

      if (currentMode == WATER_MODE) {
        creditML += ml;
        Serial.print("COIN_WATER "); 
        Serial.println(ml);
      } 
      else if (currentMode == CHARGE_MODE) {
        Serial.print("COIN_CHARGE "); 
        Serial.println(peso);
      }
      lastActivity = millis();
    } else {
      // REJECT invalid coins completely
      coinPulseCount = 0;  // Reset to prevent accumulation
      Serial.print("[DEBUG] Rejected invalid coin pattern: ");
      Serial.println(pulses);
    }
  }
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
  else if (cmd.equalsIgnoreCase("STATUS")) {
    Serial.print("CREDIT_ML "); Serial.println(creditML);
    Serial.print("DISPENSING "); Serial.println(dispensing ? "YES" : "NO");
    Serial.print("FLOW_PULSES "); Serial.println(flowPulseCount);
    Serial.print("CUP_REMOVED_FLAG "); Serial.println(cupRemovedFlag ? "YES" : "NO");
    if (cupRemovedFlag) {
      Serial.print("TIME_SINCE_REMOVAL "); 
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
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  Serial.println("System reset.");
  lastActivity = millis();
}