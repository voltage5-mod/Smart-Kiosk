#include <EEPROM.h>

// ---------------- PIN DEFINITIONS ----------------
#define COIN_PIN          2
#define FLOW_SENSOR_PIN   3
#define CUP_TRIG_PIN      9
#define CUP_ECHO_PIN      10
#define PUMP_PIN          8
#define VALVE_PIN         7

// ---------------- CONSTANTS ----------------
#define COIN_DEBOUNCE_MS  50
#define COIN_TIMEOUT_MS   800
#define INACTIVITY_TIMEOUT 300000

// SIMPLE CUP DETECTION
#define CUP_DETECT_THRESHOLD_CM 15.0
#define COUNTDOWN_DURATION_MS 5000

// ---------------- GLOBAL VARIABLES ----------------
int currentMode = 1;
float pulsesPerLiter = 450.0;

// Coin settings
int coin1P_pulses = 1;
int coin5P_pulses = 3;
int coin10P_pulses = 5;

int creditML_1P = 50;
int creditML_5P = 250;
int creditML_10P = 500;

// SIMPLE Cup detection
bool cupDetected = false;
bool countdownActive = false;
unsigned long countdownStartTime = 0;
int currentCountdown = 5;

// Volatiles
volatile unsigned long lastCoinPulseTime = 0;
volatile int coinPulseCount = 0;
volatile unsigned long flowPulseCount = 0;

// System state
bool dispensing = false;
int creditML = 0;
unsigned long targetPulses = 0;
unsigned long startFlowCount = 0;
unsigned long lastActivity = 0;

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

  Serial.println("System Ready - 5s Countdown Mode");
  lastActivity = millis();
}

// ---------------- LOOP ----------------
void loop() {
  handleSerialCommand();
  handleCoin();
  handleCup();
  handleCountdown();
  handleDispensing();

  if (millis() - lastActivity > INACTIVITY_TIMEOUT && !dispensing) {
    resetSystem();
  }

  delay(100);
}

// ---------------- HELPER FUNCTIONS ----------------
float pulsesToML(unsigned long pulses) {
  return (pulses / pulsesPerLiter) * 1000.0;
}

// ---------------- SIMPLE CUP DETECTION ----------------
float readDistance() {
  digitalWrite(CUP_TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(CUP_TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(CUP_TRIG_PIN, LOW);

  long duration = pulseIn(CUP_ECHO_PIN, HIGH, 30000);
  if (duration == 0) {
    return -1.0;
  }
  
  float distance = duration * 0.034 / 2;
  
  // DEBUG: Always send distance reading
  Serial.print("DEBUG distance: ");
  Serial.print(distance);
  Serial.println(" cm");
  
  return distance;
}

void handleCup() {
  // Don't check if already in countdown or dispensing
  if (countdownActive || dispensing) return;

  float distance = readDistance();
  bool currentDetection = (distance > 0 && distance < CUP_DETECT_THRESHOLD_CM);
  
  // Start countdown when cup is first detected with credit
  if (currentDetection && !cupDetected && creditML > 0) {
    cupDetected = true;
    countdownActive = true;
    countdownStartTime = millis();
    currentCountdown = 5;
    
    Serial.println("CUP_DETECTED");
    Serial.println("COUNTDOWN_START 5");
  }
  
  cupDetected = currentDetection;
}

// ---------------- COUNTDOWN HANDLER ----------------
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
    }
  }

  // Check if cup was removed during countdown
  float distance = readDistance();
  if (!(distance > 0 && distance < CUP_DETECT_THRESHOLD_CM)) {
    Serial.println("Cup removed during countdown - CANCELLED");
    countdownActive = false;
    cupDetected = false;
  }
}

