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
        
        # Set up logging without Unicode emojis
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
    # SERIAL CONNECTION SETUP
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
                
                # Clear any existing data
                self.ser.reset_input_buffer()
                
                # Test communication by sending a status request
                self.ser.write(b"PING\n")
                time.sleep(0.5)
                
                # Try to read response to verify connection
                if self.ser.in_waiting > 0:
                    test_response = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    self.logger.info(f"Arduino responded: {test_response}")
                else:
                    self.logger.info("Arduino connected (no response to PING)")
                
                self.connected = True
                self.actual_port = port
                self.logger.info(f"SUCCESS: ArduinoListener connected on {port} @ {self.baud_rate} baud")
                return True
                
            except (serial.SerialException, OSError) as e:
                self.logger.debug(f"Failed to connect to {port}: {e}")
                continue
            except Exception as e:
                self.logger.debug(f"Unexpected error with {port}: {e}")
                continue
        
        # If we get here, no ports worked
        self.logger.error("ERROR: Failed to connect to Arduino on any port")
        self.connected = False
        return False

    # -------------------------------------------------
    # START LISTENING THREAD
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
            self.logger.info("STARTED: ArduinoListener started and running")
            return True
        else:
            self.logger.warning("Listener already running or not connected")
            return False

    # --------------------------------------------------------
    # Stop Listener
    # --------------------------------------------------------
    def stop(self):
        """Stop the listener and close serial connection."""
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
        self.logger.info("STOPPED: ArduinoListener stopped completely")

    # -------------------------------------------------
    # READ LOOP
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
                self.logger.error(f"Serial read error: {e}")
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
                    self.logger.info("SUCCESS: Reconnected to Arduino successfully")
                    return True
                time.sleep(2)  # Wait before retry
            except Exception as e:
                self.logger.warning(f"Reconnection attempt {attempt + 1} failed: {e}")
        
        self.logger.error("ERROR: Failed to reconnect to Arduino after multiple attempts")
        return False

    # -------------------------------------------------
    # MESSAGE PARSER
    # -------------------------------------------------
    def _process_line(self, line):
        """Parse and dispatch Arduino messages."""
        self.logger.debug(f"[Arduino RAW] {line}")

        # Handle ultrasonic debug messages - IGNORE these to reduce spam
        if line.startswith("[DEBUG] Ultrasonic distance:"):
            # Only log occasionally to reduce spam
            if random.random() < 0.1:  # Log only 10% of ultrasonic messages
                self.logger.debug(f"ULTRASONIC: {line}")
            return
            
        if "ultrasonic:" in line.lower():
            # Skip these entirely - they're confusing the event parser
            return
    
        # Handle CUP_DETECTED events
        if line.startswith("CUP_DETECTED"):
            self.logger.info("CUP DETECTED - Dispensing should start")
            event = "cup_detected"
            value = True
            self._dispatch_event(event, value, line)
            return
            
        if line.startswith("CUP_REMOVED"):
            self.logger.info("CUP REMOVED - Dispensing stopped")
            event = "cup_removed" 
            value = True
            self._dispatch_event(event, value, line)
            return

        # Handle DEBUG messages for cup sensor
        if "[DEBUG] Cup detected:" in line:
            self.logger.info(f"CUP SENSOR STATUS: {line}")
            return
            
        if "[DEBUG] Ultrasonic duration:" in line:
            self.logger.info(f"ULTRASONIC RAW: {line}")
            return
            
        if "[DEBUG] Calculated distance:" in line:
            self.logger.info(f"DISTANCE: {line}")
            return

        # Handle COIN_INSERTED events (highest priority)
        if line.startswith("COIN_INSERTED"):
            try:
                parts = line.split()
                if len(parts) >= 2:
                    coin_value = int(parts[1])
                    event = "coin"
                    value = coin_value
                    self._dispatch_event(event, value, line)
                    return
            except (ValueError, IndexError) as e:
                self.logger.warning(f"Could not parse COIN_INSERTED value from: {line}")
                return

        # Handle COIN_WATER events  
        if line.startswith("COIN_WATER"):
            try:
                parts = line.split()
                if len(parts) >= 2:
                    ml_value = int(parts[1])
                    event = "coin_water"
                    value = ml_value
                    self._dispatch_event(event, value, line)
                    return
            except (ValueError, IndexError) as e:
                self.logger.warning(f"Could not parse COIN_WATER value from: {line}")
                return

        # Handle COIN_CHARGE events (charging credit in pesos)
        if line.startswith("COIN_CHARGE"):
            try:
                parts = line.split()
                if len(parts) >= 2:
                    peso_value = int(parts[1])
                    event = "coin_charge"
                    value = peso_value
                    self._dispatch_event(event, value, line)
                    return
            except (ValueError, IndexError) as e:
                self.logger.warning(f"Could not parse COIN_CHARGE value from: {line}")
                return

        # Handle DISPENSE_START events
        if line.startswith("DISPENSE_START"):
            event = "dispense_start"
            value = True
            self._dispatch_event(event, value, line)
            return
            
        # Handle DISPENSE_DONE events
        if line.startswith("DISPENSE_DONE"):
            try:
                parts = line.split()
                if len(parts) >= 2:
                    ml_dispensed = float(parts[1])
                    event = "dispense_done"
                    value = ml_dispensed
                    self._dispatch_event(event, value, line)
                    return
            except (ValueError, IndexError) as e:
                self.logger.warning(f"Could not parse DISPENSE_DONE value from: {line}")
                return

        # Handle CREDIT_LEFT events
        if line.startswith("CREDIT_LEFT"):
            try:
                parts = line.split()
                if len(parts) >= 2:
                    remaining_ml = float(parts[1])
                    event = "credit_left"
                    value = remaining_ml
                    self._dispatch_event(event, value, line)
                    return
            except (ValueError, IndexError) as e:
                self.logger.warning(f"Could not parse CREDIT_LEFT value from: {line}")
                return

        # Handle DISPENSE_PROGRESS events
        if line.startswith("DISPENSE_PROGRESS"):
            try:
                # Format: "DISPENSE_PROGRESS ml=300.0 remaining=200.0"
                import re
                ml_match = re.search(r'ml=([\d.]+)', line)
                remaining_match = re.search(r'remaining=([\d.]+)', line)
                
                if ml_match and remaining_match:
                    ml_dispensed = float(ml_match.group(1))
                    ml_remaining = float(remaining_match.group(1))
                    event = "dispense_progress"
                    value = {"dispensed": ml_dispensed, "remaining": ml_remaining}
                    self._dispatch_event(event, value, line)
                    return
            except (ValueError, IndexError) as e:
                self.logger.warning(f"Could not parse DISPENSE_PROGRESS value from: {line}")
                return

        # Handle MODE command format
        if line.startswith("MODE:"):
            event = "mode"
            value = line.split(":", 1)[1].strip()
            self._dispatch_event(event, value, line)
            return

        # Handle CALIBRATION events
        if line.startswith("CAL_DONE"):
            event = "calibration_done"
            value = line
            self._dispatch_event(event, value, line)
            return

        # Handle SYSTEM events
        if line.startswith("System Ready") or line.startswith("System reset"):
            event = "system_ready"
            value = True
            self._dispatch_event(event, value, line)
            return

        # Handle debug messages with coin detection (legacy format)
        if "Coin detected" in line or "new credit:" in line:
            self.logger.info(f"Arduino Debug: {line}")
            
            # Try to extract coin value from various debug formats
            try:
                import re
                # Format: "Coin detected, new credit: 300 ml"
                credit_match = re.search(r'credit:\s*(\d+)', line)
                if credit_match:
                    credit_ml = int(credit_match.group(1))
                    self.logger.info(f"Credit updated to {credit_ml}mL from debug message")
                    
                    # Try to determine coin value from credit amount
                    coin_value = None
                    if credit_ml % 50 == 0:  # 1 peso = 50ml
                        coin_value = 1
                    elif credit_ml % 250 == 0:  # 5 peso = 250ml  
                        coin_value = 5
                    elif credit_ml % 500 == 0:  # 10 peso = 500ml
                        coin_value = 10
                        
                    if coin_value:
                        event = "coin"
                        value = coin_value
                        self._dispatch_event(event, value, line)
                        return
                        
            except Exception as e:
                self.logger.debug(f"Could not extract coin from debug: {e}")
            return

        # Handle other COIN events with better detection (legacy format)
        if "COIN" in line.upper() and not line.startswith("COIN_INSERTED") and not line.startswith("COIN_WATER") and not line.startswith("COIN_CHARGE"):
            # Extract coin value - handle various formats
            coin_value = None
            try:
                if ":" in line:
                    # Format: "COIN:10" or "COIN: 10"
                    value_str = line.split(":", 1)[1].strip()
                    coin_value = int(value_str)
                else:
                    # Format: "COIN 10" or just "10" (if COIN is implied)
                    parts = line.split()
                    if len(parts) > 1:
                        coin_value = int(parts[1])
                    else:
                        coin_value = 1  # Default coin value
                        
                event = "coin"
                value = coin_value
                self._dispatch_event(event, value, line)
                return
                
            except (ValueError, IndexError) as e:
                self.logger.warning(f"Could not parse coin value from: {line}")
                # Still dispatch as coin event with None value
                event = "coin"
                value = None
                self._dispatch_event(event, value, line)
                return

        # Skip other debug lines but log them
        if line.startswith("[DEBUG]") or line.startswith("DEBUG:") or line.startswith("Calibrating") or line.startswith("FLOW CALIBRATION"):
            self.logger.info(f"Arduino Debug: {line}")
            return

        # Parse generic events (fallback)
        parts = line.split()
        if not parts:
            return

        event = parts[0].strip().lower()  # Convert to lowercase for consistency
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
    # SEND COMMANDS TO ARDUINO
    # -------------------------------------------------
    def send_command(self, cmd):
        """
        Send a command string to Arduino.
        
        Example:
            send_command("MODE WATER")
            send_command("RESET")
            send_command("STATUS")
        """
        if not self.ser or not self.ser.is_open:
            self.logger.warning("WARNING: Cannot send command - serial not connected.")
            return False

        try:
            full_cmd = cmd + "\n"
            self.ser.write(full_cmd.encode())
            self.logger.info(f"SENT: Command to Arduino: {cmd}")
            return True
        except Exception as e:
            self.logger.error(f"ERROR: Error sending command to Arduino: {e}")
            return False

    # -------------------------------------------------
    # UTILITY METHODS
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

    def set_callback(self, callback):
        """Alternative method to set the main event callback."""
        self.event_callback = callback
        self.logger.info("Main event callback set")

    def write(self, command):
        """Alternative method name for send_command for compatibility."""
        return self.send_command(command)


