// arduinocode.cpp
// Updated Arduino sketch: one-shot cup detection -> 3s countdown -> dispense ALL credit
// Sensor only active when creditML > 0. Cup triggers only once per paid credit.
// Emits serial events for UI: CUP_DETECTED, COUNTDOWN n, COUNTDOWN_END, DISPENSE_START, DISPENSE_DONE, CREDIT_ML

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

// Cup detection (improved)
#define CUP_DETECT_THRESHOLD_CM 20.0

// Cup trigger delay (3 seconds)
const unsigned long CUP_DELAY_MS = 3000UL;

// Button-style cup detection variables (stable detector)
unsigned long cupLastStateChange = 0;
bool cupStableState = false;
const unsigned long CUP_STABLE_MS = 200;

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

// ---------------- CUP TRIGGER / COUNTDOWN STATE ----------------
// One-shot trigger: sensor only used to detect single movement while credit > 0
bool cupTriggerFired = false;     // true once a cup trigger has been detected for current credit
unsigned long cupDetectionTime = 0;
int currentCountdown = -1;        // tracks what countdown tick we last emitted (-1 = none)
// sensorDisabled is true while waiting/dispensing after trigger (prevents retrigger)
bool sensorDisabled = false;

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

  if (isnan(pulsesPerLiter) || pulsesPerLiter < 200 || pulsesPerLiter > 5000)
    pulsesPerLiter = 450.0;

  Serial.println("System Ready (One-shot Cup Trigger + 3s Delay).");
  lastActivity = millis();
}

// ---------------- LOOP ----------------
void loop() {
  handleCoin();
  handleCup();         // non-blocking, handles detection + countdown + start
  handleDispensing();

  if (millis() - lastActivity > INACTIVITY_TIMEOUT && !dispensing) {
    resetSystem();
  }

  if (Serial.available()) handleSerialCommand();

  // Periodically publish summary variables for UI/upstream
  if (creditML != last_creditML || dispensing != last_dispensing || flowPulseCount != last_flowCount) {
    Serial.print("CREDIT_ML: "); Serial.println(creditML);
    Serial.print("DISPENSING: "); Serial.println(dispensing ? "YES" : "NO");
    Serial.print("FLOW_PULSES: "); Serial.println(flowPulseCount);
    Serial.print("DISPENSED_ML: "); Serial.println((float)pulsesToML(flowPulseCount - startFlowCount), 1);
    last_creditML = creditML;
    last_dispensing = dispensing;
    last_flowCount = flowPulseCount;
  }

  delay(80);
}

// ---------------- HELPERS ----------------
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
  float distance = (duration * 0.034) / 2.0;

  // consider only plausible distances (filter obvious noise)
  if (duration == 0) return false;
  if (distance <= 0.0 || distance > 1000.0) return false;

  return (distance > 0 && distance < CUP_DETECT_THRESHOLD_CM);
}

// --------------- STABLE CUP DETECTOR ----------------
bool detectCupStable() {
  bool raw = rawCupReading();

  if (raw != cupStableState) {
    // only change state after it's stable for CUP_STABLE_MS
    if (millis() - cupLastStateChange >= CUP_STABLE_MS) {
      cupStableState = raw;
      cupLastStateChange = millis();
    }
  } else {
    // reset the state change timer when state remains the same
    cupLastStateChange = millis();
  }

  return cupStableState;
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

  if (abs(pulses - coin1P_pulses) <= 1) creditML += creditML_1P;
  else if (abs(pulses - coin5P_pulses) <= 1) creditML += creditML_5P;
  else if (abs(pulses - coin10P_pulses) <= 1) creditML += creditML_10P;
  else {
    Serial.print("Unknown or noise coin pattern: ");
    Serial.println(pulses);
    return;
  }

  Serial.print("Coin accepted: pulses=");
  Serial.println(pulses);
  lastActivity = millis();

  // make sure detection is enabled for the new credit
  if (creditML > 0) {
    sensorDisabled = false;
    cupTriggerFired = false;
    currentCountdown = -1;
  }
}

