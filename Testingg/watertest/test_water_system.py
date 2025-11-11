# test_water_system.py
# Focused hardware test for the water subsystem of the Smart Kiosk

import time
import board
import busio
import digitalio
from adafruit_mcp230xx.mcp23017 import MCP23017

# ----------------------------
# MCP23017 Initialization
# ----------------------------
print("Initializing I2C bus...")
i2c = busio.I2C(board.SCL, board.SDA)
mcp = MCP23017(i2c, address=0x20)
print("MCP23017 detected successfully at address 0x20")

# ----------------------------
# Pin Assignments (from pinmap.json)
# ----------------------------
flow_pin = mcp.get_pin(0)        # GPA0
level_low_pin = mcp.get_pin(1)   # GPA1
level_high_pin = mcp.get_pin(2)  # GPA2
pump_pin = mcp.get_pin(8)        # GPB0
valve_pin = mcp.get_pin(9)       # GPB1

# ----------------------------
# Pin Configuration
# ----------------------------
for p in [flow_pin, level_low_pin, level_high_pin]:
    p.direction = digitalio.Direction.INPUT
    p.pull_up = True

for p in [pump_pin, valve_pin]:
    p.direction = digitalio.Direction.OUTPUT
    p.value = False

print("Water system pins configured successfully.\n")

# ----------------------------
# Helper Functions
# ----------------------------
def test_relays():
    print("--- RELAY TEST ---")
    print("Turning ON Pump relay...")
    pump_pin.value = True
    time.sleep(1)
    print("Turning ON Valve relay...")
    valve_pin.value = True
    time.sleep(1)

    print("Turning OFF Pump relay...")
    pump_pin.value = False
    time.sleep(1)
    print("Turning OFF Valve relay...")
    valve_pin.value = False
    print("--- Relay Test Complete ---\n")

def monitor_sensors():
    print("--- SENSOR MONITORING ---")
    print("Reading Flow, Tank Level (Low/High) sensors.")
    print("Press CTRL + C to stop.\n")

    flow_count = 0
    last_flow_state = flow_pin.value
    last_display_time = time.time()

    try:
        while True:
            # Flow pulse counting
            current_flow_state = flow_pin.value
            if not last_flow_state and current_flow_state:
                flow_count += 1
            last_flow_state = current_flow_state

            # Read tank level sensors (active LOW)
            level_low = not level_low_pin.value
            level_high = not level_high_pin.value

            # Display every 1 second
            if time.time() - last_display_time >= 1:
                last_display_time = time.time()
                print(f"Flow Pulses: {flow_count:<5} | "
                      f"Low Level: {'ON' if level_low else 'OFF'} | "
                      f"High Level: {'ON' if level_high else 'OFF'} | "
                      f"Pump: {'ON' if pump_pin.value else 'OFF'} | "
                      f"Valve: {'ON' if valve_pin.value else 'OFF'}")

            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nStopping monitoring...")
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
# Run Program
# ----------------------------
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pump_pin.value = False
        valve_pin.value = False
        print("\nTest aborted. Outputs OFF.")
