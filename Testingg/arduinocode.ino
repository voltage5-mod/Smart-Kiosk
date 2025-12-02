#include <EEPROM.h>

/* =======================
   FUNCTION PROTOTYPES
   ======================= */
void resetSystem();
void showStatus();
float pulsesToML(unsigned long pulses);
bool detectCup();
void startDispense(uint16_t ml);
void startDispenseWithEvents();
void stopDispense();
void handleCoin();
void handleCup();
void handleDispensing();
void handleSerialCommand();
void processCommand(char* cmd);
void handleInactivity();
void reportStatus();

/* =======================
   PIN DEFINITIONS
   ======================= */
#define COIN_PIN          3
#define FLOW_SENSOR_PIN   2
#define CUP_TRIG_PIN      9
#define CUP_ECHO_PIN      10
#define PUMP_PIN          8
#define VALVE_PIN         7

/* =======================
   CONSTANTS
   ======================= */
#define COIN_DEBOUNCE_MS  50
#define COIN_TIMEOUT_MS   800
#define INACTIVITY_TIMEOUT 300000
#define CUP_DISTANCE_CM   10.0

/* =======================
   MODES
   ======================= */
#define MODE_WATER 0
#define MODE_CHARGING 1

/* =======================
   FLOW CALIBRATION
   ======================= */
float pulsesPerLiter = 450.0;

/* =======================
   COIN SETTINGS
   ======================= */
uint8_t coin1P_pulses = 1;
uint8_t coin5P_pulses = 5;
uint8_t coin10P_pulses = 10;

/* =======================
   SYSTEM STATE
   ======================= */
uint8_t currentMode = MODE_WATER;

volatile unsigned long lastCoinPulseTime = 0;
volatile unsigned long lastCoinMicros = 0;
volatile uint8_t coinPulseCount = 0;

volatile unsigned long flowPulseCount = 0;

bool dispensing = false;
bool coinInputEnabled = true;

uint16_t creditML = 0;
uint16_t chargeSeconds = 0;

unsigned long targetPulses = 0;
unsigned long startFlowCount = 0;
unsigned long lastActivity = 0;

int16_t last_creditML = -1;
int16_t last_chargeSeconds = -1;
bool last_dispensing = false;
unsigned long last_flowCount = 0;

// CUP STATE
bool lastCupState = false;
unsigned long cupDetectedTime = 0;

/* =======================
   SERIAL COMMAND BUFFER
   ======================= */
char cmdBuffer[32];
uint8_t cmdIndex = 0;

/* =======================
   INTERRUPTS
   ======================= */
void coinISR() {
  if (!coinInputEnabled) return;

  unsigned long nowMicros = micros();
  if (nowMicros - lastCoinMicros < 5000) return;

  lastCoinMicros = nowMicros;

  unsigned long now = millis();
  if (now - lastCoinPulseTime > COIN_DEBOUNCE_MS) {
    coinPulseCount++;
    lastCoinPulseTime = now;
  }
}

void flowISR() {
  flowPulseCount++;
}

/* =======================
   SETUP
   ======================= */
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

  if (isnan(pulsesPerLiter) || pulsesPerLiter < 200 || pulsesPerLiter > 10000)
      pulsesPerLiter = 450.0;

  Serial.println(F("System Ready."));
  lastActivity = millis();
}

/* =======================
   MAIN LOOP
   ======================= */
void loop() {
  handleCoin();

  if (currentMode == MODE_WATER) {
    handleCup();
    handleDispensing();
  }

  handleInactivity();
  handleSerialCommand();
  reportStatus();

  delay(100);
}

/* =======================
   COIN HANDLER
   ======================= */
void handleCoin() {
  if (!coinInputEnabled) { coinPulseCount = 0; return; }

  if (coinPulseCount > 0 && (millis() - lastCoinPulseTime > COIN_TIMEOUT_MS)) {

    uint8_t pulses = coinPulseCount;
    coinPulseCount = 0;

    if (pulses == 1) Serial.println(F("COIN:1"));
    else if (pulses == 5) Serial.println(F("COIN:5"));
    else if (pulses == 10) Serial.println(F("COIN:10"));
    else Serial.println(F("Rejected noise pulses."));

    delay(10);
    lastActivity = millis();
  }
}

/* =======================
   CUP HANDLER + COUNTDOWN
   ======================= */
void handleCup() {
  bool cupNow = detectCup();

  if (cupNow && !lastCupState) {
    Serial.println("CUP_DETECTED");
    cupDetectedTime = millis();
  }

  if (!cupNow && lastCupState) {
    Serial.println("CUP_REMOVED");
  }

  lastCupState = cupNow;

  if (!cupNow || creditML <= 0 || dispensing) return;

  unsigned long elapsed = millis() - cupDetectedTime;
  int countdown = 3 - (elapsed / 1000);

  if (countdown > 0) {
    Serial.print("COUNTDOWN:");
    Serial.println(countdown);
    return;
  }

  Serial.println("COUNTDOWN_END");
  startDispenseWithEvents();
}

/* =======================
   DISPENSING SYSTEM
   ======================= */
