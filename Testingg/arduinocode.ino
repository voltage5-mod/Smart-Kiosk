#include <EEPROM.h>

// ---------------- PIN DEFINITIONS ----------------
#define COIN_PIN          3     // Coin slot signal pin (interrupt)
#define FLOW_SENSOR_PIN   2     // YF-S201 flow sensor (interrupt)
#define CUP_TRIG_PIN      9     // Ultrasonic trigger
#define CUP_ECHO_PIN      10    // Ultrasonic echo
#define PUMP_PIN          8     // Pump relay
#define VALVE_PIN         7     // Solenoid valve relay

// ---------------- CONSTANTS ----------------
#define COIN_DEBOUNCE_MS  50
#define COIN_TIMEOUT_MS   800
#define INACTIVITY_TIMEOUT 300000 // 5 min
#define CUP_DISTANCE_CM   10.0

// ---------------- FLOW CALIBRATION ----------------
float pulsesPerLiter = 450.0;   // will be overwritten by EEPROM

// ---------------- COIN CREDIT SETTINGS ----------------
// UPDATED: Coin acceptor sends peso value as pulse count
int coin1P_pulses = 1;    // ₱1 = 1 pulse
int coin5P_pulses = 5;    // ₱5 = 5 pulses (matches peso value)
int coin10P_pulses = 10;  // ₱10 = 10 pulses (matches peso value)

int creditML_1P = 50;
int creditML_5P = 250;
int creditML_10P = 500;

// Charging time in seconds (2min, 10min, 20min)
int chargeSeconds_1P = 120;   // 2 minutes
int chargeSeconds_5P = 600;   // 10 minutes  
int chargeSeconds_10P = 1200; // 20 minutes

// ---------------- SYSTEM MODES ----------------
String currentMode = "WATER";  // Default mode: "WATER" or "CHARGING"

// ---------------- VOLATILES ----------------
volatile unsigned long lastCoinPulseTime = 0;
volatile unsigned long lastCoinMicros = 0;
volatile int coinPulseCount = 0;
volatile unsigned long flowPulseCount = 0;

// ---------------- SYSTEM STATE ----------------
bool dispensing = false;
bool coinInputEnabled = true;

int creditML = 0;
int chargeSeconds = 0;
unsigned long targetPulses = 0;
unsigned long startFlowCount = 0;
unsigned long lastActivity = 0;

// Serial change detection
int last_creditML = -1;
int last_chargeSeconds = -1;
bool last_dispensing = false;
unsigned long last_flowCount = 0;

// ---------------- INTERRUPTS ----------------
void coinISR() {
  if (!coinInputEnabled) return;

  unsigned long nowMicros = micros();

  // Noise pulses are <5ms apart
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

  // Load calibration from EEPROM
  EEPROM.get(0, coin1P_pulses);
  EEPROM.get(4, coin5P_pulses);
  EEPROM.get(8, coin10P_pulses);
  EEPROM.get(12, pulsesPerLiter);

  if (isnan(pulsesPerLiter) || pulsesPerLiter < 200 || pulsesPerLiter > 10000)
    pulsesPerLiter = 450.0;

  Serial.println("System Ready. Insert coin or type commands.");
  Serial.print("Current Mode: "); Serial.println(currentMode);
  lastActivity = millis();
}

// ---------------- LOOP ----------------
void loop() {
  handleCoin();
  
  // Only handle cup detection in WATER mode
  if (currentMode.equalsIgnoreCase("WATER")) {
    handleCup();
    handleDispensing();
  }
  
  handleInactivity();
  
  if (Serial.available()) handleSerialCommand();

  reportStatus();
  
  delay(100);
}