// ---------------- COIN HANDLER ----------------
void handleCoin() {
  if (coinPulseCount > 0 && (millis() - lastCoinPulseTime > COIN_TIMEOUT_MS)) {
    int pulses = coinPulseCount;
    
    int peso = 0;
    int ml = 0;
    bool validCoin = false;

    if (pulses >= coin1P_pulses-1 && pulses <= coin1P_pulses+1) { 
      peso = 1; ml = 50; validCoin = true; 
    }
    else if (pulses >= coin5P_pulses-1 && pulses <= coin5P_pulses+1) { 
      peso = 5; ml = 250; validCoin = true; 
    }
    else if (pulses >= coin10P_pulses-1 && pulses <= coin10P_pulses+1) { 
      peso = 10; ml = 500; validCoin = true; 
    }

    if (validCoin) {
      coinPulseCount = 0;
      
      Serial.print("COIN_INSERTED "); 
      Serial.println(peso);

      if (currentMode == 1) {
        creditML += ml;
        Serial.print("COIN_WATER "); 
        Serial.println(ml);
      } 
      else if (currentMode == 2) {
        Serial.print("COIN_CHARGE "); 
        Serial.println(peso);
      }
      lastActivity = millis();
    } else {
      coinPulseCount = 0;
      Serial.print("DEBUG: Rejected coin pattern: ");
      Serial.println(pulses);
    }
  }
}

// ---------------- DISPENSING ----------------
void startDispense(int ml) {
  if (ml <= 0) return;
  
  startFlowCount = flowPulseCount;
  targetPulses = (unsigned long)((ml / 1000.0) * pulsesPerLiter);
  digitalWrite(PUMP_PIN, HIGH);
  digitalWrite(VALVE_PIN, HIGH);
  dispensing = true;

  Serial.println("DISPENSE_START");
  
  // Send initial progress
  Serial.print("DISPENSE_PROGRESS ml=0 remaining=");
  Serial.println(ml);
}

void handleDispensing() {
  if (!dispensing) return;

  unsigned long dispensedPulses = flowPulseCount - startFlowCount;
  float dispensedML = pulsesToML(dispensedPulses);
  float remainingML = creditML - dispensedML;

  // Send progress updates
  if (dispensedPulses % 50 == 0) {
    Serial.print("DISPENSE_PROGRESS ml=");
    Serial.print(dispensedML, 1);
    Serial.print(" remaining=");
    Serial.println(remainingML, 1);
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
  Serial.print("DISPENSE_DONE ");
  Serial.println(dispensedML, 1);

  creditML = 0;
  cupDetected = false;
  lastActivity = millis();
}

// ---------------- SERIAL COMMAND HANDLER ----------------
void handleSerialCommand() {
  if (!Serial.available()) return;
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();

  if (cmd.equalsIgnoreCase("RESET")) resetSystem();
  else if (cmd.equalsIgnoreCase("MODE WATER")) {
    currentMode = 1;
    Serial.println("MODE: WATER");
  }
  else if (cmd.equalsIgnoreCase("MODE CHARGE")) {
    currentMode = 2;
    Serial.println("MODE: CHARGE");
  }
  else if (cmd.equalsIgnoreCase("STATUS")) {
    Serial.print("CREDIT_ML "); Serial.println(creditML);
    Serial.print("DISPENSING "); Serial.println(dispensing ? "YES" : "NO");
    Serial.print("CUP_DETECTED "); Serial.println(cupDetected ? "YES" : "NO");
    Serial.print("COUNTDOWN_ACTIVE "); Serial.println(countdownActive ? "YES" : "NO");
  }
  else if (cmd.equalsIgnoreCase("DEBUG")) {
    float distance = readDistance();
    Serial.print("DEBUG: Distance = ");
    Serial.print(distance);
    Serial.println(" cm");
  }
}

// ---------------- RESET ----------------
void resetSystem() {
  creditML = 0;
  dispensing = false;
  cupDetected = false;
  countdownActive = false;
  digitalWrite(PUMP_PIN, LOW);
  digitalWrite(VALVE_PIN, LOW);
  Serial.println("System reset.");
  lastActivity = millis();
}