// ---------------- CUP HANDLER (one-shot + countdown) ----------------
void handleCup() {
  // Sensor does nothing unless there is credit and sensor is not disabled
  if (creditML <= 0) return;
  if (sensorDisabled) return;

  // If a trigger already fired, manage countdown/timer
  if (cupTriggerFired) {
    unsigned long elapsed = millis() - cupDetectionTime;

    // compute seconds remaining: 3..1..0
    int secondsRemaining = (CUP_DELAY_MS - elapsed + 999) / 1000; // ceiling division
    if (secondsRemaining < 0) secondsRemaining = 0;

    // Emit COUNTDOWN ticks only when changed
    if (secondsRemaining != currentCountdown && secondsRemaining > 0) {
      currentCountdown = secondsRemaining;
      Serial.print("COUNTDOWN ");
      Serial.println(currentCountdown);
    }

    // when countdown finishes, emit COUNTDOWN_END and start dispensing
    if (elapsed >= CUP_DELAY_MS && !dispensing) {
      Serial.println("COUNTDOWN_END");
      // disable sensor now (prevent re-trigger while dispensing)
      sensorDisabled = true;
      startDispense(creditML); // starts dispensing ALL credit
    }

    return;
  }

  // No trigger fired yet: detect a single movement (stable read becomes true)
  bool cupPresent = detectCupStable();

  if (cupPresent && !cupTriggerFired && creditML > 0) {
    // One-shot: register trigger, start countdown timer
    cupTriggerFired = true;
    cupDetectionTime = millis();
    currentCountdown = 3;
    Serial.println("CUP_DETECTED");
    Serial.print("COUNTDOWN ");
    Serial.println(currentCountdown); // emit initial tick (3)
    // note: subsequent ticks emitted in the section above as secondsRemaining changes
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
  lastActivity = millis();
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

  float dispensedML = pulsesToML(flowPulseCount - startFlowCount);

  Serial.print("DISPENSE_DONE ");
  Serial.println(dispensedML, 1);

  // Clear credit and reset trigger/sensor so next customer can use it
  creditML = 0;
  Serial.print("CREDIT_ML: ");
  Serial.println(creditML);

  cupTriggerFired = false;
  sensorDisabled = false;   // allow sensor for next transaction (only effective when creditML > 0)
  currentCountdown = -1;

  lastActivity = millis();
}

// ---------------- SERIAL COMMAND HANDLER ----------------
void handleSerialCommand() {
  if (!Serial.available()) return;
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();

  if (cmd.equalsIgnoreCase("STATUS")) {
    Serial.print("Flow pulses: "); Serial.println(flowPulseCount);
    Serial.print("Flow mL: "); Serial.println(pulsesToML(flowPulseCount));
    Serial.print("Flow calibration: "); Serial.println(pulsesPerLiter);
    Serial.print("Credit mL: "); Serial.println(creditML);
    Serial.print("Dispensing: "); Serial.println(dispensing ? "YES" : "NO");
  }
  else if (cmd.equalsIgnoreCase("FLOWCAL")) {
    calibrateFlow();
  }
  else if (cmd.equalsIgnoreCase("RESET")) resetSystem();
}

// ---------------- FLOW CALIBRATION ----------------
void calibrateFlow() {
  Serial.println("=== FLOW CALIBRATION MODE ===");
  Serial.println("Collect EXACTLY 1000 mL then type DONE.");
  Serial.println("Normal system functions PAUSED.");

  // ----------- STOP EVERYTHING -----------
  dispensing = false;
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  cupTriggerFired = false;
  sensorDisabled = false;

  delay(300);

  // ----------- RESET PULSES -----------
  noInterrupts();          // prevent interrupts from triggering early
  flowPulseCount = 0;
  interrupts();            // re-enable interrupts for accurate counting

  // ----------- START WATER FLOW -----------
  digitalWrite(PUMP_PIN, HIGH);
  digitalWrite(VALVE_PIN, HIGH);
  Serial.println("Pump ON... calibrating.");

  // ----------- WAIT FOR USER INPUT "DONE" -----------
  while (true) {
    if (Serial.available()) {
      String cmd = Serial.readStringUntil('\n');
      cmd.trim();
      if (cmd.equalsIgnoreCase("DONE")) break;
    }
  }

  // ----------- STOP WATER -----------
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  Serial.println("Pump OFF.");

  // ----------- SAVE CALIBRATION -----------
  noInterrupts(); // freeze pulses while saving
  pulsesPerLiter = flowPulseCount;
  interrupts();

  EEPROM.put(12, pulsesPerLiter);

  Serial.print("NEW CALIBRATION SAVED: ");
  Serial.print(pulsesPerLiter);
  Serial.println(" pulses per liter");
  Serial.println("=== CALIBRATION COMPLETE ===");

  delay(500);
}

// ---------------- RESET ----------------
void resetSystem() {
  creditML = 0;
  dispensing = false;

  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);

  cupTriggerFired = false;
  sensorDisabled = false;
  cupLastStateChange = 0;
  cupStableState = false;
  currentCountdown = -1;

  Serial.println("System reset.");
  lastActivity = millis();
}
