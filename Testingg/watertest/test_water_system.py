# test_water_system_auto.py
# Automatic water subsystem tester for Smart Kiosk using pinmap.json

import json
import time
import board
import busio
import digitalio
from adafruit_mcp230xx.mcp23017 import MCP23017

# ----------------------------
# Load Pin Configuration from pinmap.json
# ----------------------------
try:
    with open("pinmap.json") as f:
        config = json.load(f)
    print("✅ Loaded pinmap.json successfully.")
except FileNotFoundError:
    print("❌ pinmap.json not found. Please place it in the same folder as this script.")
    exit(1)

# MCP23017 configuration
mcp_config = config["mcp23017_expander"]
address = int(mcp_config["address"], 16)
pins = mcp_config["pins"]

# ----------------------------
# Initialize MCP23017 via I2C
# ----------------------------
print("🔌 Initializing I2C bus...")
i2c = busio.I2C(board.SCL, board.SDA)
mcp = MCP23017(i2c, address=address)
print(f"✅ MCP23017 detected at address {hex(address)}")

# ----------------------------
# Helper function to locate pin by function name
# ----------------------------
def find_pin(function_name):
    for pin_name, details in pins.items():
        if details.get("function") == function_name:
            # convert to 0–15 range
            if pin_name.startswith("GPA"):
                return int(pin_name.replace("GPA", ""))
            elif pin_name.startswith("GPB"):
                return int(pin_name.replace("GPB", "")) + 8
    return None

# ----------------------------
# Assign MCP pins using JSON mapping
# ----------------------------
flow_pin_num = find_pin("flow_sensor_signal")
low_pin_num = find_pin("tank_level_low")
high_pin_num = find_pin("tank_level_high")
pump_pin_num = find_pin("pump_relay")
valve_pin_num = find_pin("solenoid_valve_relay")

if None in [flow_pin_num, low_pin_num, high_pin_num, pump_pin_num, valve_pin_num]:
    print("❌ Missing one or more pin mappings in pinmap.json.")
    exit(1)

# Initialize MCP pins
flow_pin = mcp.get_pin(flow_pin_num)
level_low_pin = mcp.get_pin(low_pin_num)
level_high_pin = mcp.get_pin(high_pin_num)
pump_pin = mcp.get_pin(pump_pin_num)
valve_pin = mcp.get_pin(valve_pin_num)

# Configure directions
for p in [flow_pin, level_low_pin, level_high_pin]:
    p.direction = digitalio.Direction.INPUT
    p.pull_up = True

for p in [pump_pin, valve_pin]:
    p.direction = digitalio.Direction.OUTPUT
    p.value = False

print("✅ Water system pins configured successfully.\n")

# ----------------------------
# Component Test Functions
# ----------------------------
def test_relays():
    print("--- RELAY TEST ---")
    print("Turning ON pump relay...")
    pump_pin.value = True
    time.sleep(1)
    print("Turning ON solenoid valve relay...")
    valve_pin.value = True
    time.sleep(1)

    print("Turning OFF pump relay...")
    pump_pin.value = False
    time.sleep(1)
    print("Turning OFF solenoid valve relay...")
    valve_pin.value = False
    print("--- Relay Test Complete ---\n")

def monitor_sensors():
    print("--- SENSOR MONITORING ---")
    print("Monitoring Flow Sensor + Water Levels. Press CTRL+C to stop.\n")

    flow_count = 0
    last_flow_state = flow_pin.value
    last_display_time = time.time()

    try:
        while True:
            # Count flow sensor pulses
            current_flow_state = flow_pin.value
            if not last_flow_state and current_flow_state:
                flow_count += 1
            last_flow_state = current_flow_state

            # Read tank levels (active LOW)
            low_state = not level_low_pin.value
            high_state = not level_high_pin.value

            # Display every 1s
            if time.time() - last_display_time >= 1:
                last_display_time = time.time()
                print(f"Flow Pulses: {flow_count:4d} | "
                      f"Low Level: {'ON' if low_state else 'OFF'} | "
                      f"High Level: {'ON' if high_state else 'OFF'} | "
                      f"Pump: {'ON' if pump_pin.value else 'OFF'} | "
                      f"Valve: {'ON' if valve_pin.value else 'OFF'}")
            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nStopping sensor monitor...")
        pump_pin.value = False
        valve_pin.value = False
        print("All outputs OFF. Exiting test.\n")

def manual_control():
    print("\n--- MANUAL CONTROL ---")
    print("Commands:")
    print("[P] Toggle Pump")
    print("[V] Toggle Valve")
    print("[Q] Quit Manual Mode\n")

    pump_state = False
    valve_state = False

    while True:
        cmd = input("Enter command: ").strip().upper()
        if cmd == "P":
            pump_state = not pump_state
            pump_pin.value = pump_state
            print(f"Pump {'ON' if pump_state else 'OFF'}")
        elif cmd == "V":
            valve_state = not valve_state
            valve_pin.value = valve_state
            print(f"Valve {'ON' if valve_state else 'OFF'}")
        elif cmd == "Q":
            break
        else:
            print("Invalid command. Use P, V, or Q.")

    pump_pin.value = False
    valve_pin.value = False
    print("Manual control ended. All relays OFF.\n")

# ----------------------------
# Main Menu
# ----------------------------
def main():
    while True:
        print("====== WATER SYSTEM TEST MENU ======")
        print("1. Relay Test (Pump & Valve)")
        print("2. Monitor Sensors (Flow, Level Low, Level High)")
        print("3. Manual Relay Control")
        print("4. Exit\n")

        choice = input("Enter your choice [1-4]: ").strip()

        if choice == "1":
            test_relays()
        elif choice == "2":
            monitor_sensors()
        elif choice == "3":
            manual_control()
        elif choice == "4":
            pump_pin.value = False
            valve_pin.value = False
            print("All outputs OFF. Exiting test.")
            break
        else:
            print("Invalid choice, please enter 1–4.\n")

# ----------------------------
# Run the Test
# ----------------------------
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pump_pin.value = False
        valve_pin.value = False
        print("\nTest aborted. Outputs OFF.")