void startDispenseWithEvents() {
  Serial.println("dispense_start");
  startDispense(creditML);
}

void startDispense(uint16_t ml) {
  if (currentMode != MODE_WATER) return;

  coinInputEnabled = false;
  detachInterrupt(digitalPinToInterrupt(COIN_PIN));
  coinPulseCount = 0;

  targetPulses = (unsigned long)((ml / 1000.0) * pulsesPerLiter);
  startFlowCount = flowPulseCount;

  delay(200);

  digitalWrite(PUMP_PIN, HIGH);
  digitalWrite(VALVE_PIN, HIGH);
  dispensing = true;

  float flowRate = 41.7;
  float seconds = (ml / flowRate) + 4.0;

  Serial.print("ANIMATION_START:");
  Serial.print(ml);
  Serial.print(",");
  Serial.println((uint16_t)(seconds + 0.5));
}

void handleDispensing() {
  if (!dispensing) return;

  unsigned long done = flowPulseCount - startFlowCount;

  if (done >= targetPulses) stopDispense();
}

void stopDispense() {
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  dispensing = false;

  float totalML = pulsesToML(flowPulseCount - startFlowCount);

  Serial.print("dispense_done:");
  Serial.println((int)totalML);

  creditML = 0;

  delay(300);

  coinPulseCount = 0;
  lastCoinMicros = micros();
  coinInputEnabled = true;

  attachInterrupt(digitalPinToInterrupt(COIN_PIN), coinISR, FALLING);
  lastActivity = millis();
}

/* =======================
   HELPERS
   ======================= */
float pulsesToML(unsigned long pulses) {
  return (pulses / pulsesPerLiter) * 1000.0;
}

bool detectCup() {
  digitalWrite(CUP_TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(CUP_TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(CUP_TRIG_PIN, LOW);

  long duration = pulseIn(CUP_ECHO_PIN, HIGH, 30000);
  if (duration == 0) return false;

  float distance = duration * 0.034 / 2;
  return (distance > 0 && distance < CUP_DISTANCE_CM);
}

/* =======================
   INACTIVITY
   ======================= */
void handleInactivity() {
  if (millis() - lastActivity > INACTIVITY_TIMEOUT && !dispensing) {
    resetSystem();
  }
}

/* =======================
   STATUS REPORTING
   ======================= */
void reportStatus() {
  bool changed =
      (creditML != last_creditML ||
       chargeSeconds != last_chargeSeconds ||
       dispensing != last_dispensing ||
       flowPulseCount != last_flowCount);

  if (!changed) return;

  Serial.print("MODE:");
  Serial.println(currentMode == MODE_WATER ? "WATER" : "CHARGING");

  Serial.print("CREDIT_ML:");
  Serial.println(creditML);

  Serial.print("CHARGE_SECONDS:");
  Serial.println(chargeSeconds);

  Serial.print("DISPENSING:");
  Serial.println(dispensing ? "YES" : "NO");

  Serial.print("FLOW_PULSES:");
  Serial.println(flowPulseCount);

  last_creditML = creditML;
  last_chargeSeconds = chargeSeconds;
  last_dispensing = dispensing;
  last_flowCount = flowPulseCount;
}

/* =======================
   SERIAL COMMANDS
   ======================= */
void handleSerialCommand() {
  while (Serial.available()) {
    char c = Serial.read();

    if (c == '\n' || c == '\r') {
      if (cmdIndex > 0) {
        cmdBuffer[cmdIndex] = '\0';
        processCommand(cmdBuffer);
        cmdIndex = 0;
      }
    } else if (cmdIndex < sizeof(cmdBuffer) - 1) {
      cmdBuffer[cmdIndex++] = c;
    }
  }
}

void processCommand(char* cmd) {
  for (char* p = cmd; *p; p++) *p = toupper(*p);

  if (strcmp(cmd, "STATUS") == 0) showStatus();
  else if (strcmp(cmd, "RESET") == 0) resetSystem();
  else if (strcmp(cmd, "WATER") == 0) currentMode = MODE_WATER;
  else if (strcmp(cmd, "CHARGING") == 0) currentMode = MODE_CHARGING;
  else Serial.println(F("Unknown command."));
}

/* =======================
   SHOW STATUS
   ======================= */
void showStatus() {
  Serial.println(F("=== SYSTEM STATUS ==="));
  Serial.print(F("Mode: ")); 
  Serial.println(currentMode == MODE_WATER ? "WATER" : "CHARGING");
  Serial.print(F("Water Credit: ")); Serial.println(creditML);
  Serial.print(F("Charging Credit: ")); Serial.println(chargeSeconds);
  Serial.print(F("Flow pulses: ")); Serial.println(flowPulseCount);
  Serial.print(F("Flow mL: ")); Serial.println(pulsesToML(flowPulseCount));
  Serial.println(F("===================="));
}

/* =======================
   RESET SYSTEM
   ======================= */
void resetSystem() {
  creditML = 0;
  chargeSeconds = 0;
  dispensing = false;

  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);

  attachInterrupt(digitalPinToInterrupt(COIN_PIN), coinISR, FALLING);

  Serial.println("System reset.");
  lastActivity = millis();
}
