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

// Button-style cup detection variables
unsigned long cupLastStateChange = 0;
bool cupStableState = false;
const unsigned long CUP_STABLE_MS = 200;
bool cupLatched = false;
const unsigned long CUP_RETURN_TIMEOUT_MS = 10000;
unsigned long cupRemovedAt = 0;
bool pauseOnRemove = true;

// Countdown variables - ADDED
bool countdownActive = false;
unsigned long countdownStartTime = 0;
const unsigned long COUNTDOWN_DURATION_MS = 5000; // 5 seconds
int currentCountdown = 5;

// ---------------- FLOW CALIBRATION ----------------
float pulsesPerLiter = 450.0;

// ---------------- COIN CREDIT SETTINGS ----------------
int coin1P_pulses = 1;
int coin5P_pulses = 3;
int coin10P_pulses = 5;

int creditML_1P  = 1000;
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

  if (isnan(pulsesPerLiter) || pulsesPerLiter < 200 || pulsesPerLiter > 5000)
    pulsesPerLiter = 450.0;

  Serial.println("System Ready (Cup-as-Button + 5s Countdown).");
  lastActivity = millis();
}

// ---------------- LOOP ----------------
void loop() {
  handleCoin();
  handleCup();
  handleCountdown(); // ADDED
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
float readDistance() { // CHANGED: Now returns distance instead of boolean
  digitalWrite(CUP_TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(CUP_TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(CUP_TRIG_PIN, LOW);

  long duration = pulseIn(CUP_ECHO_PIN, HIGH, 30000);
  if (duration == 0) {
    return -1.0; // No reading
  }
  
  float distance = duration * 0.034 / 2;
  
  // DEBUG: Send distance reading to Python
  Serial.print("DEBUG distance: ");
  Serial.print(distance);
  Serial.println(" cm");
  
  return distance;
}

bool rawCupReading() {
  float distance = readDistance();
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

// ---------------- COUNTDOWN HANDLER - ADDED ----------------
void handleCountdown() {
  if (!countdownActive) return;

  unsigned long elapsed = millis() - countdownStartTime;
  int secondsRemaining = 5 - (elapsed / 1000);

  // Update countdown display
  if (secondsRemaining != currentCountdown && secondsRemaining >= 0) {
    currentCountdown = secondsRemaining;
    if (secondsRemaining > 0) {
      Serial.print("COUNTDOWN ");
      Serial.println(secondsRemaining);
    } else {
      Serial.println("COUNTDOWN_END");
      // Start dispensing ALL water after countdown
      startDispense(creditML);
      countdownActive = false;
      cupLatched = true; // Set latched when countdown completes
    }
  }

  // Check if cup was removed during countdown
  float distance = readDistance();
  if (!(distance > 0 && distance < CUP_DETECT_THRESHOLD_CM)) {
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

  int peso = 0;
  int ml = 0;
  
  if (abs(pulses - coin1P_pulses) <= 1) {
    peso = 1; 
    ml = creditML_1P;
  }
  else if (abs(pulses - coin5P_pulses) <= 1) {
    peso = 5; 
    ml = creditML_5P;
  }
  else if (abs(pulses - coin10P_pulses) <= 1) {
    peso = 10; 
    ml = creditML_10P;
  }
  else {
    Serial.print("Unknown or noise coin pattern: ");
    Serial.println(pulses);
    return;
  }

  // Send coin events for Python
  Serial.print("COIN_INSERTED ");
  Serial.println(peso);
  
  creditML += ml;
  Serial.print("COIN_WATER ");
  Serial.println(ml);

  Serial.print("Coin accepted: pulses=");
  Serial.println(pulses);
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
        Serial.println("CUP_REMOVED");
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
    currentCountdown = -1; // Reset countdown tracking
    
    Serial.println("CUP_DETECTED");
    Serial.println("COUNTDOWN_START 5");
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

void calibrateFlow() {
  Serial.println("FLOW CALIBRATION MODE ACTIVE");
  Serial.println("Collect exactly 1000 mL and type DONE.");
  Serial.println("All other system functions paused.");

  // Stop all vending actions
  dispensing = false;
  cupLatched = false;
  countdownActive = false;
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  delay(300);

  // Reset pulse counter
  noInterrupts();
  flowPulseCount = 0;
  interrupts();

  // Start pump and valve
  Serial.println("Pump ON...");
  digitalWrite(PUMP_PIN, HIGH);
  digitalWrite(VALVE_PIN, HIGH);

  // FULL BLOCKING LOOP (same behavior as old code)
  while (true) {
    if (Serial.available()) {
      String cmd = Serial.readStringUntil('\n');
      cmd.trim();
      if (cmd.equalsIgnoreCase("DONE")) break;
    }
  }

  // Stop pump
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  Serial.println("Pump OFF.");

  // Read final pulses
  noInterrupts();
  pulsesPerLiter = flowPulseCount;
  interrupts();

  // Save calibration
  EEPROM.put(12, pulsesPerLiter);

  Serial.print("New calibration saved: ");
  Serial.print(pulsesPerLiter);
  Serial.println(" pulses per liter");
  Serial.println("Calibration complete. Reboot recommended.");
}

// ---------------- SERIAL COMMAND HANDLER ----------------
void handleSerialCommand() {
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();

  if (cmd.equalsIgnoreCase("STATUS")) {
    Serial.print("Flow pulses: "); Serial.println(flowPulseCount);
    Serial.print("Flow mL: "); Serial.println(pulsesToML(flowPulseCount));
    Serial.print("Flow calibration: "); Serial.println(pulsesPerLiter);
    Serial.print("Cup detected: "); Serial.println(detectCupStable() ? "YES" : "NO");
    Serial.print("Countdown active: "); Serial.println(countdownActive ? "YES" : "NO");
  }
  else if (cmd.equalsIgnoreCase("FLOWCAL")) {
    calibrateFlow();
  }
  else if (cmd.equalsIgnoreCase("DEBUG")) {
    // Force distance reading
    float distance = readDistance();
    Serial.print("DEBUG: Distance = ");
    Serial.print(distance);
    Serial.println(" cm");
  }
  else if (cmd.equalsIgnoreCase("MODE WATER")) {
    Serial.println("MODE: WATER");
  }
  else if (cmd.equalsIgnoreCase("MODE CHARGE")) {
    Serial.println("MODE: CHARGE");
  }
  else if (cmd.equalsIgnoreCase("RESET")) resetSystem();
}

// ---------------- RESET ----------------
void resetSystem() {
  creditML = 0;
  dispensing = false;
  cupLatched = false;
  cupRemovedAt = 0;
  countdownActive = false;

  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);

  Serial.println("System reset.");
  lastActivity = millis();
}