// timer_display_4slot.ino
// Enhanced version with improved functionality
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

// Custom segment patterns
const uint8_t SEG_DASH[] = {
    SEG_G  // Just the middle segment for dash
};
const uint8_t SEG_DASH2[] = {
    SEG_G,
    SEG_G  // Two dashes
};
const uint8_t SEG_ERR[] = {
    SEG_A | SEG_D | SEG_E | SEG_F | SEG_G,  // E
    SEG_E | SEG_G,                           // r
    SEG_E | SEG_G                            // r
};

// Time remaining for each slot (in seconds)
int slotTimes[4] = {0, 0, 0, 0};
bool slotActive[4] = {false, false, false, false};
bool blinkColon[4] = {true, true, true, true};
bool slotPaused[4] = {false, false, false, false};
unsigned long lastBlink[4] = {0, 0, 0, 0};
unsigned long lastDecrement[4] = {0, 0, 0, 0};
unsigned long lastHeartbeat = 0;
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
  Serial.println("Commands: SLOTn:value, BRIGHT:x, TEST, RESET, STATUS, PAUSE:n, RESUME:n, SYNC:n:seconds, HELP");
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
  
  // Send heartbeat every 5 seconds
  if (millis() - lastHeartbeat > 5000) {
    Serial.println("READY");
    lastHeartbeat = millis();
  }
  
  delay(50);  // Reduced delay for more responsive blinking
}

void processCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;
  
  // Command formats:
  // "SLOT1:3600"  - Set slot 1 to 3600 seconds
  // "SLOT2:OFF"   - Turn off slot 2 display
  // "SLOT3:-"     - Show "--" for slot 3 (waiting)
  // "SLOT4:WAIT"  - Show "--" for slot 4 (waiting)
  // "BRIGHT:5"    - Set brightness 0-7
  // "TEST"        - Test all displays
  // "PAUSE:1"     - Pause slot 1
  // "RESUME:1"    - Resume slot 1
  // "SYNC:1:3600" - Sync slot 1 to exact time
  // "STATUS"      - Get all slot times
  // "RESET"       - Reset all slots
  // "HELP"        - Show help
  
  if (cmd.startsWith("SLOT")) {
    int slotNum = cmd.charAt(4) - '1';  // 0-based index
    
    if (slotNum < 0 || slotNum > 3) {
      Serial.print("ERROR: Invalid slot number. Use 1-4. Got: ");
      Serial.println(cmd.charAt(4));
      return;
    }
    
    int colonPos = cmd.indexOf(':');
    if (colonPos != -1) {
      String valueStr = cmd.substring(colonPos + 1);
      valueStr.toUpperCase();  // Case-insensitive for OFF/WAIT
      
      if (valueStr == "OFF" || valueStr == "0") {
        slotTimes[slotNum] = 0;
        slotActive[slotNum] = false;
        slotPaused[slotNum] = false;
        displays[slotNum]->clear();
        Serial.print("SLOT");
        Serial.print(slotNum + 1);
        Serial.println(":OFF");
      }
      else if (valueStr == "-" || valueStr == "WAIT") {
        // Show "--" for waiting/available slot
        slotActive[slotNum] = false;
        slotPaused[slotNum] = false;
        displays[slotNum]->clear();
        // Show "-- --" pattern
        displays[slotNum]->setSegments(SEG_DASH2, 2, 0);
        displays[slotNum]->setSegments(SEG_DASH2, 2, 2);
        Serial.print("SLOT");
        Serial.print(slotNum + 1);
        Serial.println(":WAITING");
      }
      else {
        // Set time in seconds
        int newTime = valueStr.toInt();
        if (newTime < 0) {
          Serial.print("ERROR: Time cannot be negative: ");
          Serial.println(newTime);
          return;
        }
        
        slotTimes[slotNum] = newTime;
        slotActive[slotNum] = (slotTimes[slotNum] > 0);
        slotPaused[slotNum] = false;
        
        if (slotActive[slotNum]) {
          lastDecrement[slotNum] = millis();
          lastBlink[slotNum] = millis();
          Serial.print("SLOT");
          Serial.print(slotNum + 1);
          Serial.print(":SET:");
          Serial.println(slotTimes[slotNum]);
        } else {
          displays[slotNum]->clear();
          Serial.print("SLOT");
          Serial.print(slotNum + 1);
          Serial.println(":CLEARED");
        }
      }
    }
  }
  else if (cmd.startsWith("BRIGHT:")) {
    int newBrightness = cmd.substring(7).toInt();
    brightness = constrain(newBrightness, 0, 7);
    
    for (int i = 0; i < 4; i++) {
      displays[i]->setBrightness(brightness);
    }
    Serial.print("BRIGHTNESS:");
    Serial.println(brightness);
  }
  else if (cmd.startsWith("PAUSE:")) {
    int slotNum = cmd.substring(6).toInt() - 1;
    if (slotNum >= 0 && slotNum < 4) {
      if (slotActive[slotNum] && slotTimes[slotNum] > 0) {
        slotPaused[slotNum] = true;
        Serial.print("SLOT");
        Serial.print(slotNum + 1);
        Serial.println(":PAUSED");
      } else {
        Serial.print("ERROR: Cannot pause inactive slot ");
        Serial.println(slotNum + 1);
      }
    } else {
      Serial.print("ERROR: Invalid slot for PAUSE: ");
      Serial.println(slotNum + 1);
    }
  }
  else if (cmd.startsWith("RESUME:")) {
    int slotNum = cmd.substring(7).toInt() - 1;
    if (slotNum >= 0 && slotNum < 4) {
      if (slotTimes[slotNum] > 0) {
        slotPaused[slotNum] = false;
        slotActive[slotNum] = true;
        lastDecrement[slotNum] = millis();
        Serial.print("SLOT");
        Serial.print(slotNum + 1);
        Serial.println(":RESUMED");
      } else {
        Serial.print("ERROR: Cannot resume empty slot ");
        Serial.println(slotNum + 1);
      }
    } else {
      Serial.print("ERROR: Invalid slot for RESUME: ");
      Serial.println(slotNum + 1);
    }
  }
  else if (cmd.startsWith("SYNC:")) {
    // Format: SYNC:slot:seconds
    int colon1 = cmd.indexOf(':');
    int colon2 = cmd.indexOf(':', colon1 + 1);
    if (colon2 != -1) {
      int slotNum = cmd.substring(colon1 + 1, colon2).toInt() - 1;
      int newTime = cmd.substring(colon2 + 1).toInt();
      
      if (slotNum >= 0 && slotNum < 4) {
        if (newTime >= 0) {
          slotTimes[slotNum] = newTime;
          lastDecrement[slotNum] = millis();
          if (newTime > 0) {
            slotActive[slotNum] = true;
            slotPaused[slotNum] = false;
          }
          Serial.print("SLOT");
          Serial.print(slotNum + 1);
          Serial.print(":SYNCED:");
          Serial.println(slotTimes[slotNum]);
        } else {
          Serial.print("ERROR: Invalid time value: ");
          Serial.println(newTime);
        }
      } else {
        Serial.print("ERROR: Invalid slot for SYNC: ");
        Serial.println(slotNum + 1);
      }
    } else {
      Serial.println("ERROR: Invalid SYNC format. Use: SYNC:slot:seconds");
    }
  }
  else if (cmd == "TEST") {
    testDisplays();
    Serial.println("TEST:COMPLETE");
  }
  else if (cmd == "RESET") {
    for (int i = 0; i < 4; i++) {
      slotTimes[i] = 0;
      slotActive[i] = false;
      slotPaused[i] = false;
      displays[i]->clear();
    }
    Serial.println("ALL_SLOTS_RESET");
  }
  else if (cmd == "STATUS") {
    Serial.print("STATUS:");
    for (int i = 0; i < 4; i++) {
      Serial.print(slotTimes[i]);
      Serial.print(":");
      Serial.print(slotActive[i] ? "A" : "I");
      Serial.print(slotPaused[i] ? "P" : "R");
      if (i < 3) Serial.print(",");
    }
    Serial.println();
  }
  else if (cmd == "HELP") {
    showHelp();
  }
  else {
    Serial.print("ERROR: Unknown command '");
    Serial.print(cmd);
    Serial.println("'");
    Serial.println("Type HELP for available commands");
  }
}