// ---------------- COIN HANDLER ----------------
void handleCoin() {
  if (!coinInputEnabled) {
    coinPulseCount = 0;
    return;
  }

  if (coinPulseCount > 0 && (millis() - lastCoinPulseTime > COIN_TIMEOUT_MS)) {
    int pulses = coinPulseCount;
    coinPulseCount = 0;

    // DEBUG: Show what we received
    Serial.print("DEBUG: Received ");
    Serial.print(pulses);
    Serial.print(" pulse(s) in ");
    Serial.print(currentMode);
    Serial.println(" mode");

    if (pulses < 1 || pulses > 12) {
      Serial.println("Rejected noise pulses.");
      return;
    }

    // EXACT MATCHING for peso-value-as-pulse-count pattern
    int coinValue = 0;
    int addedML = 0;
    int addedSeconds = 0;
    
    if (pulses == 1) {
      coinValue = 1;
      addedML = creditML_1P;
      addedSeconds = chargeSeconds_1P;
      Serial.println("DEBUG: Recognized as ₱1 coin");
    } 
    else if (pulses == 5) {
      coinValue = 5;
      addedML = creditML_5P;
      addedSeconds = chargeSeconds_5P;
      Serial.println("DEBUG: Recognized as ₱5 coin");
    }
    else if (pulses == 10) {
      coinValue = 10;
      addedML = creditML_10P;
      addedSeconds = chargeSeconds_10P;
      Serial.println("DEBUG: Recognized as ₱10 coin");
    }
    else {
      Serial.print("Unknown coin pattern: ");
      Serial.println(pulses);
      return;
    }

    // Handle based on current mode
    if (currentMode.equalsIgnoreCase("WATER")) {
      creditML += addedML;
      
      Serial.print("WATER Coin accepted: pulses=");
      Serial.print(pulses);
      Serial.print(", value=P");
      Serial.print(coinValue);
      Serial.print(", added=");
      Serial.print(addedML);
      Serial.print("mL, total=");
      Serial.print(creditML);
      Serial.println("mL");

      // Send simple coin event for Python listener
      Serial.print("COIN_EVENT:");
      Serial.println(coinValue);

    } 
    else if (currentMode.equalsIgnoreCase("CHARGING")) {
      chargeSeconds += addedSeconds;
      
      Serial.print("CHARGING Coin accepted: pulses=");
      Serial.print(pulses);
      Serial.print(", value=P");
      Serial.print(coinValue);
      Serial.print(", added=");
      Serial.print(addedSeconds);
      Serial.print("s, total=");
      Serial.print(chargeSeconds);
      Serial.println("s");

      // Send simple coin event for Python listener
      Serial.print("COIN_EVENT:");
      Serial.println(coinValue);
    }

    lastActivity = millis();
  }
}

// ---------------- CUP HANDLER ----------------
void handleCup() {
  // Only detect cup if in WATER mode with credit
  if (detectCup() && creditML > 0 && !dispensing) {
    Serial.println("Cup detected. Starting dispense...");
    startDispense(creditML);
  }
}

// ---------------- DISPENSING ----------------
void startDispense(int ml) {
  // Only allow dispensing in WATER mode
  if (!currentMode.equalsIgnoreCase("WATER")) {
    Serial.println("ERROR: Cannot dispense in CHARGING mode");
    return;
  }

  coinInputEnabled = false;
  detachInterrupt(digitalPinToInterrupt(COIN_PIN));
  coinPulseCount = 0;

  // Accurate target pulses (pure formula)
  targetPulses = (unsigned long)((ml / 1000.0) * pulsesPerLiter);
  startFlowCount = flowPulseCount;

  // Flow stabilization for horizontal sensor
  delay(200);

  digitalWrite(PUMP_PIN, HIGH);
  digitalWrite(VALVE_PIN, HIGH);
  dispensing = true;
  
  // Calculate exact animation time based on 41.70 mL/second flow rate
  float baseFlowRateMLperSecond = 41.70;
  float estimatedSeconds = ml / baseFlowRateMLperSecond;
  estimatedSeconds += 4.0; // Add buffer for system delay
  
  int animationSeconds = (int)(estimatedSeconds + 0.5f); 
  
  // Send clean animation command FIRST, then debug messages
  Serial.print("ANIMATION_START:");
  Serial.print(ml);
  Serial.print(",");
  Serial.println(animationSeconds);
  
  // Small delay to ensure the animation command is sent completely
  delay(50);
  
  // Then send debug messages separately
  Serial.print("DEBUG: Starting dispense - ML: ");
  Serial.print(ml);
  Serial.print(", Flow Rate: ");
  Serial.print(baseFlowRateMLperSecond);
  Serial.print(" mL/s, Estimated Time: ");
  Serial.println(animationSeconds);
}

void handleDispensing() {
  if (!dispensing) return;

  unsigned long dispensedPulses = flowPulseCount - startFlowCount;
  
  // Show dispensing progress
  static unsigned long lastProgress = 0;
  if (millis() - lastProgress > 1000) {
    float progressML = pulsesToML(dispensedPulses);
    Serial.print("Dispensing progress: ");
    Serial.print(progressML, 1);
    Serial.print("mL / ");
    Serial.print(creditML);
    Serial.println("mL");
    lastProgress = millis();
  }
  
  if (dispensedPulses >= targetPulses) {
    stopDispense();
  }
}

