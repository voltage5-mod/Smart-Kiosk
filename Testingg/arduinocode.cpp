#include <EEPROM.h>

// ---------------- PIN DEFINITIONS ----------------
#define COIN_PIN          2     // Coin slot signal pin (interrupt)
#define FLOW_SENSOR_PIN   3     // YF-S201 flow sensor (interrupt)
#define CUP_TRIG_PIN      9     // Ultrasonic trigger
#define CUP_ECHO_PIN      10    // Ultrasonic echo
#define PUMP_PIN          8     // Pump relay
#define VALVE_PIN         7     // Solenoid valve relay

// ---------------- CONSTANTS ----------------
#define COIN_DEBOUNCE_MS  120
#define COIN_TIMEOUT_MS   800
#define INACTIVITY_TIMEOUT 300000 // 5 min
#define CUP_DISTANCE_CM   10.0
#define BAUDRATE          115200

// ---------------- FLOW CALIBRATION ----------------
// YF-S201 typical: ~450 pulses per liter (4.5 pulses per mL)
float pulsesPerLiter = 450.0;

// ---------------- COIN CREDIT SETTINGS ----------------
int coin1P_pulses = 1;
int coin5P_pulses = 3;
int coin10P_pulses = 5;

// (Water mode)
int creditML_1P = 100;
int creditML_5P = 500;
int creditML_10P = 1000;

// (Charging mode)
int creditMINS_5P = 10;
int creditMINS_10P = 20;

// ---------------- VOLATILES ----------------
volatile unsigned long lastCoinPulseTime = 0;
volatile int coinPulseCount = 0;
volatile unsigned long flowPulseCount = 0;

// ---------------- SYSTEM STATE ----------------
bool dispensing = false;
bool waterMode = false;    // false = Charging, true = Water
int creditML = 0;
unsigned long targetPulses = 0;
unsigned long startFlowCount = 0;
unsigned long lastActivity = 0;

// ---------------- SERIAL STATE TRACKING ----------------
int last_creditML = -1;
bool last_dispensing = false;
unsigned long last_flowCount = 0;

// ---------------- INTERRUPTS ----------------
bool firstPulseIgnored = false;  // add this global flag at top

void coinISR() {
  unsigned long now = millis();
  if (now - lastCoinPulseTime > COIN_DEBOUNCE_MS) {
    // Ignore the very first pulse after system starts
    if (!firstPulseIgnored) {
      firstPulseIgnored = true;
      lastCoinPulseTime = now;
      return;  // ignore this one
    }

    coinPulseCount++;
    lastCoinPulseTime = now;
  }
}


void flowISR() {
  flowPulseCount++;
}

// ---------------- SETUP ----------------
void setup() {
  Serial.begin(BAUDRATE);

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

  Serial.println("System Ready. Waiting for Pi signal...");
  lastActivity = millis();
}

// ---------------- LOOP ----------------
void loop() {
  handlePiCommands();
  handleCoin();
  if (waterMode) {
    handleCup();
    handleDispensing();
  }

  if (millis() - lastActivity > INACTIVITY_TIMEOUT && !dispensing) {
    resetSystem();
  }

  delay(100);
}

// ---------------- COIN HANDLER ----------------
void handleCoin() {
  if (coinPulseCount > 0 && (millis() - lastCoinPulseTime > COIN_TIMEOUT_MS)) {
    int pulses = coinPulseCount;
    coinPulseCount = 0;
    lastActivity = millis();

    if (waterMode) {
      // WATER MODE CREDIT
      if (abs(pulses - coin1P_pulses) <= 1) creditML += creditML_1P;
      else if (abs(pulses - coin5P_pulses) <= 1) creditML += creditML_5P;
      else if (abs(pulses - coin10P_pulses) <= 1) creditML += creditML_10P;
      else {
        Serial.print("UNKNOWN_COIN "); Serial.println(pulses);
        return;
      }
      Serial.print("COIN_WATER "); Serial.println(creditML);
    } else {
      // CHARGING MODE (just notify Pi)
      int peso = 0;
      if (abs(pulses - coin5P_pulses) <= 1) peso = 5;
      else if (abs(pulses - coin10P_pulses) <= 1) peso = 10;
      else if (abs(pulses - coin1P_pulses) <= 1) peso = 1;
      Serial.print("COIN_CHARGE "); Serial.println(peso);
    }
  }
}

