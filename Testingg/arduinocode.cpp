#include <EEPROM.h>

// ---------------- PIN DEFINITIONS ----------------
#define COIN_PIN          2     // Coin slot signal pin (interrupt)
#define FLOW_SENSOR_PIN   3     // YF-S201 flow sensor (interrupt)
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

// Cup detection
#define CUP_DETECT_THRESHOLD_CM 20.0

// Button-style cup detection variables
unsigned long cupLastStateChange = 0;
bool cupStableState = false;
const unsigned long CUP_STABLE_MS = 200;
bool cupLatched = false;
const unsigned long CUP_RETURN_TIMEOUT_MS = 10000;
unsigned long cupRemovedAt = 0;
bool pauseOnRemove = true;

// Countdown variables
bool countdownActive = false;
unsigned long countdownStartTime = 0;
const unsigned long COUNTDOWN_DURATION_MS = 5000; // 5 seconds
int lastCountdownSecond = -1;

// ---------------- FLOW CALIBRATION ----------------
float pulsesPerLiter = 450.0;

// ---------------- COIN CREDIT SETTINGS ----------------
int coin1P_pulses = 1;
int coin5P_pulses = 3;
int coin10P_pulses = 5;

int creditML_1P  = 100;
int creditML_5P  = 250;
int creditML_10P = 500;

// ---------------- VOLATILES ----------------
volatile unsigned long lastCoinPulseTime = 0;
volatile int coinPulseCount = 0;
volatile unsigned long flowPulseCount = 0;

// ---------------- SYSTEM STATE ----------------
bool dispensing = false;
int creditML = 0;
unsigned long targetPulses = 0;
unsigned long startFlowCount = 0;
unsigned long lastActivity = 0;

// Serial change detection
int last_creditML = -1;
bool last_dispensing = false;
unsigned long last_flowCount = 0;

// ---------------- INTERRUPTS ----------------
void coinISR() {
  unsigned long now = millis();

  if (now - lastCoinPulseTime < COIN_ISR_RATE_LIMIT) return;
  if (now - lastCoinPulseTime < COIN_MIN_PULSE_SPACING) return;

  if (now - lastCoinPulseTime > COIN_DEBOUNCE_MS) {
    coinPulseCount++;
  }

  lastCoinPulseTime = now;
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

  Serial.println("System Ready (Cup-as-Button + 5s Countdown Mode).");
  lastActivity = millis();
}