void updateDisplay(int slot) {
  if (!slotActive[slot] || slotPaused[slot]) {
    if (slotPaused[slot] && slotTimes[slot] > 0) {
      // Show paused indication (blink colon solid)
      int timeLeft = slotTimes[slot];
      displayPaused(slot, timeLeft);
    }
    return;
  }
  
  // Decrement time every second
  if (millis() - lastDecrement[slot] >= 1000) {
    if (slotTimes[slot] > 0) {
      slotTimes[slot]--;
      lastDecrement[slot] = millis();
      
      // Send alerts for low time
      if (slotTimes[slot] == 300) {
        Serial.print("ALERT:SLOT");
        Serial.print(slot + 1);
        Serial.println(":5MIN");
      } else if (slotTimes[slot] == 60) {
        Serial.print("ALERT:SLOT");
        Serial.print(slot + 1);
        Serial.println(":1MIN");
      } else if (slotTimes[slot] == 30) {
        Serial.print("ALERT:SLOT");
        Serial.print(slot + 1);
        Serial.println(":30SEC");
      } else if (slotTimes[slot] == 10) {
        Serial.print("ALERT:SLOT");
        Serial.print(slot + 1);
        Serial.println(":10SEC");
      } else if (slotTimes[slot] <= 5 && slotTimes[slot] > 0) {
        Serial.print("ALERT:SLOT");
        Serial.print(slot + 1);
        Serial.print(":");
        Serial.print(slotTimes[slot]);
        Serial.println("SEC");
      }
      
      if (slotTimes[slot] == 0) {
        slotActive[slot] = false;
        slotPaused[slot] = false;
        Serial.print("COMPLETE:SLOT");
        Serial.println(slot + 1);
        
        // Flash display 3 times when complete
        for (int i = 0; i < 3; i++) {
          displays[slot]->setSegments(SEG_DASH2, 2, 0);
          displays[slot]->setSegments(SEG_DASH2, 2, 2);
          delay(300);
          displays[slot]->clear();
          delay(300);
        }
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
      true);  // Show all 4 digits
  }
  else {
    // Display seconds only (SS)
    displays[slot]->showNumberDecEx(timeLeft, 
      0x00,  // No colon
      true, 2, 2);  // Show 2 digits at rightmost position
    
    // Left side blank
    displays[slot]->showNumberDecEx(0, 0x00, true, 2, 0);
    
    // Blink entire display when less than 10 seconds
    if (timeLeft <= 10) {
      if (blinkColon[slot]) {
        displays[slot]->clear();
      }
    }
  }
}

void displayPaused(int slot, int timeLeft) {
  // Show time with solid colon (no blinking) when paused
  if (timeLeft >= 3600) {
    int hours = timeLeft / 3600;
    int minutes = (timeLeft % 3600) / 60;
    int displayValue = hours * 100 + minutes;
    displays[slot]->showNumberDecEx(displayValue, 0x40, true, 3, 0);
  }
  else if (timeLeft >= 60) {
    int minutes = timeLeft / 60;
    int seconds = timeLeft % 60;
    int displayValue = minutes * 100 + seconds;
    displays[slot]->showNumberDecEx(displayValue, 0x40, true);
  }
  else {
    displays[slot]->showNumberDecEx(timeLeft, 0x00, true, 2, 2);
    displays[slot]->showNumberDecEx(0, 0x00, true, 2, 0);
  }
}

void testDisplays() {
  // Test pattern for all displays
  Serial.println("TEST:STARTING");
  
  for (int i = 0; i < 4; i++) {
    displays[i]->setBrightness(7);
  }
  
  // All segments test
  uint8_t allSegments[] = {0xFF, 0xFF, 0xFF, 0xFF};
  for (int i = 0; i < 4; i++) {
    displays[i]->setSegments(allSegments);
  }
  delay(500);
  
  // Countdown test
  for (int count = 8888; count >= 0; count -= 1111) {
    for (int i = 0; i < 4; i++) {
      displays[i]->showNumberDec(count);
    }
    delay(300);
  }
  
  // Colon blink test
  for (int i = 0; i < 6; i++) {
    for (int slot = 0; slot < 4; slot++) {
      displays[slot]->showNumberDecEx(1234, (i % 2) ? 0x40 : 0x00, true);
    }
    delay(300);
  }
  
  // Slot number display
  for (int i = 0; i < 4; i++) {
    displays[i]->showNumberDec(i+1);
  }
  delay(1000);
  
  // Error display test
  for (int i = 0; i < 4; i++) {
    displays[i]->setSegments(SEG_ERR, 3, 0);
  }
  delay(1000);
  
  // Reset to normal
  for (int i = 0; i < 4; i++) {
    displays[i]->setBrightness(brightness);
    displays[i]->clear();
  }
  
  Serial.println("TEST:COMPLETE");
}

void showHelp() {
  Serial.println("=== 4-SLOT TIMER HELP ===");
  Serial.println("SLOTn:value  - Set slot n (1-4) to value (seconds)");
  Serial.println("              Special values: OFF, -, WAIT");
  Serial.println("BRIGHT:x     - Set brightness 0-7");
  Serial.println("PAUSE:n      - Pause slot n");
  Serial.println("RESUME:n     - Resume slot n");
  Serial.println("SYNC:n:sec   - Sync slot n to exact seconds");
  Serial.println("TEST         - Run display test");
  Serial.println("RESET        - Reset all slots");
  Serial.println("STATUS       - Show all slot statuses");
  Serial.println("HELP         - Show this help");
  Serial.println("========================");
}