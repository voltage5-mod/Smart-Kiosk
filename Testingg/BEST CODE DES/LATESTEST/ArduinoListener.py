import serial
import threading
import time
import logging
import sys
import os
import re

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
        
        # Add these for duplicate prevention
        self._processed_lines = []  # Track processed message hashes
        self._last_coin_time = 0    # Last coin processing time
        self._last_coin_value = 0   # Last coin value processed
        
        # ... rest of initialization ...

        # Enhanced coin validation state
        self.last_coin_time = 0
        self.coin_debounce_delay = 1.0  # 1 second between coin events
        self.valid_coin_values = [1, 5, 10]  # Only accept these coin values
        self.coin_event_count = 0
        self.max_coin_events_per_second = 2  # Maximum 2 coin events per second
        
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
                self.ser.write(b"STATUS\n")
                time.sleep(0.5)
                
                # Try to read response to verify connection
                if self.ser.in_waiting > 0:
                    test_response = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    self.logger.info(f"Arduino responded: {test_response}")
                else:
                    self.logger.info("Arduino connected (no response to STATUS)")
                
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
        
        # Reset coin event counter every second
        last_reset_time = time.time()
        
        while self.running:
            try:
                # Reset coin event counter every second
                current_time = time.time()
                if current_time - last_reset_time >= 1.0:
                    self.coin_event_count = 0
                    last_reset_time = current_time
                
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
        """Parse Arduino messages - IGNORE ALL DUPLICATES."""
        if not line.strip():
            return
        
        # ========== CRITICAL: Track processed lines to prevent duplicates ==========
        # Initialize if not exists
        if not hasattr(self, '_processed_lines'):
            self._processed_lines = []
        
        # Create a simple hash to detect duplicates (first 40 chars is usually enough)
        line_stripped = line.strip()
        line_hash = hash(line_stripped[:40])
        
        # Check if we've already processed this line
        if line_hash in self._processed_lines:
            self.logger.debug(f"IGNORING DUPLICATE: {line_stripped[:40]}...")
            return
        
        # Store this hash to prevent re-processing
        self._processed_lines.append(line_hash)
        
        # Keep only last 15 lines to avoid memory buildup
        if len(self._processed_lines) > 15:
            self._processed_lines.pop(0)
        
        # ========== ONLY PROCESS THESE SPECIFIC MESSAGE TYPES ==========
        
        # 1. COIN_EVENT: - Process ONLY this format for coins
        if line.startswith("COIN_EVENT:"):
            try:
                # Extract just the number after COIN_EVENT:
                parts = line.split("COIN_EVENT:")
                if len(parts) < 2:
                    return
                
                # Get the number (strip any extra text after it)
                number_part = parts[1].strip()
                # Take only digits (in case there's extra text)
                coin_value = int(''.join(filter(str.isdigit, number_part)))
                
                # Validate it's a real coin value
                if coin_value not in [1, 5, 10]:
                    self.logger.warning(f"Invalid coin value: {coin_value}")
                    return
                
                # Rate limiting: Don't process coins too fast
                current_time = time.time()
                if not hasattr(self, '_last_coin_time'):
                    self._last_coin_time = 0
                
                # Debounce: Minimum 0.3 seconds between coins
                if current_time - self._last_coin_time < 0.3:
                    self.logger.debug(f"DEBOUNCED: Coin P{coin_value} too fast")
                    return
                
                self._last_coin_time = current_time
                
                # FINALLY: Process this single coin event
                self.logger.info(f"PROCESSING COIN: P{coin_value}")
                self._dispatch_event("coin", coin_value, line)
                return
                
            except (ValueError, IndexError, AttributeError) as e:
                self.logger.warning(f"Failed to parse COIN_EVENT: {e} - Line: {line}")
                return
        
        # 2. ANIMATION_START: - Process animations
        elif "ANIMATION_START:" in line:
            try:
                # Extract animation parameters
                anim_part = line.split("ANIMATION_START:")[1]
                
                # Clean up the string (remove debug text that might be appended)
                anim_part = anim_part.split("DEBUG")[0].strip()
                
                # Find the numbers (ml,seconds)
                import re
                numbers = re.findall(r'\d+', anim_part)
                
                if len(numbers) >= 2:
                    total_ml = int(numbers[0])
                    total_seconds = int(numbers[1])
                    
                    animation_data = {
                        "total_ml": total_ml,
                        "total_seconds": total_seconds
                    }
                    
                    self.logger.info(f"ANIMATION: {total_ml}mL in {total_seconds}s")
                    self._dispatch_event("animation_start", animation_data, line)
                else:
                    self.logger.warning(f"Invalid ANIMATION_START format: {line}")
                
                return
                    
            except Exception as e:
                self.logger.warning(f"Failed to parse animation: {e}")
                return
        
        # ========== IGNORE ALL OTHER COIN-RELATED MESSAGES ==========
        # These are the duplicate messages causing double counting:
        
        coin_keywords = [
            "Coin accepted: pulses=",
            "WATER Coin accepted:",
            "CHARGING Coin accepted:",
            "DEBUG: Received",
            "TEST: Detected",
            "TEST Coin:",
            "pulses=",
            "value=P",
            "added=",
            "total=",
            "Coin accepted:",
            "Recognized as"
        ]
        
        # If line contains ANY of these keywords (and isn't COIN_EVENT:), ignore it
        for keyword in coin_keywords:
            if keyword in line:
                self.logger.debug(f"IGNORING DUPLICATE COIN MESSAGE: {line[:50]}...")
                return
        
        # ========== LOG OTHER MESSAGES FOR DEBUGGING ONLY ==========
        
        # Log system status messages
        if any(keyword in line for keyword in ["MODE:", "CREDIT_ML:", "CHARGE_SECONDS:", "FLOW_PULSES:"]):
            self.logger.debug(f"[Arduino Status] {line.strip()}")
            return
        
        # Log debug messages (optional)
        if "DEBUG:" in line:
            self.logger.debug(f"[Arduino Debug] {line.strip()}")
            return
        
        # Log info messages
        if "INFO:" in line or "System Ready" in line or "Dispensing" in line:
            self.logger.info(f"[Arduino] {line.strip()}")
            return
        
        # Log everything else at debug level
        self.logger.debug(f"[Arduino Other] {line.strip()}")
        
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

    def reset_coin_debounce(self):
        """Reset the coin debounce timer (useful for testing)."""
        self.last_coin_time = 0
        self.coin_event_count = 0
        self.logger.info("Coin debounce timer reset")


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
            listener.send_command("TEST_COIN_1")
            time.sleep(1)
            listener.send_command("TEST_COIN_5")
            time.sleep(1)
            listener.send_command("TEST_COIN_10")
            
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