// ---------------- CUP HANDLER ----------------
void handleCup() {
  if (detectCup() && creditML > 0 && !dispensing) {
    Serial.println("CUP_DETECTED");
    startDispense(creditML);
  } else if (!detectCup() && dispensing) {
    Serial.println("CUP_REMOVED");
    stopDispense();
    Serial.print("CREDIT_LEFT "); Serial.println(creditML);
  }
}

// ---------------- DISPENSING ----------------
void startDispense(int ml) {
  startFlowCount = flowPulseCount;
  targetPulses = (unsigned long)((ml / 1000.0) * pulsesPerLiter);
  digitalWrite(PUMP_PIN, HIGH);
  digitalWrite(VALVE_PIN, HIGH);
  dispensing = true;
  Serial.println("DISPENSE_START");
}

void handleDispensing() {
  if (!dispensing) return;

  unsigned long dispensedPulses = flowPulseCount - startFlowCount;
  float dispensedML = pulsesToML(dispensedPulses);
  if (dispensedPulses >= targetPulses) {
    stopDispense();
    Serial.print("DISPENSE_DONE "); Serial.println(dispensedML);
    creditML = 0;
  } else {
    Serial.print("DISPENSE_PROGRESS ml="); 
    Serial.print(dispensedML, 1);
    Serial.print(" remaining="); 
    Serial.println(creditML - dispensedML);
  }
}

void stopDispense() {
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  dispensing = false;
}

// ---------------- CUP DETECTION ----------------
bool detectCup() {
  digitalWrite(CUP_TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(CUP_TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(CUP_TRIG_PIN, LOW);

  long duration = pulseIn(CUP_ECHO_PIN, HIGH, 30000);
  float distance = duration * 0.034 / 2;
  return (distance > 0 && distance < CUP_DISTANCE_CM);
}

// ---------------- SERIAL COMMANDS FROM PI ----------------
void handlePiCommands() {
  if (!Serial.available()) return;
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();

  if (cmd.equalsIgnoreCase("MODE WATER")) {
    waterMode = true;
    Serial.println("MODE: WATER");
  }
  else if (cmd.equalsIgnoreCase("MODE CHARGE")) {
    waterMode = false;
    Serial.println("MODE: CHARGE");
  }
  else if (cmd.equalsIgnoreCase("STATUS")) {
    Serial.print("CREDIT_ML "); Serial.println(creditML);
    Serial.print("DISPENSING "); Serial.println(dispensing ? "YES" : "NO");
  }
  else if (cmd.equalsIgnoreCase("RESET")) resetSystem();
  else if (cmd.equalsIgnoreCase("CAL")) calibrateCoins();
  else if (cmd.equalsIgnoreCase("FLOWCAL")) calibrateFlow();
}

// ---------------- CONVERSIONS ----------------
float pulsesToML(unsigned long pulses) {
  return (pulses / pulsesPerLiter) * 1000.0;
}

// ---------------- CALIBRATION ----------------
void calibrateCoins() {
  Serial.println("Calibrating coins...");
  coinPulseCount = 0;
  Serial.println("Insert 1 Peso...");
  waitForCoinPulse();
  coin1P_pulses = coinPulseCount; EEPROM.put(0, coin1P_pulses);
  Serial.print("1P: "); Serial.println(coin1P_pulses);

  coinPulseCount = 0;
  Serial.println("Insert 5 Peso...");
  waitForCoinPulse();
  coin5P_pulses = coinPulseCount; EEPROM.put(4, coin5P_pulses);
  Serial.print("5P: "); Serial.println(coin5P_pulses);

  coinPulseCount = 0;
  Serial.println("Insert 10 Peso...");
  waitForCoinPulse();
  coin10P_pulses = coinPulseCount; EEPROM.put(8, coin10P_pulses);
  Serial.print("10P: "); Serial.println(coin10P_pulses);

  Serial.println("Calibration complete.");
}

void waitForCoinPulse() {
  unsigned long start = millis();
  while (millis() - start < 10000) {
    if (coinPulseCount > 0 && millis() - lastCoinPulseTime > COIN_TIMEOUT_MS) return;
  }
  Serial.println("Timeout. Skipped coin.");
}

void calibrateFlow() {
  Serial.println("FLOW CALIBRATION: Collect 1000 ml, then send 'DONE'");
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
  Serial.println(pulsesPerLiter);
}

// ---------------- RESET ----------------
void resetSystem() {
  creditML = 0;
  dispensing = false;
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  Serial.println("System reset.");
  lastActivity = millis();
}
