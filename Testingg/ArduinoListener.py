"""
ArduinoListener.py
------------------
This module manages serial communication between the Raspberry Pi and Arduino Uno.
It listens for real-time hardware events such as coin insertions, water dispensing, 
and flow sensor readings. These events are parsed and forwarded to the UI and 
Firebase systems for data synchronization and user feedback.

🧠 Purpose:
- Bridge hardware logic (Arduino) and software logic (Python/UI)
- Read incoming serial messages from Arduino via USB
- Emit structured events for use in other modules (e.g., WaterScreen)
- Allow sending commands (e.g., MODE WATER, RESET, STATUS) back to Arduino

🧩 Compatible with:
- Water automation system handled by Arduino
- Charging automation handled by Raspberry Pi GPIO
"""

import serial
import threading
import time
import logging

# ----------------- CONFIGURATION -----------------
# Update this if your Arduino shows up on a different USB path
ARDUINO_PORT = "/dev/ttyUSB0"   # Common: /dev/ttyACM0 or /dev/ttyUSB0
ARDUINO_BAUD = 115200
READ_INTERVAL = 0.05  # seconds between read cycles

# -------------------------------------------------

class ArduinoListener:
    """
    ArduinoListener continuously reads serial messages from the Arduino
    and converts them into structured event callbacks usable by the kiosk UI.
    """

    def __init__(self, event_callback=None):
        """
        Initialize listener thread and serial connection.

        Args:
            event_callback (callable): Optional callback function to handle parsed events.
                                       It should accept parameters (event_name, event_value).
        """
        self.event_callback = event_callback
        self.running = False
        self.ser = None
        self.thread = None
        self.last_line = ""
        self.connected = False

    # -------------------------------------------------
    # 🔌 SERIAL CONNECTION SETUP
    # -------------------------------------------------
    def connect(self):
        """Attempt to connect to the Arduino via USB serial."""
        try:
            self.ser = serial.Serial(ARDUINO_PORT, ARDUINO_BAUD, timeout=1)
            time.sleep(2)  # Allow Arduino reset after serial connection
            self.connected = True
            logging.info(f"✅ ArduinoListener connected on {ARDUINO_PORT} @ {ARDUINO_BAUD} baud.")
        except serial.SerialException as e:
            logging.error(f"❌ Failed to connect to Arduino: {e}")
            self.connected = False

    # -------------------------------------------------
    # ▶️ START LISTENING THREAD
    # -------------------------------------------------
    def start(self):
        """Start a background thread to continuously read serial data."""
        if not self.connected:
            self.connect()

        if self.connected:
            self.running = True
            self.thread = threading.Thread(target=self._read_loop, daemon=True)
            self.thread.start()
            logging.info("🟢 ArduinoListener started.")
        else:
            logging.warning("⚠️ Arduino not connected, listener not started.")

    # -------------------------------------------------
    # 🛑 STOP LISTENER
    # -------------------------------------------------
    def stop(self):
        """Stop the reading thread and close serial port."""
        self.running = False
        if self.ser and self.ser.is_open:
            self.ser.close()
        logging.info("🔴 ArduinoListener stopped.")

    # -------------------------------------------------
    # 🧠 READ LOOP
    # -------------------------------------------------
    def _read_loop(self):
        """Continuously read from Arduino and parse messages."""
        while self.running:
            try:
                if self.ser.in_waiting > 0:
                    line = self.ser.readline().decode("utf-8", errors="ignore").strip()
                    if line:
                        self.last_line = line
                        self._process_line(line)
            except serial.SerialException as e:
                logging.error(f"⚠️ Serial read error: {e}")
                self.connected = False
                break
            except Exception as e:
                logging.error(f"Unexpected error in ArduinoListener: {e}")
            time.sleep(READ_INTERVAL)

    # -------------------------------------------------
    # 🧩 MESSAGE PARSER
    # -------------------------------------------------
    def _process_line(self, line):
        """
        Parse Arduino serial messages and trigger corresponding events.

        Expected message formats:
        - COIN_INSERTED <peso>
        - COIN_WATER <credit_ml>
        - DISPENSE_START
        - DISPENSE_COMPLETE
        - CUP_DETECTED
        - SYSTEM_RESET
        - FLOW_PULSES <count>
        - [DEBUG] ... (ignored or logged)
        """

        logging.debug(f"[Arduino RAW] {line}")

        # Ignore debug messages
        if line.startswith("[DEBUG]"):
            logging.info(f"Arduino Debug: {line}")
            return

        parts = line.split()
        if not parts:
            return

        event = parts[0].strip()
        value = None

        if len(parts) > 1:
            try:
                value = int(parts[1])
            except ValueError:
                value = parts[1]

        # 🔁 Emit structured event to callback (e.g., WaterScreen)
        if self.event_callback:
            self.event_callback(event, value)
        else:
            logging.info(f"Arduino Event: {event} {value if value else ''}")

    # -------------------------------------------------
    # ⬆️ SEND COMMANDS TO ARDUINO
    # -------------------------------------------------
    def send_command(self, cmd):
        """
        Send a command string to Arduino.
        Used for mode switching, calibration, or reset.

        Example:
            send_command("MODE WATER")
            send_command("RESET")
        """
        if self.ser and self.ser.is_open:
            self.ser.write((cmd + "\n").encode())
            logging.info(f"➡️ Sent command to Arduino: {cmd}")
        else:
            logging.warning("⚠️ Cannot send command — serial not connected.")

    # -------------------------------------------------
    # 🔍 UTILITY
    # -------------------------------------------------
    def is_connected(self):
        """Return True if Arduino is connected."""
        return self.connected

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    def print_event(event, value):
        print(f"[EVENT] {event} - {value}")

    listener = ArduinoListener(event_callback=print_event)
    listener.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        listener.stop()
        print("Stopped.")