// ---------------- LOOP ----------------
void loop() {
  handleCoin();
  handleCup();
  handleCountdown();
  handleDispensing();

  if (millis() - lastActivity > INACTIVITY_TIMEOUT && !dispensing) {
    resetSystem();
  }

  if (Serial.available()) handleSerialCommand();

  if (creditML != last_creditML || dispensing != last_dispensing || flowPulseCount != last_flowCount) {
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

// --------------- STABLE CUP DETECTOR ----------------
bool detectCupStable() {
  bool raw = rawCupReading();

  if (raw != cupStableState) {
    if (millis() - cupLastStateChange >= CUP_STABLE_MS) {
      cupStableState = raw;
      cupLastStateChange = millis();
    }
  } else {
    cupLastStateChange = millis();
  }

  return cupStableState;
}

// ---------------- COUNTDOWN HANDLER ----------------
void handleCountdown() {
  if (!countdownActive) return;

  unsigned long elapsed = millis() - countdownStartTime;
  int secondsRemaining = COUNTDOWN_DURATION_MS / 1000 - (elapsed / 1000);

  // Only send countdown updates when the second changes
  if (secondsRemaining != lastCountdownSecond) {
    lastCountdownSecond = secondsRemaining;
    
    if (secondsRemaining > 0) {
      Serial.print("COUNTDOWN ");
      Serial.println(secondsRemaining);
    } else if (secondsRemaining == 0) {
      Serial.println("COUNTDOWN_END");
      // Start dispensing after countdown completes
      startDispense(creditML);
      countdownActive = false;
    }
  }

  // Check if cup was removed during countdown
  bool cupPresent = detectCupStable();
  if (!cupPresent) {
    Serial.println("Cup removed during countdown - CANCELLED");
    countdownActive = false;
    cupLatched = false;
  }
}

// ---------------- COIN HANDLER ----------------
void handleCoin() {
  if (coinPulseCount == 0) return;
  if (millis() - lastCoinPulseTime <= COIN_TIMEOUT_MS) return;

  int pulses = coinPulseCount;
  coinPulseCount = 0;

  if (pulses < 1 || pulses > 10) {
    Serial.print("Rejected noise pulses: ");
    Serial.println(pulses);
    return;
  }

  int addedCredit = 0;
  if (abs(pulses - coin1P_pulses) <= 1) {
    creditML += creditML_1P;
    addedCredit = creditML_1P;
    Serial.print("COIN_INSERTED 1");
  }
  else if (abs(pulses - coin5P_pulses) <= 1) {
    creditML += creditML_5P;
    addedCredit = creditML_5P;
    Serial.print("COIN_INSERTED 5");
  }
  else if (abs(pulses - coin10P_pulses) <= 1) {
    creditML += creditML_10P;
    addedCredit = creditML_10P;
    Serial.print("COIN_INSERTED 10");
  }
  else {
    Serial.print("Unknown or noise coin pattern: ");
    Serial.println(pulses);
    return;
  }

  Serial.print(" - Credit added: ");
  Serial.print(addedCredit);
  Serial.print("mL - Total: ");
  Serial.print(creditML);
  Serial.println("mL");
  
  lastActivity = millis();
}

// ---------------- CUP HANDLER (Button Mode with Countdown) ----------------
void handleCup() {
  bool cupPresent = detectCupStable();

  // If countdown is active, let handleCountdown manage it
  if (countdownActive) {
    return;
  }

  // Already dispensing → manage pause/resume
  if (cupLatched && dispensing) {
    if (!cupPresent) {
      if (cupRemovedAt == 0) cupRemovedAt = millis();

      if (pauseOnRemove) {
        digitalWrite(PUMP_PIN, LOW);
        digitalWrite(VALVE_PIN, LOW);
        Serial.println("Cup removed — PAUSED");
      }
    } else {
      if (pauseOnRemove && cupRemovedAt != 0) {
        digitalWrite(PUMP_PIN, HIGH);
        digitalWrite(VALVE_PIN, HIGH);
        Serial.println("Cup returned — RESUMING");
        cupRemovedAt = 0;
        lastActivity = millis();
      }
    }

    if (cupRemovedAt != 0 && millis() - cupRemovedAt > CUP_RETURN_TIMEOUT_MS) {
      Serial.println("Cup did not return — stopping + refunding leftover credit");
      stopDispenseAndRefund();
    }
    return;
  }

  // TRIGGER COUNTDOWN WHEN CUP PLACED (NEW BEHAVIOR)
  if (!dispensing && !cupLatched && !countdownActive && cupPresent && creditML > 0) {
    cupLatched = true;
    countdownActive = true;
    countdownStartTime = millis();
    lastCountdownSecond = -1; // Reset countdown tracking
    
    Serial.println("CUP_DETECTED");
    Serial.println("COUNTDOWN_START 5");
    Serial.println("Starting 5-second countdown...");
  }

  // Reset latch when idle and no credit
  if (!dispensing && cupLatched && creditML == 0 && !cupPresent) {
    cupLatched = false;
    cupRemovedAt = 0;
  }
}

// ---------------- DISPENSING ENGINE ----------------
void startDispense(int ml) {
  if (ml <= 0) return;
  
  startFlowCount = flowPulseCount;
  targetPulses = (unsigned long)((ml / 1000.0) * pulsesPerLiter);

  digitalWrite(PUMP_PIN, HIGH);
  digitalWrite(VALVE_PIN, HIGH);
  dispensing = true;

  Serial.println("DISPENSE_START");
  
  // Send initial progress with ALL remaining water
  Serial.print("DISPENSE_PROGRESS ml=0 remaining=");
  Serial.println(ml);
  
  lastActivity = millis();
}

void handleDispensing() {
  if (!dispensing) return;

  unsigned long dispensedPulses = flowPulseCount - startFlowCount;
  float dispensedML = pulsesToML(dispensedPulses);
  float remainingML = creditML - dispensedML;

  // Send progress updates frequently
  if (dispensedPulses % 50 == 0) {
    Serial.print("DISPENSE_PROGRESS ml=");
    Serial.print(dispensedML, 1);
    Serial.print(" remaining=");
    Serial.println(remainingML, 1);
  }

  if (dispensedPulses >= targetPulses || remainingML <= 0) {
    stopDispense();
  }
}

void stopDispense() {
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  dispensing = false;

  float dispensedML = pulsesToML(flowPulseCount - startFlowCount);
  Serial.print("DISPENSE_DONE ");
  Serial.println(dispensedML, 1);

  creditML = 0; // All water dispensed
  cupLatched = false;
  countdownActive = false;
  lastActivity = millis();
}

// ---------------- REFUND LOGIC ----------------
void stopDispenseAndRefund() {
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);

  unsigned long pulsesDispensed = flowPulseCount - startFlowCount;
  int mlDispensed = (int)pulsesToML(pulsesDispensed + 0.5);

  int remaining = creditML - mlDispensed;
  if (remaining < 0) remaining = 0;

  Serial.print("Dispensed so far: ");
  Serial.println(mlDispensed);
  Serial.print("Refunding remaining credit: ");
  Serial.println(remaining);

  creditML = remaining;
  dispensing = false;
  cupLatched = false;
  cupRemovedAt = 0;
  countdownActive = false;

  Serial.print("CREDIT_LEFT ");
  Serial.println(creditML);
}

// ---------------- SERIAL COMMAND HANDLER ----------------
void handleSerialCommand() {
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();

  if (cmd.equalsIgnoreCase("STATUS")) {
    Serial.print("CREDIT_ML: "); Serial.println(creditML);
    Serial.print("DISPENSING: "); Serial.println(dispensing ? "YES" : "NO");
    Serial.print("COUNTDOWN_ACTIVE: "); Serial.println(countdownActive ? "YES" : "NO");
    Serial.print("CUP_LATCHED: "); Serial.println(cupLatched ? "YES" : "NO");
    Serial.print("Flow pulses: "); Serial.println(flowPulseCount);
    Serial.print("Flow mL: "); Serial.println(pulsesToML(flowPulseCount));
    Serial.print("Flow calibration: "); Serial.println(pulsesPerLiter);
  }
  else if (cmd.equalsIgnoreCase("RESET")) {
    resetSystem();
  }
  else if (cmd.equalsIgnoreCase("MODE WATER")) {
    Serial.println("MODE: WATER");
  }
  else if (cmd.equalsIgnoreCase("MODE CHARGE")) {
    Serial.println("MODE: CHARGE");
  }
}

// ---------------- RESET ----------------
void resetSystem() {
  creditML = 0;
  dispensing = false;
  countdownActive = false;
  cupLatched = false;
  cupRemovedAt = 0;

  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);

  Serial.println("System reset.");
  lastActivity = millis();
}