void stopDispense() {
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  dispensing = false;

  float dispensedML = pulsesToML(flowPulseCount - startFlowCount);
  Serial.print("Dispensing complete. Total: ");
  Serial.print(dispensedML, 1);
  Serial.println(" ml");

  // Reset water credit after dispensing
  creditML = 0;

  delay(300); // noise-clearing window

  // Re-enable coin input
  coinPulseCount = 0;
  lastCoinMicros = micros();
  coinInputEnabled = true;
  attachInterrupt(digitalPinToInterrupt(COIN_PIN), coinISR, FALLING);

  lastActivity = millis();
}

// ---------------- SERIAL COMMAND HANDLER ----------------
void handleSerialCommand() {
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();

  if (cmd.equalsIgnoreCase("CAL")) calibrateCoins();
  else if (cmd.equalsIgnoreCase("FLOWCAL")) calibrateFlow();
  else if (cmd.equalsIgnoreCase("STATUS")) showStatus();
  else if (cmd.equalsIgnoreCase("RESET")) resetSystem();
  else if (cmd.equalsIgnoreCase("TEST")) testCoinPatterns();
  else if (cmd.startsWith("MODE")) handleModeCommand(cmd);
  else if (cmd.equalsIgnoreCase("WATER")) setMode("WATER");
  else if (cmd.equalsIgnoreCase("CHARGING")) setMode("CHARGING");
  else if (cmd.equalsIgnoreCase("CLEAR")) clearCredits();
  else Serial.println("Unknown command. Use: CAL, FLOWCAL, STATUS, RESET, TEST, MODE [WATER|CHARGING], WATER, CHARGING, CLEAR");
}

void handleModeCommand(String cmd) {
  String newMode = cmd.substring(5);
  newMode.trim();
  if (newMode.equalsIgnoreCase("WATER") || newMode.equalsIgnoreCase("CHARGING")) {
    setMode(newMode);
  } else {
    Serial.println("Invalid mode. Use: MODE WATER or MODE CHARGING");
  }
}

void setMode(String newMode) {
  if (dispensing) {
    Serial.println("ERROR: Cannot change mode while dispensing");
    return;
  }
  
  currentMode = newMode;
  Serial.print("Mode set to: ");
  Serial.println(currentMode);
  
  // Reset credits when switching modes to prevent confusion
  if (currentMode.equalsIgnoreCase("WATER")) {
    chargeSeconds = 0;
    Serial.println("Charging credits cleared");
  } else {
    creditML = 0;
    Serial.println("Water credits cleared");
  }
}

// ---------------- STATUS REPORTING ----------------
void reportStatus() {
  bool changed = false;
  
  if (creditML != last_creditML || chargeSeconds != last_chargeSeconds || 
      dispensing != last_dispensing || flowPulseCount != last_flowCount) {
    changed = true;
  }
  
  if (changed) {
    Serial.print("MODE:"); Serial.println(currentMode);
    Serial.print("CREDIT_ML:"); Serial.println(creditML);
    Serial.print("CHARGE_SECONDS:"); Serial.println(chargeSeconds);
    Serial.print("DISPENSING:"); Serial.println(dispensing ? "YES" : "NO");
    Serial.print("FLOW_PULSES:"); Serial.println(flowPulseCount);
    
    last_creditML = creditML;
    last_chargeSeconds = chargeSeconds;
    last_dispensing = dispensing;
    last_flowCount = flowPulseCount;
  }
}

void showStatus() {
  Serial.println("=== SYSTEM STATUS ===");
  Serial.print("Current Mode: "); Serial.println(currentMode);
  Serial.print("Water Credit: "); Serial.print(creditML); Serial.println(" mL");
  Serial.print("Charging Credit: "); Serial.print(chargeSeconds); Serial.println(" seconds");
  Serial.print("Dispensing: "); Serial.println(dispensing ? "YES" : "NO");
  Serial.print("Flow pulses: "); Serial.println(flowPulseCount);
  Serial.print("Flow mL: "); Serial.println(pulsesToML(flowPulseCount), 2);
  Serial.print("Flow calibration: "); Serial.println(pulsesPerLiter);
  Serial.print("Coin patterns - ₱1: "); Serial.print(coin1P_pulses);
  Serial.print(", ₱5: "); Serial.print(coin5P_pulses);
  Serial.print(", ₱10: "); Serial.println(coin10P_pulses);
  Serial.println("====================");
}

