#include <EEPROM.h>

// Pin Definitions
#define COIN_PIN          3     // Coin slot signal pin 
#define FLOW_SENSOR_PIN   2     // YF-S201 flow sensor (interrupt)
#define CUP_TRIG_PIN      9     // Ultrasonic trigger
#define CUP_ECHO_PIN      10    // Ultrasonic echo
#define PUMP_PIN          8     // Pump relay
#define VALVE_PIN         7     // Solenoid valve relay

// Constants
float pulsesPerLiter = 450.0;
volatile int pulseCount = 0;
unsigned long lastPulseTime = 0;
float totalLiters = 0;

// Coin values
uint8_t coin1P_pulses = 1;    // P1 = 1 pulse
uint8_t coin5P_pulses = 5;    // P5 = 5 pulses
uint8_t coin10P_pulses = 10;  // P10 = 10 pulses

// Flow sensor interrupt
void pulseCounter() {
  pulseCount++;
  lastPulseTime = millis();
}

void setup() {
  Serial.begin(9600);
  
  // Pin modes
  pinMode(COIN_PIN, INPUT_PULLUP);
  pinMode(FLOW_SENSOR_PIN, INPUT_PULLUP);
  pinMode(PUMP_PIN, OUTPUT);
  pinMode(VALVE_PIN, OUTPUT);
  pinMode(CUP_TRIG_PIN, OUTPUT);
  pinMode(CUP_ECHO_PIN, INPUT);
  
  // Initialize relays OFF
  digitalWrite(PUMP_PIN, HIGH);  // Active LOW relay
  digitalWrite(VALVE_PIN, HIGH);
  
  // Attach interrupts
  attachInterrupt(digitalPinToInterrupt(FLOW_SENSOR_PIN), pulseCounter, RISING);
  
  Serial.println("Arduino Water Vendo Ready");
}

void loop() {
  // Check for coin
  checkCoin();
  
  // Check for serial commands
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    processCommand(command);
  }
  
  // Update flow reading
  static unsigned long lastFlowReport = 0;
  if (millis() - lastFlowReport > 1000) {  // Report every second
    if (pulseCount > 0) {
      totalLiters = pulseCount / pulsesPerLiter;
      Serial.print("FLOW:");
      Serial.println(pulseCount);
      pulseCount = 0;
    }
    lastFlowReport = millis();
  }
  
  // Check cup presence
  checkCup();
  
  delay(10);
}

void checkCoin() {
  static int coinPulseCount = 0;
  static unsigned long lastCoinPulse = 0;
  
  int coinState = digitalRead(COIN_PIN);
  
  if (coinState == LOW && millis() - lastCoinPulse > 50) {  // Debounce
    coinPulseCount++;
    lastCoinPulse = millis();
    
    // Check coin value based on pulse count
    if (coinPulseCount == coin1P_pulses) {
      Serial.println("COIN:1");
      coinPulseCount = 0;
    } else if (coinPulseCount == coin5P_pulses) {
      Serial.println("COIN:5");
      coinPulseCount = 0;
    } else if (coinPulseCount == coin10P_pulses) {
      Serial.println("COIN:10");
      coinPulseCount = 0;
    }
  }
  
  // Reset if no pulses for 1 second
  if (millis() - lastCoinPulse > 1000) {
    coinPulseCount = 0;
  }
}

void checkCup() {
  static unsigned long lastCupCheck = 0;
  
  if (millis() - lastCupCheck > 1000) {  // Check every second
    // Send ultrasonic pulse
    digitalWrite(CUP_TRIG_PIN, LOW);
    delayMicroseconds(2);
    digitalWrite(CUP_TRIG_PIN, HIGH);
    delayMicroseconds(10);
    digitalWrite(CUP_TRIG_PIN, LOW);
    
    // Read echo
    long duration = pulseIn(CUP_ECHO_PIN, HIGH);
    float distance = duration * 0.034 / 2;  // Convert to cm
    
    if (distance < 10) {  // Cup present if < 10cm
      Serial.println("CUP:PRESENT");
    } else {
      Serial.println("CUP:ABSENT");
    }
    
    lastCupCheck = millis();
  }
}

void processCommand(String cmd) {
  cmd.trim();
  
  if (cmd == "PUMP:ON") {
    digitalWrite(PUMP_PIN, LOW);  // Turn pump ON
    Serial.println("PUMP:STARTED");
  } else if (cmd == "PUMP:OFF") {
    digitalWrite(PUMP_PIN, HIGH);  // Turn pump OFF
    Serial.println("PUMP:STOPPED");
  } else if (cmd == "VALVE:OPEN") {
    digitalWrite(VALVE_PIN, LOW);  // Open valve
    Serial.println("VALVE:OPENED");
  } else if (cmd == "VALVE:CLOSE") {
    digitalWrite(VALVE_PIN, HIGH);  // Close valve
    Serial.println("VALVE:CLOSED");
  } else if (cmd == "STATUS") {
    Serial.println("STATUS:READY");
  }
}