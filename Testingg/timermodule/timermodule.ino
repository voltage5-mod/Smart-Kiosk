// timer_display_4slot.ino
// Controls 4 independent 7-segment displays

#include <TM1637Display.h>

// Define pins for each display
#define CLK_1 2
#define DIO_1 3
#define CLK_2 4
#define DIO_2 5
#define CLK_3 6
#define DIO_3 7
#define CLK_4 8
#define DIO_4 9

TM1637Display display1(CLK_1, DIO_1);
TM1637Display display2(CLK_2, DIO_2);
TM1637Display display3(CLK_3, DIO_3);
TM1637Display display4(CLK_4, DIO_4);

TM1637Display* displays[4] = {&display1, &display2, &display3, &display4};

// Time remaining for each slot (in seconds)
int slotTimes[4] = {0, 0, 0, 0};
bool slotActive[4] = {false, false, false, false};
bool blinkColon[4] = {true, true, true, true};
unsigned long lastBlink[4] = {0, 0, 0, 0};
unsigned long lastDecrement[4] = {0, 0, 0, 0};
int brightness = 3;  // 0-7

void setup() {
  Serial.begin(115200);
  
  // Initialize all displays
  for (int i = 0; i < 4; i++) {
    displays[i]->setBrightness(brightness);
    displays[i]->clear();
    
    // Show slot number during startup
    displays[i]->showNumberDec(i+1);
  }
  
  delay(1000);
  
  // Clear all displays
  for (int i = 0; i < 4; i++) {
    displays[i]->clear();
  }
  
  Serial.println("4SLOT_TIMER_READY");
}

void loop() {
  // Read serial commands
  if (Serial.available()) {
    String command = Serial.readStringUntil('\n');
    command.trim();
    processCommand(command);
  }
  
  // Update all displays
  for (int slot = 0; slot < 4; slot++) {
    updateDisplay(slot);
  }
  
  delay(100);
}

void processCommand(String cmd) {
  // Command formats:
  // "SLOT1:3600"  - Set slot 1 to 3600 seconds
  // "SLOT2:OFF"   - Turn off slot 2 display
  // "SLOT3:-"     - Show "--" for slot 3 (waiting)
  // "BRIGHT:5"    - Set brightness 0-7
  // "TEST"        - Test all displays
  
  if (cmd.startsWith("SLOT")) {
    int slotNum = cmd.charAt(4) - '1';  // 0-based index
    
    if (slotNum >= 0 && slotNum < 4) {
      int colonPos = cmd.indexOf(':');
      if (colonPos != -1) {
        String valueStr = cmd.substring(colonPos + 1);
        
        if (valueStr == "OFF" || valueStr == "0") {
          slotTimes[slotNum] = 0;
          slotActive[slotNum] = false;
          displays[slotNum]->clear();
        }
        else if (valueStr == "-" || valueStr == "WAIT") {
          // Show "--" for waiting/available slot
          slotActive[slotNum] = false;
          displays[slotNum]->showNumberDecEx(0, 0x40, false, 2, 0);  // Show "--"
          displays[slotNum]->showNumberDecEx(0, 0x40, false, 2, 2);  // Both segments
        }
        else {
          // Set time in seconds
          slotTimes[slotNum] = valueStr.toInt();
          slotActive[slotNum] = (slotTimes[slotNum] > 0);
          
          if (slotActive[slotNum]) {
            lastDecrement[slotNum] = millis();
          }
        }
      }
    }
  }
  else if (cmd.startsWith("BRIGHT:")) {
    brightness = cmd.substring(7).toInt();
    brightness = constrain(brightness, 0, 7);
    
    for (int i = 0; i < 4; i++) {
      displays[i]->setBrightness(brightness);
    }
  }
  else if (cmd == "TEST") {
    testDisplays();
  }
  else if (cmd == "RESET") {
    for (int i = 0; i < 4; i++) {
      slotTimes[i] = 0;
      slotActive[i] = false;
      displays[i]->clear();
    }
  }
  else if (cmd == "STATUS") {
    Serial.print("STATUS:");
    for (int i = 0; i < 4; i++) {
      Serial.print(slotTimes[i]);
      if (i < 3) Serial.print(",");
    }
    Serial.println();
  }
  
  Serial.print("ACK:");
  Serial.println(cmd);
}

void updateDisplay(int slot) {
  if (!slotActive[slot]) {
    return;
  }
  
  // Decrement time every second
  if (millis() - lastDecrement[slot] >= 1000) {
    if (slotTimes[slot] > 0) {
      slotTimes[slot]--;
      lastDecrement[slot] = millis();
      
      // Send alerts for low time
      if (slotTimes[slot] == 300 || slotTimes[slot] == 60 || 
          slotTimes[slot] == 30 || slotTimes[slot] == 10 || 
          slotTimes[slot] <= 5) {
        Serial.print("ALERT:SLOT");
        Serial.print(slot + 1);
        Serial.print(":");
        Serial.println(slotTimes[slot]);
      }
      
      if (slotTimes[slot] == 0) {
        slotActive[slot] = false;
        Serial.print("COMPLETE:SLOT");
        Serial.println(slot + 1);
        displays[slot]->clear();
        return;
      }
    }
  }
  
  // Blink colon every second
  if (millis() - lastBlink[slot] >= 500) {
    blinkColon[slot] = !blinkColon[slot];
    lastBlink[slot] = millis();
  }
  
  // Format display based on time remaining
  int timeLeft = slotTimes[slot];
  
  if (timeLeft >= 3600) {
    // Display hours and minutes (H:MM)
    int hours = timeLeft / 3600;
    int minutes = (timeLeft % 3600) / 60;
    int displayValue = hours * 100 + minutes;
    
    displays[slot]->showNumberDecEx(displayValue, 
      blinkColon[slot] ? 0x40 : 0x00,  // Colon on/off
      true, 3, 0);  // Show 3 digits starting at position 0
  }
  else if (timeLeft >= 60) {
    // Display minutes and seconds (MM:SS)
    int minutes = timeLeft / 60;
    int seconds = timeLeft % 60;
    int displayValue = minutes * 100 + seconds;
    
    displays[slot]->showNumberDecEx(displayValue, 
      blinkColon[slot] ? 0x40 : 0x00,
      true);
  }
  else {
    // Display seconds only (SS)
    displays[slot]->showNumberDecEx(timeLeft, 
      0x00,  // No colon
      true, 2, 2);  // Show 2 digits at rightmost position
    
    // Blink entire display when less than 10 seconds
    if (timeLeft <= 10) {
      if (blinkColon[slot]) {
        displays[slot]->clear();
      }
    }
  }
}

void testDisplays() {
  // Test pattern for all displays
  for (int i = 0; i < 4; i++) {
    displays[i]->setBrightness(7);
  }
  
  // Countdown test
  for (int count = 9999; count >= 0; count -= 1111) {
    for (int i = 0; i < 4; i++) {
      displays[i]->showNumberDec(count);
    }
    delay(300);
  }
  
  // Slot number display
  for (int i = 0; i < 4; i++) {
    displays[i]->showNumberDec(i+1);
  }
  delay(1000);
  
  // Reset brightness
  for (int i = 0; i < 4; i++) {
    displays[i]->setBrightness(brightness);
    displays[i]->clear();
  }
}