// ---------------- HELPERS ----------------
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

void handleInactivity() {
  if (millis() - lastActivity > INACTIVITY_TIMEOUT && !dispensing) {
    resetSystem();
  }
}

void clearCredits() {
  creditML = 0;
  chargeSeconds = 0;
  Serial.println("All credits cleared");
  lastActivity = millis();
}

// ---------------- CALIBRATION ----------------
void calibrateCoins() {
  Serial.println("=== COIN CALIBRATION ===");
  Serial.println("Insert coins when prompted...");

  coinPulseCount = 0;
  Serial.println("Insert 1 Peso coin...");
  waitForCoinPulse();
  coin1P_pulses = coinPulseCount;
  EEPROM.put(0, coin1P_pulses);
  Serial.print("₱1 coin: "); Serial.print(coin1P_pulses); Serial.println(" pulses");

  coinPulseCount = 0;
  Serial.println("Insert 5 Peso coin...");
  waitForCoinPulse();
  coin5P_pulses = coinPulseCount;
  EEPROM.put(4, coin5P_pulses);
  Serial.print("₱5 coin: "); Serial.print(coin5P_pulses); Serial.println(" pulses");

  coinPulseCount = 0;
  Serial.println("Insert 10 Peso coin...");
  waitForCoinPulse();
  coin10P_pulses = coinPulseCount;
  EEPROM.put(8, coin10P_pulses);
  Serial.print("₱10 coin: "); Serial.print(coin10P_pulses); Serial.println(" pulses");

  Serial.println("Coin calibration saved to EEPROM.");
}

void waitForCoinPulse() {
  unsigned long start = millis();
  while (millis() - start < 15000) { // 15 second timeout
    if (coinPulseCount > 0 && millis() - lastCoinPulseTime > COIN_TIMEOUT_MS) {
      Serial.print("Detected: "); Serial.print(coinPulseCount); Serial.println(" pulses");
      return;
    }
    delay(100);
  }
  Serial.println("Timeout. No coin detected.");
}

void calibrateFlow() {
  Serial.println("=== FLOW CALIBRATION ===");
  Serial.println("Collect exactly 1000 ml and type DONE when ready.");

  flowPulseCount = 0;
  digitalWrite(PUMP_PIN, HIGH);
  digitalWrite(VALVE_PIN, HIGH);

  while (true) {
    if (Serial.available()) {
      String cmd = Serial.readStringUntil('\n');
      cmd.trim();
      if (cmd.equalsIgnoreCase("DONE")) break;
    }
    // Show progress
    static unsigned long lastUpdate = 0;
    if (millis() - lastUpdate > 1000) {
      Serial.print("Current pulses: "); Serial.println(flowPulseCount);
      lastUpdate = millis();
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

// ---------------- TEST FUNCTION ----------------
void testCoinPatterns() {
  Serial.println("=== COIN TEST MODE ===");
  Serial.println("Insert coins to see pulse counts. Type any key to exit.");
  Serial.println("Waiting for coins...");

  unsigned long startTime = millis();
  while (millis() - startTime < 60000) { // Run for 60 seconds
    if (coinPulseCount > 0 && (millis() - lastCoinPulseTime > COIN_TIMEOUT_MS)) {
      int pulses = coinPulseCount;
      coinPulseCount = 0;
      
      Serial.print("TEST: Detected ");
      Serial.print(pulses);
      Serial.println(" pulses");
      
      // Try to identify the coin
      if (pulses == 1) Serial.println("TEST: This appears to be a ₱1 coin");
      else if (pulses == 5) Serial.println("TEST: This appears to be a ₱5 coin");
      else if (pulses == 10) Serial.println("TEST: This appears to be a ₱10 coin");
      else Serial.println("TEST: Unknown coin pattern");
    }
    
    if (Serial.available()) {
      Serial.readString(); // Clear buffer
      break;
    }
    
    delay(100);
  }
  Serial.println("=== TEST MODE ENDED ===");
}

// ---------------- RESET ----------------
void resetSystem() {
  creditML = 0;
  chargeSeconds = 0;
  dispensing = false;

  coinInputEnabled = true;
  coinPulseCount = 0;

  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);

  attachInterrupt(digitalPinToInterrupt(COIN_PIN), coinISR, FALLING);

  Serial.println("System reset.");
  lastActivity = millis();
}