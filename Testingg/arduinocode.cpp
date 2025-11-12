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
#define INACTIVITY_TIMEOUT 300000 // 5 min
#define CUP_DISTANCE_CM   10.0
#define BAUDRATE          115200

// ---------------- FLOW CALIBRATION ----------------
// YF-S201 typical: ~450 pulses per liter (4.5 pulses per mL)
float pulsesPerLiter = 450.0;

// ---------------- COIN CREDIT SETTINGS ----------------
int coin1P_pulses = 1;
int coin5P_pulses = 5;
int coin10P_pulses = 10;

// Water mode: mL per coin
int creditML_1P = 100;    // 1 peso = 100 mL
int creditML_5P = 500;    // 5 peso = 500 mL
int creditML_10P = 1000;  // 10 peso = 1000 mL (1 liter)

// Charging mode: minutes per coin (sent to Pi)
int creditMINS_5P = 10;   // 5 peso = 10 minutes
int creditMINS_10P = 20;  // 10 peso = 20 minutes

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

    // Determine coin type based on pulse count
    int coinType = 0;  // 0=unknown, 1=1P, 5=5P, 10=10P
    if (abs(pulses - coin1P_pulses) <= 1) coinType = 1;
    else if (abs(pulses - coin5P_pulses) <= 1) coinType = 5;
    else if (abs(pulses - coin10P_pulses) <= 1) coinType = 10;
    else {
      Serial.print("COIN_UNKNOWN "); Serial.println(pulses);
      return;
    }

    if (waterMode) {
      // WATER MODE: Add mL credit and report to Pi
      int addML = 0;
      if (coinType == 1) addML = creditML_1P;      // 1P = 100mL
      else if (coinType == 5) addML = creditML_5P; // 5P = 500mL
      else if (coinType == 10) addML = creditML_10P; // 10P = 1000mL
      
      creditML += addML;
      Serial.print("COIN_WATER "); Serial.println(creditML);
      Serial.print("Coin inserted: "); Serial.print(coinType); 
      Serial.print("P added "); Serial.print(addML); 
      Serial.print("mL, new total: "); Serial.println(creditML);
    } else {
      // CHARGING MODE: Report coin to Pi (Pi handles charging logic)
      int minutes = 0;
      if (coinType == 5) minutes = creditMINS_5P;    // 5P = 10 min
      else if (coinType == 10) minutes = creditMINS_10P; // 10P = 20 min
      else if (coinType == 1) {
        // 1P not valid for charging, skip
        Serial.println("COIN_IGNORED 1P_invalid_charging");
        return;
      }
      Serial.print("COIN_CHARGE "); Serial.println(coinType);
      Serial.print("Coin inserted: "); Serial.print(coinType); 
      Serial.print("P -> "); Serial.print(minutes); 
      Serial.println(" minutes for charging");
    }
  }
}


// ---------------- CUP HANDLER ----------------
bool cupDetectedPrevious = false;

void handleCup() {
  bool cupPresent = detectCup();
  
  // Cup just detected (transition from not detected to detected)
  if (cupPresent && !cupDetectedPrevious && creditML > 0 && !dispensing) {
    Serial.println("CUP_DETECTED");
    startDispense(creditML);
    lastActivity = millis();
  } 
  // Cup just removed (transition from detected to not detected)
  else if (!cupPresent && cupDetectedPrevious && dispensing) {
    Serial.println("CUP_REMOVED");
    stopDispense();
    // Send remaining balance to Pi
    Serial.print("CREDIT_UPDATE "); Serial.println(creditML);
    lastActivity = millis();
  }
  
  cupDetectedPrevious = cupPresent;
}


// ---------------- DISPENSING ----------------
unsigned long lastDispensingReport = 0;
#define DISPENSING_REPORT_INTERVAL 500  // Report progress every 500ms

void startDispense(int ml) {
  startFlowCount = flowPulseCount;
  targetPulses = (unsigned long)((ml / 1000.0) * pulsesPerLiter);
  digitalWrite(PUMP_PIN, HIGH);
  digitalWrite(VALVE_PIN, HIGH);
  dispensing = true;
  lastDispensingReport = millis();
  Serial.println("DISPENSE_START");
  Serial.print("Target: "); Serial.print(ml); Serial.println(" mL");
}

void handleDispensing() {
  if (!dispensing) return;

  unsigned long dispensedPulses = flowPulseCount - startFlowCount;
  float dispensedML = pulsesToML(dispensedPulses);
  float remainingML = creditML - dispensedML;

  // Check if target reached or credit exhausted
  if (dispensedPulses >= targetPulses || remainingML <= 0) {
    stopDispense();
    Serial.print("DISPENSE_DONE "); Serial.println((int)dispensedML);
    creditML = 0;
    Serial.print("CREDIT_UPDATE 0");
  } 
  // Periodic progress report to Pi (every 500ms)
  else if (millis() - lastDispensingReport >= DISPENSING_REPORT_INTERVAL) {
    Serial.print("DISPENSE_PROGRESS ml="); 
    Serial.print((int)dispensedML);
    Serial.print(" remaining="); 
    Serial.println((int)remainingML);
    lastDispensingReport = millis();
  }
}

void stopDispense() {
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  dispensing = false;
  Serial.println("DISPENSE_STOP");
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
    creditML = 0;
    dispensing = false;
    digitalWrite(PUMP_PIN, LOW);
    digitalWrite(VALVE_PIN, LOW);
    Serial.println("MODE: WATER");
    Serial.println("Ready for water service. Insert coins or place cup.");
  }
  else if (cmd.equalsIgnoreCase("MODE CHARGE")) {
    waterMode = false;
    creditML = 0;
    dispensing = false;
    digitalWrite(PUMP_PIN, LOW);
    digitalWrite(VALVE_PIN, LOW);
    Serial.println("MODE: CHARGE");
    Serial.println("Ready for charging service. Insert coins.");
  }
  else if (cmd.equalsIgnoreCase("STATUS")) {
    Serial.print("STATUS: mode=");
    Serial.print(waterMode ? "WATER" : "CHARGE");
    Serial.print(" credit="); Serial.print(creditML);
    Serial.print(" dispensing="); Serial.println(dispensing ? "YES" : "NO");
  }
  else if (cmd.equalsIgnoreCase("STOP")) {
    stopDispense();
    creditML = 0;
    Serial.println("STOP_ACK");
  }
  else if (cmd.equalsIgnoreCase("RESET")) {
    resetSystem();
    Serial.println("RESET_ACK");
  }
  else if (cmd.equalsIgnoreCase("CAL")) {
    calibrateCoins();
  }
  else if (cmd.equalsIgnoreCase("FLOWCAL")) {
    calibrateFlow();
  }
  else {
    Serial.print("UNKNOWN_CMD: ");
    Serial.println(cmd);
  }
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
