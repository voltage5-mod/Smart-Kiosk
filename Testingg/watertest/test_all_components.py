# test_all_components.py
# Dynamic hardware test for Smart Kiosk based on pinmap.json

import json
import time
import board
import busio
import RPi.GPIO as GPIO
from adafruit_mcp230xx.mcp23017 import MCP23017
import digitalio

# ----------------------------
# Load Pin Configuration
# ----------------------------
with open("pinmap.json") as f:
    config = json.load(f)

rpi_pins = config["raspberry_pi"]
mcp_pins = config["mcp23017_expander"]["pins"]

# ----------------------------
# Initialize Raspberry Pi GPIO
# ----------------------------
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# --- Coin Slot ---
coin_pin = rpi_pins["coin_acceptor"]["signal"]
GPIO.setup(coin_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# --- Ultrasonic ---
ultra_trig = rpi_pins["ultrasonic_sensor"]["trig"]
ultra_echo = rpi_pins["ultrasonic_sensor"]["echo"]
GPIO.setup(ultra_trig, GPIO.OUT)
GPIO.setup(ultra_echo, GPIO.IN)

# --- Relays (Charging Slots) ---
relays = {}
for name, pin in rpi_pins["relays"].items():
    GPIO.setup(pin, GPIO.OUT)
    GPIO.output(pin, GPIO.LOW)
    relays[name] = pin

# ----------------------------
# Initialize MCP23017
# ----------------------------
print("Initializing I2C bus...")
i2c = busio.I2C(board.SCL, board.SDA)
mcp = MCP23017(i2c, address=int(config["mcp23017_expander"]["address"], 16))
print("MCP23017 connected successfully.")

# Map MCP pins
def get_pin(name):
    for k, v in mcp_pins.items():
        if v["function"] == name:
            return int(k.replace("GPA", "").replace("GPB", "")) + (0 if "GPA" in k else 8)
    return None

# MCP pin references
flow_pin = mcp.get_pin(get_pin("flow_sensor_signal"))
low_pin = mcp.get_pin(get_pin("tank_level_low"))
high_pin = mcp.get_pin(get_pin("tank_level_high"))
pump_pin = mcp.get_pin(get_pin("pump_relay"))
valve_pin = mcp.get_pin(get_pin("solenoid_valve_relay"))

# Configure MCP directions
for p in [flow_pin, low_pin, high_pin]:
    p.direction = digitalio.Direction.INPUT
    p.pull_up = True
for p in [pump_pin, valve_pin]:
    p.direction = digitalio.Direction.OUTPUT
    p.value = False

print("Pin configuration completed.")

# ----------------------------
# Helper Functions
# ----------------------------
def test_relays():
    print("\n--- RELAY TEST (Pump + Valve) ---")
    print("Pump ON")
    pump_pin.value = True
    time.sleep(1)
    print("Valve ON")
    valve_pin.value = True
    time.sleep(1)
    print("Pump OFF, Valve OFF")
    pump_pin.value = False
    valve_pin.value = False
    print("--- Relay Test Complete ---")

def test_ultrasonic():
    print("\n--- ULTRASONIC TEST ---")
    for _ in range(5):
        GPIO.output(ultra_trig, True)
        time.sleep(0.00001)
        GPIO.output(ultra_trig, False)

        start_time = time.time()
        stop_time = time.time()

        while GPIO.input(ultra_echo) == 0:
            start_time = time.time()
        while GPIO.input(ultra_echo) == 1:
            stop_time = time.time()

        time_elapsed = stop_time - start_time
        distance = (time_elapsed * 34300) / 2
        print(f"Distance: {distance:.2f} cm")
        time.sleep(1)
    print("--- Ultrasonic Test Complete ---")

def test_flow_sensor(duration=10):
    print("\n--- FLOW SENSOR TEST ---")
    flow_count = 0
    last_state = flow_pin.value
    start_time = time.time()

    while time.time() - start_time < duration:
        current = flow_pin.value
        if (not last_state) and current:
            flow_count += 1
        last_state = current
        time.sleep(0.002)

    print(f"Total flow pulses detected: {flow_count}")
    print("--- Flow Test Complete ---")

def test_tank_levels():
    print("\n--- TANK LEVEL SENSORS TEST ---")
    low = not low_pin.value
    high = not high_pin.value
    print(f"Low Level:  {'ACTIVE' if low else 'INACTIVE'}")
    print(f"High Level: {'ACTIVE' if high else 'INACTIVE'}")
    print("--- Tank Level Test Complete ---")

def test_coin_slot(duration=10):
    print("\n--- COIN ACCEPTOR TEST ---")
    print(f"Waiting {duration}s for coin pulses on GPIO{coin_pin}...")
    count = 0
    last = GPIO.input(coin_pin)
    start = time.time()

    while time.time() - start < duration:
        now = GPIO.input(coin_pin)
        if last == 1 and now == 0:
            count += 1
            print(f"Pulse {count} detected")
        last = now
        time.sleep(0.002)

    print(f"Total pulses detected: {count}")
    print("--- Coin Slot Test Complete ---")

# ----------------------------
# MAIN TEST MENU
# ----------------------------
def main():
    try:
        print("\n===== SMART KIOSK COMPONENT TEST =====")
        print("1. Test Relays (Pump + Valve)")
        print("2. Test Ultrasonic Sensor")
        print("3. Test Flow Sensor")
        print("4. Test Tank Level Sensors")
        print("5. Test Coin Acceptor")
        print("6. Exit\n")

        while True:
            choice = input("Enter choice [1-6]: ").strip()
            if choice == "1":
                test_relays()
            elif choice == "2":
                test_ultrasonic()
            elif choice == "3":
                test_flow_sensor()
            elif choice == "4":
                test_tank_levels()
            elif choice == "5":
                test_coin_slot()
            elif choice == "6":
                break
            else:
                print("Invalid choice, please enter 1–6.")

    except KeyboardInterrupt:
        print("\nTest aborted by user.")
    finally:
        print("Turning off all outputs...")
        pump_pin.value = False
        valve_pin.value = False
        for pin in relays.values():
            GPIO.output(pin, GPIO.LOW)
        GPIO.cleanup()
        print("All components OFF. Exiting cleanly.")

# ----------------------------
# Run
# ----------------------------
if __name__ == "__main__":
    main()
