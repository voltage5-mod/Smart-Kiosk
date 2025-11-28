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
import sys
import os

# ----------------- CONFIGURATION -----------------
# Common Arduino ports - will try each in order
ARDUINO_PORTS = [
    "/dev/ttyUSB0",    # Most common for USB-serial adapters
    "/dev/ttyUSB1", 
    "/dev/ttyACM0",    # Common for genuine Arduino Uno
    "/dev/ttyACM1",
    "/dev/ttyS0",      # Raspberry Pi GPIO serial
    "COM3",            # Windows
    "COM4",
    "COM5",
    "COM6"
]
ARDUINO_BAUD = 115200
READ_INTERVAL = 0.05  # seconds between read cycles

# -------------------------------------------------

class ArduinoListener:
    """
    ArduinoListener continuously reads serial messages from the Arduino
    and converts them into structured event callbacks usable by the kiosk UI.
    """

    def __init__(self, event_callback=None, port_candidates=None, baud_rate=115200):
        self.event_callback = event_callback
        self.baud_rate = baud_rate
        self.port_candidates = port_candidates or ARDUINO_PORTS
        self.running = False
        self.ser = None
        self.thread = None
        self.connected = False
        self.callbacks = []
        self.actual_port = None
        
        # Set up logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler('/tmp/arduino_listener.log')
            ]
        )
        self.logger = logging.getLogger('ArduinoListener')

    def register_callback(self, fn):
        """Allow UI screens to attach additional listeners."""
        if fn not in self.callbacks:
            self.callbacks.append(fn)
            self.logger.info(f"Registered callback: {fn.__name__ if hasattr(fn, '__name__') else 'anonymous'}")
        else:
            self.logger.warning("Callback already registered")

    def unregister_callback(self, fn):
        """Remove a callback."""
        if fn in self.callbacks:
            self.callbacks.remove(fn)
            self.logger.info(f"Unregistered callback: {fn.__name__ if hasattr(fn, '__name__') else 'anonymous'}")

    # -------------------------------------------------
    # 🔌 SERIAL CONNECTION SETUP
    # -------------------------------------------------
    def connect(self):
        """Attempt to connect to the Arduino via USB serial."""
        if self.connected and self.ser and self.ser.is_open:
            self.logger.info("Already connected to Arduino")
            return True
            
        # Try each port candidate
        for port in self.port_candidates:
            try:
                self.logger.info(f"Trying to connect to {port}...")
                self.ser = serial.Serial(port, self.baud_rate, timeout=1)
                time.sleep(2)  # Allow Arduino reset after serial connection
                
                # Test communication by sending a status request
                self.ser.write(b"STATUS\n")
                time.sleep(0.5)
                
                # Try to read response to verify connection
                if self.ser.in_waiting > 0:
                    test_response = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    self.logger.info(f"Arduino responded: {test_response}")
                
                self.connected = True
                self.actual_port = port
                self.logger.info(f"✅ ArduinoListener connected on {port} @ {self.baud_rate} baud")
                return True
                
            except serial.SerialException as e:
                self.logger.debug(f"Failed to connect to {port}: {e}")
                continue
            except Exception as e:
                self.logger.debug(f"Unexpected error with {port}: {e}")
                continue
        
        # If we get here, no ports worked
        self.logger.error("❌ Failed to connect to Arduino on any port")
        self.connected = False
        return False

    # -------------------------------------------------
    # ▶️ START LISTENING THREAD
    # -------------------------------------------------
    def start(self):
        """Start a background thread to continuously read serial data."""
        if not self.connected:
            if not self.connect():
                self.logger.error("Cannot start listener - no Arduino connection")
                return False

        if self.connected and not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._read_loop, daemon=True)
            self.thread.start()
            self.logger.info("🟢 ArduinoListener started and running")
            return True
        else:
            self.logger.warning("Listener already running or not connected")
            return False

    # -------------------------------------------------
    # 🛑 STOP LISTENER
    # -------------------------------------------------
    def stop(self):
        """Stop the reading thread and close serial port."""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
                self.logger.info("Serial port closed")
            except Exception as e:
                self.logger.error(f"Error closing serial port: {e}")
        
        self.connected = False
        self.logger.info("🔴 ArduinoListener stopped completely")

    # -------------------------------------------------
    # 🧠 READ LOOP
    # -------------------------------------------------
    def _read_loop(self):
        """Continuously read from Arduino and parse messages."""
        self.logger.info("Starting Arduino read loop...")
        
        while self.running:
            try:
                if self.ser and self.ser.is_open and self.ser.in_waiting > 0:
                    line = self.ser.readline().decode("utf-8", errors="ignore").strip()
                    if line:
                        self._process_line(line)
                time.sleep(READ_INTERVAL)
                
            except serial.SerialException as e:
                self.logger.error(f"⚠️ Serial read error: {e}")
                self.connected = False
                # Try to reconnect
                self._attempt_reconnect()
                
            except Exception as e:
                self.logger.error(f"Unexpected error in ArduinoListener: {e}")
                time.sleep(1)  # Prevent tight loop on errors

    def _attempt_reconnect(self):
        """Attempt to reconnect to Arduino."""
        self.logger.info("Attempting to reconnect to Arduino...")
        retries = 3
        for attempt in range(retries):
            try:
                if self.connect():
                    self.logger.info("✅ Reconnected to Arduino successfully")
                    return True
                time.sleep(2)  # Wait before retry
            except Exception as e:
                self.logger.warning(f"Reconnection attempt {attempt + 1} failed: {e}")
        
        self.logger.error("❌ Failed to reconnect to Arduino after multiple attempts")
        return False

    # -------------------------------------------------
    # 🧩 MESSAGE PARSER
    # -------------------------------------------------
    def _process_line(self, line):
        """Parse and dispatch Arduino messages."""
        self.logger.debug(f"[Arduino RAW] {line}")

        # Skip debug lines (optional)
        if line.startswith("[DEBUG]") or line.startswith("DEBUG:"):
            self.logger.info(f"Arduino Debug: {line}")
            return

        # Handle special MODE command format
        if line.startswith("MODE:"):
            event = "MODE"
            value = line.split(":", 1)[1].strip()
            self._dispatch_event(event, value, line)
            return

        parts = line.split()
        if not parts:
            return

        event = parts[0].strip()
        value = None

        if len(parts) > 1:
            # Try to convert to number if possible
            try:
                value = int(parts[1])
            except ValueError:
                try:
                    value = float(parts[1])
                except ValueError:
                    value = parts[1]  # Keep as string

        # Dispatch the event
        self._dispatch_event(event, value, line)

    def _dispatch_event(self, event, value, raw_line):
        """Dispatch event to all registered callbacks."""
        payload = {
            "event": event,
            "value": value,
            "raw": raw_line,
            "timestamp": time.time()
        }

        # Send to main event callback (KioskApp)
        if self.event_callback:
            try:
                self.event_callback(event, value)
            except Exception as e:
                self.logger.error(f"Error in main event_callback: {e}")

        # Send to additional UI callbacks (e.g., WaterScreen)
        for callback in self.callbacks[:]:  # Use slice to avoid modification during iteration
            try:
                callback(payload)
            except Exception as e:
                self.logger.error(f"Error in callback {callback}: {e}")

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
            send_command("STATUS")
        """
        if not self.ser or not self.ser.is_open:
            self.logger.warning("⚠️ Cannot send command — serial not connected.")
            return False

        try:
            full_cmd = cmd + "\n"
            self.ser.write(full_cmd.encode())
            self.logger.info(f"➡️ Sent command to Arduino: {cmd}")
            return True
        except Exception as e:
            self.logger.error(f"❌ Error sending command to Arduino: {e}")
            return False

    # -------------------------------------------------
    # 🔍 UTILITY METHODS
    # -------------------------------------------------
    def is_connected(self):
        """Return True if Arduino is connected."""
        return self.connected and self.ser and self.ser.is_open

    def get_port(self):
        """Return the actual port being used."""
        return self.actual_port

    def get_status(self):
        """Return connection status information."""
        return {
            "connected": self.connected,
            "running": self.running,
            "port": self.actual_port,
            "baud_rate": self.baud_rate,
            "callbacks_registered": len(self.callbacks)
        }

    def reset_connection(self):
        """Reset the serial connection."""
        self.stop()
        time.sleep(1)
        return self.connect() and self.start()


# -------------------------------------------------
# 🧪 TEST FUNCTION
# -------------------------------------------------
def test_arduino_listener():
    """Test function to verify Arduino communication."""
    
    def test_callback(event, value):
        print(f"[TEST CALLBACK] Event: {event}, Value: {value}")
    
    def test_payload_callback(payload):
        print(f"[TEST PAYLOAD] {payload}")
    
    print("🧪 Testing ArduinoListener...")
    
    listener = ArduinoListener(event_callback=test_callback)
    listener.register_callback(test_payload_callback)
    
    if listener.connect():
        print("✅ Connected to Arduino")
        if listener.start():
            print("✅ Listener started")
            
            # Test sending commands
            listener.send_command("STATUS")
            listener.send_command("MODE WATER")
            
            # Run for 30 seconds to capture events
            print("📡 Listening for Arduino events for 30 seconds...")
            try:
                for i in range(30):
                    time.sleep(1)
                    print(f"⏰ {29-i} seconds remaining...")
            except KeyboardInterrupt:
                print("🛑 Stopped by user")
            
            listener.stop()
            print("✅ Test completed")
        else:
            print("❌ Failed to start listener")
    else:
        print("❌ Failed to connect to Arduino")


if __name__ == "__main__":
    test_arduino_listener()