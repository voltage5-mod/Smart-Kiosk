import serial
import threading
import time
import logging
import sys
import os

# ----------------- CONFIGURATION -----------------
ARDUINO_PORTS = [
    "/dev/ttyUSB0",
    "/dev/ttyUSB1",
    "/dev/ttyACM0",
    "/dev/ttyACM1",
    "/dev/ttyS0",
    "COM3",
    "COM4",
    "COM5",
    "COM6"
]

ARDUINO_BAUD = 115200
READ_INTERVAL = 0.05  # read loop speed


# -------------------------------------------------
# NEAREST-MATCH COIN MAPPING (fixes 5→10 misdetection)
# -------------------------------------------------
COIN_PULSE_MAP = {1: 1, 3: 5, 5: 10}
COIN_TOLERANCE = 1   # pulses may vary ±1 on some acceptors


def map_pulses_to_coin(pulses):
    """
    Convert pulse counts to nearest-match coin value (₱1, ₱5, ₱10)
    Prevents 5 peso being detected as 10.
    """
    if pulses <= 0 or pulses > 12:
        return None  # noise

    # Find nearest pulse key
    nearest = min(COIN_PULSE_MAP.keys(), key=lambda k: abs(k - pulses))

    # Reject if too far from known pulse patterns
    if abs(nearest - pulses) > COIN_TOLERANCE:
        return None

    # Break ties by choosing lower value (avoid misclassifying ₱5 as ₱10)
    candidates = []
    for key in COIN_PULSE_MAP.keys():
        if abs(key - pulses) <= COIN_TOLERANCE:
            candidates.append((key, COIN_PULSE_MAP[key]))

    if not candidates:
        return None

    # Pick the smallest denomination among candidates
    candidates.sort(key=lambda x: x[1])
    return candidates[0][1]


# -------------------------------------------------
class ArduinoListener:
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

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler('/tmp/arduino_listener.log')
            ]
        )
        self.logger = logging.getLogger("ArduinoListener")

    # -------------------------------------------------
    def register_callback(self, fn):
        if fn not in self.callbacks:
            self.callbacks.append(fn)
            self.logger.info(f"Registered callback: {fn}")

    def unregister_callback(self, fn):
        if fn in self.callbacks:
            self.callbacks.remove(fn)
            self.logger.info(f"Unregistered callback: {fn}")

    # -------------------------------------------------
    # CONNECT TO ARDUINO
    # -------------------------------------------------
    def connect(self):
        if self.connected and self.ser and self.ser.is_open:
            return True

        for port in self.port_candidates:
            try:
                self.logger.info(f"Trying {port}...")
                self.ser = serial.Serial(port, self.baud_rate, timeout=1)
                time.sleep(2)  # allow Arduino to reset
                self.ser.reset_input_buffer()

                self.ser.write(b"PING\n")
                time.sleep(0.5)

                if self.ser.in_waiting > 0:
                    resp = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    self.logger.info(f"Arduino replied: {resp}")

                self.connected = True
                self.actual_port = port
                self.logger.info(f"Connected on {port}")
                return True

            except Exception as e:
                self.logger.debug(f"Failed on {port}: {e}")

        self.logger.error("Could not connect to Arduino.")
        return False

    # -------------------------------------------------
    def start(self):
        if not self.connected:
            if not self.connect():
                return False

        if self.running:
            return True

        self.running = True
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

        self.logger.info("ArduinoListener started.")
        return True

    # -------------------------------------------------
    def stop(self):
        self.running = False

        try:
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=1)
        except:
            pass

        if self.ser:
            try:
                self.ser.close()
            except:
                pass

        self.connected = False
        self.logger.info("ArduinoListener stopped.")

    # -------------------------------------------------
    # READ LOOP
    # -------------------------------------------------
    def _read_loop(self):
        self.logger.info("Listening for Arduino messages...")

        while self.running:
            try:
                if self.ser and self.ser.in_waiting > 0:
                    line = self.ser.readline().decode("utf-8", errors="ignore").strip()
                    if line:
                        self._process_line(line)

                time.sleep(READ_INTERVAL)

            except Exception as e:
                self.logger.error(f"Read error: {e}")
                self.connected = False
                self._attempt_reconnect()

    def _attempt_reconnect(self):
        time.sleep(2)
        self.connect()

    # -------------------------------------------------
    # PARSE INCOMING LINES
    # -------------------------------------------------
    def _process_line(self, line):
        self.logger.debug(f"RAW: {line}")

        # ---------------- COIN EVENTS ----------------
        if "Coin accepted: pulses=" in line:
            try:
                raw = int(line.split("=")[1].strip())
                coin_val = map_pulses_to_coin(raw)

                if coin_val is None:
                    self.logger.info(f"Ignored noisy coin pulses ({raw})")
                    return

                self.logger.info(f"COIN DETECTED → ₱{coin_val}")
                self._dispatch("coin", coin_val, line)
                return

            except Exception as e:
                self.logger.error(f"Failed parsing coin pulses: {e}")
                return

        # ---------------- CUP ----------------
        if line.startswith("CUP_DETECTED"):
            self._dispatch("cup_detected", True, line)
            return

        # ---------------- COUNTDOWN ----------------
        if line.startswith("COUNTDOWN "):
            try:
                val = int(line.split()[1])
                self._dispatch("countdown", val, line)
            except:
                pass
            return

        if line.startswith("COUNTDOWN_END"):
            self._dispatch("countdown_end", True, line)
            return

        # ---------------- DISPENSE START/END ----------------
        if line.startswith("DISPENSE_START"):
            self._dispatch("dispense_start", True, line)
            return

        if line.startswith("DISPENSE_DONE"):
            try:
                ml = float(line.split()[1])
                self._dispatch("dispense_done", ml, line)
            except:
                pass
            return

        # ---------------- SYSTEM READY ----------------
        if "System Ready" in line:
            self._dispatch("system_ready", True, line)
            return

        # Unhandled but logged
        self.logger.info(f"UNHANDLED: {line}")

    # -------------------------------------------------
    def _dispatch(self, event, value, raw):
        payload = {
            "event": event,
            "value": value,
            "raw": raw,
            "timestamp": time.time()
        }

        # Main callback
        if self.event_callback:
            try:
                self.event_callback(event, value)
            except Exception as e:
                self.logger.error(f"Main callback error: {e}")

        # Extra UI callbacks
        for cb in self.callbacks:
            try:
                cb(payload)
            except Exception as e:
                self.logger.error(f"Callback error: {e}")

    # -------------------------------------------------
    def send_command(self, cmd):
        if not self.ser or not self.ser.is_open:
            self.logger.warning("Cannot send command — serial not open.")
            return False

        try:
            self.ser.write((cmd + "\n").encode())
            return True
        except Exception as e:
            self.logger.error(f"Send error: {e}")
            return False

    # -------------------------------------------------
    def is_connected(self):
        return self.connected

    def get_port(self):
        return self.actual_port
