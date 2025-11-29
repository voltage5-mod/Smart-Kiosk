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

        static unsigned long lastDispensingUpdate = 0;
  if (dispensing && (millis() - lastDispensingUpdate < 500)) {
    // Skip frequent updates during dispensing
    return;
  }

    Serial.print("CREDIT_ML:"); Serial.println(creditML);
    Serial.print("DISPENSING: "); Serial.println(dispensing ? "YES" : "NO");
    Serial.print("FLOW_PULSES: "); Serial.println(flowPulseCount);
    Serial.print("DISPENSED_ML:"); Serial.println(pulsesToML(flowPulseCount - startFlowCount));

    last_creditML = creditML;
    last_dispensing = dispensing;
    last_flowCount = flowPulseCount;

      if (dispensing) {
    lastDispensingUpdate = millis();
    }
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
   unsigned long currentTargetPulses = (unsigned long)((creditML / 1000.0) * pulsesPerLiter);

  // Only stop when we've reached the target OR credit is zero
   if (dispensedPulses >= currentTargetPulses * 0.95 || creditML <= 0) {
    stopDispense();
  }
  // Continue dispensing as long as we have credit and haven't reached target
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

  if (dispensing) return;
        
  if (coinPulseCount == 0) return;
  if (millis() - lastCoinPulseTime <= COIN_TIMEOUT_MS) return;

  int pulses = coinPulseCount;
  coinPulseCount = 0;

  if (abs(pulses - coin1P_pulses) <= 1) creditML += creditML_1P;
  else if (abs(pulses - coin5P_pulses) <= 1) creditML += creditML_5P;
  else if (abs(pulses - coin10P_pulses) <= 1) creditML += creditML_10P;
  else {
    Serial.print("Unknown coin pattern: ");
    Serial.println(pulses);
    return;
  }

  Serial.print("Coin accepted: pulses=");
  Serial.println(pulses);
  lastActivity = millis();
}

// ---------------- SERIAL COMMAND HANDLER ----------------
void handleSerialCommand() {
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();

  if (cmd.equalsIgnoreCase("RESET"))
    resetSystem();
}

// ---------------- RESET ----------------
void resetSystem() {
  creditML = 0;
  dispensing = false;
  countdownActive = false;
  cupDetected = false;

  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);

  Serial.println("System reset.");
  lastActivity = millis();
}