# -------------------------------------------------
# TEST FUNCTION
# -------------------------------------------------
def test_arduino_listener():
    """Test function to verify Arduino communication."""
    
    def test_callback(event, value):
        print(f"[TEST CALLBACK] Event: {event}, Value: {value}")
    
    def test_payload_callback(payload):
        print(f"[TEST PAYLOAD] {payload}")
    
    print("TESTING: Testing ArduinoListener...")
    
    listener = ArduinoListener(event_callback=test_callback)
    listener.register_callback(test_payload_callback)
    
    if listener.connect():
        print("SUCCESS: Connected to Arduino")
        if listener.start():
            print("SUCCESS: Listener started")
            
            # Test sending commands
            listener.send_command("STATUS")
            listener.send_command("MODE WATER")
            
            # Run for 30 seconds to capture events
            print("LISTENING: Listening for Arduino events for 30 seconds...")
            try:
                for i in range(30):
                    time.sleep(1)
                    print(f"TIME: {29-i} seconds remaining...")
            except KeyboardInterrupt:
                print("STOPPED: Stopped by user")
            
            listener.stop()
            print("COMPLETED: Test completed")
        else:
            print("ERROR: Failed to start listener")
    else:
        print("ERROR: Failed to connect to Arduino")


if __name__ == "__main__":
    test_arduino_listener()