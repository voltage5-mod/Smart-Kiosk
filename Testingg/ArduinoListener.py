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
        """Parse and dispatch Arduino messages with enhanced validation."""
        self.logger.debug(f"[Arduino RAW] {line}")

        # Skip empty lines
        if not line.strip():
            return
 # DEBUG: Log every line to see what's coming through
        print(f"[ARDUINO_DEBUG] {line}")

        # Handle animation start command
        if "ANIMATION_START:" in line:
            try:
                # Extract the ANIMATION_START part only
                anim_part = line.split("ANIMATION_START:")[1]
                # Take only the part before any other text (like "DEBUG")
                anim_part = anim_part.split("DEBUG")[0].strip()
                
                # Now parse the clean animation parameters
                parts = anim_part.split(",")
                if len(parts) >= 2:
                    total_ml = int(parts[0])
                    total_seconds = int(parts[1])
                    
                    self.logger.info(f"Animation start parsed: {total_ml}mL in {total_seconds} seconds")
                    
                    # Send animation parameters
                    animation_data = {
                        "total_ml": total_ml,
                        "total_seconds": total_seconds
                    }
                    self._dispatch_event("animation_start", animation_data, line)
                else:
                    self.logger.warning(f"Invalid ANIMATION_START format: {anim_part}")
                    
                return
            except (ValueError, IndexError, AttributeError) as e:
                self.logger.warning(f"Could not parse animation parameters: {line} - {e}")
                return


        # Handle COIN events - IMPROVED parsing
        if "Coin accepted: pulses=" in line:
            try:
                # More robust parsing that handles different formats
                pulses_match = re.search(r'pulses=(\d+)', line)
                value_match = re.search(r'value=P?(\d+)', line)
                added_match = re.search(r'added=(\d+)', line)
                
                if pulses_match and value_match:
                    pulses = int(pulses_match.group(1))
                    coin_value = int(value_match.group(1))
                    
                    # Enhanced coin validation
                    current_time = time.time()
                    
                    # Check debounce delay
                    if current_time - self.last_coin_time < self.coin_debounce_delay:
                        self.logger.warning(f"Coin debounced: too soon since last coin (P{coin_value})")
                        return
                    
                    # Check rate limiting
                    if self.coin_event_count >= self.max_coin_events_per_second:
                        self.logger.warning(f"Coin rate limited: too many coins per second (P{coin_value})")
                        return
                    
                    # Validate coin value
                    if coin_value not in self.valid_coin_values:
                        self.logger.warning(f"Invalid coin value rejected: P{coin_value}")
                        return
                    
                    # Valid coin detected - update state
                    self.last_coin_time = current_time
                    self.coin_event_count += 1
                    
                    added_ml = 0
                    if added_match:
                        added_ml = int(added_match.group(1))
                    
                    self.logger.info(f"COIN ACCEPTED: P{coin_value}, pulses={pulses}, added={added_ml}mL")
                    
                    # Send simple coin value
                    self._dispatch_event("coin", coin_value, line)
                    
                return
                
            except (ValueError, IndexError, AttributeError) as e:
                self.logger.warning(f"Could not parse coin details from: {line} - {e}")
                return

        # Handle TEST Coin events
        elif "TEST Coin:" in line:
            try:
                # Extract from test coin format using regex
                value_match = re.search(r'value=P?(\d+)', line)
                if value_match:
                    coin_value = int(value_match.group(1))
                    
                    self.logger.info(f"TEST COIN: P{coin_value}")
                    
                    # Send simple coin value
                    self._dispatch_event("coin", coin_value, line)
                    
                return
            except (ValueError, IndexError, AttributeError) as e:
                self.logger.warning(f"Could not parse TEST coin details: {line} - {e}")
                return
        
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