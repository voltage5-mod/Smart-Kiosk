#include <EEPROM.h>

// ---------------- PINUs DEFINITIONS ----------------
#define COIN_PIN          3     // Coin slot signal pin 
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

// ---------------- MODES ----------------
#define MODE_WATER 0
#define MODE_CHARGING 1

// ---------------- FLOW CALIBRATION ----------------
float pulsesPerLiter = 450.0;   // will be overwritten by EEPROM

// ---------------- COIN CREDIT SETTINGS ----------------
uint8_t coin1P_pulses = 1;    // P1 = 1 pulse
uint8_t coin5P_pulses = 5;    // P5 = 5 pulses
uint8_t coin10P_pulses = 10;  // P10 = 10 pulses



// ---------------- SYSTEM STATE ----------------
uint8_t currentMode = MODE_WATER;  // 0=WATER, 1=CHARGING

// ---------------- VOLATILES ----------------
volatile unsigned long lastCoinPulseTime = 0;
volatile unsigned long lastCoinMicros = 0;
volatile uint8_t coinPulseCount = 0;
volatile unsigned long flowPulseCount = 0;

// ---------------- SYSTEM STATE ----------------
bool dispensing = false;
bool coinInputEnabled = true;

uint16_t creditML = 0;
uint16_t chargeSeconds = 0;
unsigned long targetPulses = 0;
unsigned long startFlowCount = 0;
unsigned long lastActivity = 0;

// Serial change detection
int16_t last_creditML = -1;
int16_t last_chargeSeconds = -1;
bool last_dispensing = false;
unsigned long last_flowCount = 0;

// Command buffer
char cmdBuffer[32];
uint8_t cmdIndex = 0;

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

  Serial.println(F("System Ready. Insert coin or type commands."));
  Serial.print(F("Current Mode: ")); 
  Serial.println(currentMode == MODE_WATER ? "WATER" : "CHARGING");
  lastActivity = millis();
}

// ---------------- LOOP ----------------
void loop() {
  handleCoin();
  
  // Only handle cup detection in WATER mode
  if (currentMode == MODE_WATER) {
    handleCup();
    handleDispensing();
  }
  
  handleInactivity();
  
  handleSerialCommand();

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
    uint8_t pulses = coinPulseCount;
    coinPulseCount = 0;

    // REMOVE ALL DEBUG MESSAGES - keep it clean
    // Serial.print(F("DEBUG: Received "));
    // Serial.print(pulses);
    // Serial.print(F(" pulse(s) in "));
    // Serial.println(currentMode == MODE_WATER ? "WATER" : "CHARGING");

    if (pulses < 1 || pulses > 12) {
      Serial.println(F("Rejected noise pulses."));
      return;
    }

    // EXACT MATCHING - only determine coin value
    uint8_t coinValue = 0;
    
    if (pulses == 1) {
      coinValue = 1;
    } 
    else if (pulses == 5) {
      coinValue = 5;
    }
    else if (pulses == 10) {
      coinValue = 10;
    }
    else {
      Serial.print(F("Unknown coin pattern: "));
      Serial.println(pulses);
      return;
    }

    // Send ONLY ONE clean message with coin value
    // Format: "COIN:1" or "COIN:5" or "COIN:10"
    Serial.print(F("COIN:"));
    Serial.println(coinValue);
    
    // Small delay to ensure message is sent
    delay(10);

    lastActivity = millis();
  }
}


// ---------------- CUP HANDLER ----------------
// In arduinocode.ino, update the handleCup() function:
void handleCup() {
  // Only detect cup if in WATER mode with credit
  if (currentMode != MODE_WATER) {
    return;  // Don't check cup if not in WATER mode
  }
  
  if (creditML <= 0) {
    // Optional debug message
    // Serial.println(F("DEBUG: Cup detected but no credit"));
    return;
  }
  
  if (detectCup() && creditML > 0 && !dispensing) {
    Serial.println(F("Cup detected. Starting dispense..."));
    startDispense(creditML);
  }
}


