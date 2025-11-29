#include <EEPROM.h>

// ---------------- PIN DEFINITIONS ----------------
#define COIN_PIN          3     // Coin slot signal pin (interrupt)
#define FLOW_SENSOR_PIN   2     // YF-S201 flow sensor (interrupt)
#define CUP_TRIG_PIN      9     // Ultrasonic trigger
#define CUP_ECHO_PIN      10    // Ultrasonic echo
#define PUMP_PIN          8     // Pump relay
#define VALVE_PIN         7     // Solenoid valve relay

// ---------------- CONSTANTS ----------------
#define COIN_DEBOUNCE_MS       40
#define COIN_MIN_PULSE_SPACING 30
#define COIN_ISR_RATE_LIMIT    3
#define COIN_TIMEOUT_MS        700
#define INACTIVITY_TIMEOUT     300000 // 5 minutes
#define CUP_DETECT_THRESHOLD_CM 20.0
#define CUP_STABLE_MS 200

// Countdown settings
bool countdownActive = false;
unsigned long countdownStart = 0;
int countdownValue = 3;

// ---------------- FLOW CALIBRATION ----------------
float pulsesPerLiter = 450.0;

// ---------------- COIN CREDIT SETTINGS ----------------
int coin1P_pulses = 1;
int coin5P_pulses = 3;
int coin10P_pulses = 5;

int creditML_1P  = 50;   // ✔ 1 peso = 50 ml
int creditML_5P  = 250;  // ✔ 5 peso = 250 ml
int creditML_10P = 500;  // ✔ 10 peso = 500 ml

// ---------------- VOLATILES ----------------
volatile unsigned long lastCoinPulseTime = 0;
volatile int coinPulseCount = 0;
volatile unsigned long flowPulseCount = 0;

// ---------------- SYSTEM STATE ----------------
bool dispensing = false;
bool cupDetected = false;
int creditML = 0;

unsigned long targetPulses = 0;
unsigned long startFlowCount = 0;

unsigned long lastActivity = 0;

// Serial change detection
int last_creditML = -1;
bool last_dispensing = false;
unsigned long last_flowCount = 0;

// Coin validation state
unsigned long lastValidCoinTime = 0;
bool coinValidationActive = false;
unsigned long coinValidationStart = 0;
#define COIN_VALIDATION_TIMEOUT 1000 // 1 second to validate coin pattern