// ---------------- DISPENSING ----------------
void startDispense(uint16_t ml) {
  // Only allow dispensing in WATER mode
  if (currentMode != MODE_WATER) {
    Serial.println(F("ERROR: Cannot dispense in CHARGING mode"));
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
  
  uint16_t animationSeconds = (uint16_t)(estimatedSeconds + 0.5f); 
  
  // Send clean animation command FIRST, then debug messages
  Serial.print(F("ANIMATION_START:"));
  Serial.print(ml);
  Serial.print(F(","));
  Serial.println(animationSeconds);
  
  // Small delay to ensure the animation command is sent completely
  delay(50);
  
  // Then send debug messages separately
  Serial.print(F("DEBUG: Starting dispense - ML: "));
  Serial.print(ml);
  Serial.print(F(", Flow Rate: "));
  Serial.print(baseFlowRateMLperSecond);
  Serial.print(F(" mL/s, Estimated Time: "));
  Serial.println(animationSeconds);
}

void handleDispensing() {
  if (!dispensing) return;

  unsigned long dispensedPulses = flowPulseCount - startFlowCount;
  
  // Show dispensing progress
  static unsigned long lastProgress = 0;
  if (millis() - lastProgress > 1000) {
    float progressML = pulsesToML(dispensedPulses);
    Serial.print(F("Dispensing progress: "));
    Serial.print(progressML, 1);
    Serial.print(F("mL / "));
    Serial.print(creditML);
    Serial.println(F("mL"));
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
  Serial.print(F("Dispensing complete. Total: "));
  Serial.print(dispensedML, 1);
  Serial.println(F(" ml"));

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
  // Convert to uppercase for case-insensitive comparison
  for (char* p = cmd; *p; p++) {
    *p = toupper(*p);
  }

  if (strcmp(cmd, "CAL") == 0) calibrateCoins();
  else if (strcmp(cmd, "FLOWCAL") == 0) calibrateFlow();
  else if (strcmp(cmd, "STATUS") == 0) showStatus();
  else if (strcmp(cmd, "RESET") == 0) resetSystem();
  else if (strcmp(cmd, "TEST") == 0) testCoinPatterns();
  else if (strcmp(cmd, "WATER") == 0) setMode(MODE_WATER);
  else if (strcmp(cmd, "CHARGING") == 0) setMode(MODE_CHARGING);
  else if (strcmp(cmd, "CLEAR") == 0) clearCredits();
  else if (strncmp(cmd, "MODE ", 5) == 0) {
    char* modeStr = cmd + 5;
    if (strcmp(modeStr, "WATER") == 0) setMode(MODE_WATER);
    else if (strcmp(modeStr, "CHARGING") == 0) setMode(MODE_CHARGING);
    else Serial.println(F("Invalid mode. Use: MODE WATER or MODE CHARGING"));
  }
  else {
    Serial.println(F("Unknown command. Use: CAL, FLOWCAL, STATUS, RESET, TEST, MODE [WATER|CHARGING], WATER, CHARGING, CLEAR"));
  }
}

void setMode(uint8_t newMode) {
  if (dispensing) {
    Serial.println(F("ERROR: Cannot change mode while dispensing"));
    return;
  }
  
  currentMode = newMode;
  Serial.print(F("Mode set to: "));
  Serial.println(currentMode == MODE_WATER ? "WATER" : "CHARGING");
  
  // Reset credits when switching modes to prevent confusion
  if (currentMode == MODE_WATER) {
    chargeSeconds = 0;
    Serial.println(F("Charging credits cleared"));
  } else {
    creditML = 0;
    Serial.println(F("Water credits cleared"));
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
    Serial.print(F("MODE:"));
    Serial.println(currentMode == MODE_WATER ? "WATER" : "CHARGING");
    Serial.print(F("CREDIT_ML:"));
    Serial.println(creditML);
    Serial.print(F("CHARGE_SECONDS:"));
    Serial.println(chargeSeconds);
    Serial.print(F("DISPENSING:"));
    Serial.println(dispensing ? "YES" : "NO");
    Serial.print(F("FLOW_PULSES:"));
    Serial.println(flowPulseCount);
    
    last_creditML = creditML;
    last_chargeSeconds = chargeSeconds;
    last_dispensing = dispensing;
    last_flowCount = flowPulseCount;
  }
}

void showStatus() {
  Serial.println(F("=== SYSTEM STATUS ==="));
  Serial.print(F("Current Mode: ")); 
  Serial.println(currentMode == MODE_WATER ? "WATER" : "CHARGING");
  Serial.print(F("Water Credit: ")); Serial.print(creditML); Serial.println(F(" mL"));
  Serial.print(F("Charging Credit: ")); Serial.print(chargeSeconds); Serial.println(F(" seconds"));
  Serial.print(F("Dispensing: ")); Serial.println(dispensing ? "YES" : "NO");
  Serial.print(F("Flow pulses: ")); Serial.println(flowPulseCount);
  Serial.print(F("Flow mL: ")); Serial.println(pulsesToML(flowPulseCount), 2);
  Serial.print(F("Flow calibration: ")); Serial.println(pulsesPerLiter);
  Serial.print(F("Coin patterns - P1: ")); Serial.print(coin1P_pulses);
  Serial.print(F(", P5: ")); Serial.print(coin5P_pulses);
  Serial.print(F(", P10: ")); Serial.println(coin10P_pulses);
  Serial.println(F("===================="));
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
  Serial.println(F("All credits cleared"));
  lastActivity = millis();
}

// ---------------- CALIBRATION ----------------
void calibrateCoins() {
  Serial.println(F("=== COIN CALIBRATION ==="));
  Serial.println(F("Insert coins when prompted..."));

  coinPulseCount = 0;
  Serial.println(F("Insert 1 Peso coin..."));
  waitForCoinPulse();
  coin1P_pulses = coinPulseCount;
  EEPROM.put(0, coin1P_pulses);
  Serial.print(F("P1 coin: ")); Serial.print(coin1P_pulses); Serial.println(F(" pulses"));

  coinPulseCount = 0;
  Serial.println(F("Insert 5 Peso coin..."));
  waitForCoinPulse();
  coin5P_pulses = coinPulseCount;
  EEPROM.put(4, coin5P_pulses);
  Serial.print(F("P5 coin: ")); Serial.print(coin5P_pulses); Serial.println(F(" pulses"));

  coinPulseCount = 0;
  Serial.println(F("Insert 10 Peso coin..."));
  waitForCoinPulse();
  coin10P_pulses = coinPulseCount;
  EEPROM.put(8, coin10P_pulses);
  Serial.print(F("P10 coin: ")); Serial.print(coin10P_pulses); Serial.println(F(" pulses"));

  Serial.println(F("Coin calibration saved to EEPROM."));
}

void waitForCoinPulse() {
  unsigned long start = millis();
  while (millis() - start < 15000) { // 15 second timeout
    if (coinPulseCount > 0 && millis() - lastCoinPulseTime > COIN_TIMEOUT_MS) {
      Serial.print(F("Detected: ")); Serial.print(coinPulseCount); Serial.println(F(" pulses"));
      return;
    }
    delay(100);
  }
  Serial.println(F("Timeout. No coin detected."));
}

void calibrateFlow() {
  Serial.println(F("=== FLOW CALIBRATION ==="));
  Serial.println(F("Collect exactly 1000 ml and type DONE when ready."));

  flowPulseCount = 0;
  digitalWrite(PUMP_PIN, HIGH);
  digitalWrite(VALVE_PIN, HIGH);

  unsigned long startTime = millis();
  while (millis() - startTime < 120000) { // 2 minute timeout
    if (Serial.available()) {
      char c = Serial.read();
      if (c == 'D' || c == 'd') {
        // Check for "DONE"
        delay(10); // Wait for rest of characters
        while (Serial.available()) Serial.read(); // Clear buffer
        break;
      }
    }
    // Show progress every 2 seconds
    static unsigned long lastUpdate = 0;
    if (millis() - lastUpdate > 2000) {
      Serial.print(F("Current pulses: ")); Serial.println(flowPulseCount);
      lastUpdate = millis();
    }
    delay(100);
  }

  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);

  pulsesPerLiter = flowPulseCount;
  EEPROM.put(12, pulsesPerLiter);

  Serial.print(F("New calibration saved: "));
  Serial.print(pulsesPerLiter);
  Serial.println(F(" pulses per liter."));
}

// ---------------- TEST FUNCTION ----------------
void testCoinPatterns() {
  Serial.println(F("=== COIN TEST MODE ==="));
  Serial.println(F("Insert coins to see pulse counts. Type any key to exit."));
  Serial.println(F("Waiting for coins..."));

  unsigned long startTime = millis();
  while (millis() - startTime < 60000) { // Run for 60 seconds
    if (coinPulseCount > 0 && (millis() - lastCoinPulseTime > COIN_TIMEOUT_MS)) {
      uint8_t pulses = coinPulseCount;
      coinPulseCount = 0;
      
      Serial.print(F("TEST: Detected "));
      Serial.print(pulses);
      Serial.println(F(" pulses"));
      
      // Try to identify the coin
      if (pulses == 1) Serial.println(F("TEST: This appears to be a P1 coin"));
      else if (pulses == 5) Serial.println(F("TEST: This appears to be a P5 coin"));
      else if (pulses == 10) Serial.println(F("TEST: This appears to be a P10 coin"));
      else Serial.println(F("TEST: Unknown coin pattern"));
    }
    
    if (Serial.available()) {
      while (Serial.available()) Serial.read(); // Clear buffer
      break;
    }
    
    delay(100);
  }
  Serial.println(F("=== TEST MODE ENDED ==="));
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

  Serial.println(F("System reset."));
  lastActivity = millis();
}