// ---------------- INTERRUPTS ----------------
void coinISR() {
  unsigned long now = millis();

  // Rate limiting - prevent multiple triggers in quick succession
  if (now - lastCoinPulseTime < COIN_ISR_RATE_LIMIT) return;
  
  // Minimum spacing between pulses
  if (now - lastCoinPulseTime < COIN_MIN_PULSE_SPACING) return;

  // Debounce check
  if (now - lastCoinPulseTime > COIN_DEBOUNCE_MS) {
    coinPulseCount++;
    lastCoinPulseTime = now;
    
    // Start coin validation timer on first pulse
    if (coinPulseCount == 1 && !coinValidationActive) {
      coinValidationActive = true;
      coinValidationStart = now;
    }
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

  EEPROM.get(12, pulsesPerLiter);
  if (isnan(pulsesPerLiter) || pulsesPerLiter < 200 || pulsesPerLiter > 5000)
    pulsesPerLiter = 450.0;

  Serial.println("System Ready — Cup Movement Trigger Mode Enabled.");
  lastActivity = millis();
}

// ---------------- LOOP ----------------
void loop() {
  handleCoin();
  handleCup();
  handleCountdown();
  handleDispensing();

  if (millis() - lastActivity > INACTIVITY_TIMEOUT && !dispensing)
    resetSystem();

  if (Serial.available())
    handleSerialCommand();

  // UI Updates
  if (creditML != last_creditML ||
      dispensing != last_dispensing ||
      flowPulseCount != last_flowCount) {

    Serial.print("CREDIT_ML: "); Serial.println(creditML);
    Serial.print("DISPENSING: "); Serial.println(dispensing ? "YES" : "NO");
    Serial.print("FLOW_PULSES: "); Serial.println(flowPulseCount);
    Serial.print("DISPENSED_ML: "); Serial.println(pulsesToML(flowPulseCount - startFlowCount));

    last_creditML = creditML;
    last_dispensing = dispensing;
    last_flowCount = flowPulseCount;
  }

  delay(80);
}

// ---------------- HELPER FUNCTIONS ----------------
float pulsesToML(unsigned long pulses) {
  return (pulses / pulsesPerLiter) * 1000.0;
}

// --------------- RAW CUP READING ----------------
bool rawCupReading() {
  digitalWrite(CUP_TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(CUP_TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(CUP_TRIG_PIN, LOW);

  long duration = pulseIn(CUP_ECHO_PIN, HIGH, 30000);
  float distance = duration * 0.034 / 2;

  return (distance > 0 && distance < CUP_DETECT_THRESHOLD_CM);
}

// ---------------- CUP HANDLER ----------------
void handleCup() {
  bool cupPresent = rawCupReading();

  if (!cupDetected && cupPresent && creditML > 0 && !dispensing && !countdownActive) {
    cupDetected = true;

    Serial.println("CUP_DETECTED");

    // Start 3 second countdown
    countdownActive = true;
    countdownStart = millis();
    countdownValue = 3;
    Serial.println("COUNTDOWN 3");
  }
}

// ---------------- COUNTDOWN HANDLER ----------------
void handleCountdown() {
  if (!countdownActive) return;

  unsigned long elapsed = millis() - countdownStart;

  if (elapsed >= (3000 - (countdownValue * 1000))) {
    if (countdownValue > 1) {
      countdownValue--;
      Serial.print("COUNTDOWN ");
      Serial.println(countdownValue);
    } else {
      Serial.println("COUNTDOWN_END");
      countdownActive = false;
      startDispense(creditML);
    }
  }
}

// ---------------- DISPENSING ENGINE ----------------
void startDispense(int ml) {
  startFlowCount = flowPulseCount;
  targetPulses = (unsigned long)((ml / 1000.0) * pulsesPerLiter);

  digitalWrite(PUMP_PIN, HIGH);
  digitalWrite(VALVE_PIN, HIGH);
  dispensing = true;

  Serial.println("DISPENSE_START");
  lastActivity = millis();
}

void handleDispensing() {
  if (!dispensing) return;

  unsigned long dispensedPulses = flowPulseCount - startFlowCount;

  if (dispensedPulses >= targetPulses)
    stopDispense();
}

void stopDispense() {
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  dispensing = false;

  float dispensedML = pulsesToML(flowPulseCount - startFlowCount);

  Serial.print("DISPENSE_DONE ");
  Serial.println(dispensedML);

  creditML = 0;
  cupDetected = false;
  lastActivity = millis();
}

// ---------------- COIN HANDLER ----------------
void handleCoin() {
  // Handle coin validation timeout
  if (coinValidationActive && (millis() - coinValidationStart > COIN_VALIDATION_TIMEOUT)) {
    // Coin validation period expired - process whatever pulses we have
    processCoinPulses();
    coinValidationActive = false;
    return;
  }
  
  // If we have pulses and sufficient time has passed since last pulse, process them
  if (coinPulseCount > 0 && (millis() - lastCoinPulseTime > COIN_TIMEOUT_MS)) {
    processCoinPulses();
  }
}

void processCoinPulses() {
  if (coinPulseCount == 0) return;

  int pulses = coinPulseCount;
  coinPulseCount = 0;
  coinValidationActive = false;

  // Enhanced coin validation with stricter matching
  int coinValue = 0;
  int addedML = 0;
  
  if (pulses == coin1P_pulses) {
    coinValue = 1;
    addedML = creditML_1P;
  } else if (pulses == coin5P_pulses) {
    coinValue = 5;
    addedML = creditML_5P;
  } else if (pulses == coin10P_pulses) {
    coinValue = 10;
    addedML = creditML_10P;
  } else {
    // Invalid coin pattern - log but don't add credit
    Serial.print("Unknown coin pattern: ");
    Serial.println(pulses);
    return;
  }

  // Valid coin detected
  creditML += addedML;
  lastValidCoinTime = millis();

  Serial.print("Coin accepted: pulses=");
  Serial.print(pulses);
  Serial.print(", value=P");
  Serial.print(coinValue);
  Serial.print(", added=");
  Serial.print(addedML);
  Serial.print("mL, total=");
  Serial.print(creditML);
  Serial.println("mL");

  lastActivity = millis();
}

// ---------------- SERIAL COMMAND HANDLER ----------------
void handleSerialCommand() {
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();

  if (cmd.equalsIgnoreCase("RESET"))
    resetSystem();
  else if (cmd.equalsIgnoreCase("STATUS"))
    printStatus();
  else if (cmd.startsWith("CALIBRATE")) {
    // Calibration command format: CALIBRATE 450.0
    int spaceIndex = cmd.indexOf(' ');
    if (spaceIndex > 0) {
      String valueStr = cmd.substring(spaceIndex + 1);
      float newCalibration = valueStr.toFloat();
      if (newCalibration >= 200 && newCalibration <= 5000) {
        pulsesPerLiter = newCalibration;
        EEPROM.put(12, pulsesPerLiter);
        Serial.print("Calibration updated: ");
        Serial.println(pulsesPerLiter);
      }
    }
  }
}

void printStatus() {
  Serial.println("=== SYSTEM STATUS ===");
  Serial.print("Credit: "); Serial.print(creditML); Serial.println(" mL");
  Serial.print("Dispensing: "); Serial.println(dispensing ? "YES" : "NO");
  Serial.print("Cup Detected: "); Serial.println(cupDetected ? "YES" : "NO");
  Serial.print("Countdown Active: "); Serial.println(countdownActive ? "YES" : "NO");
  Serial.print("Flow Pulses: "); Serial.println(flowPulseCount);
  Serial.print("Pulses/Liter: "); Serial.println(pulsesPerLiter);
  Serial.println("===================");
}

// ---------------- RESET ----------------
void resetSystem() {
  creditML = 0;
  dispensing = false;
  countdownActive = false;
  cupDetected = false;
  coinPulseCount = 0;
  coinValidationActive = false;

  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);

  Serial.println("System reset.");
  lastActivity = millis();
}