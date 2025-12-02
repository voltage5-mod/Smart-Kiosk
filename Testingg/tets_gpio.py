#!/usr/bin/env python3
import RPi.GPIO as GPIO
import time

print("Testing GPIO...")

# Clean up any existing setup
GPIO.cleanup()

# Set mode and disable warnings
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# Test pins (change these to your actual pins)
test_pins = [17, 18, 27, 22]

try:
    for pin in test_pins:
        print(f"Setting up pin {pin}...")
        GPIO.setup(pin, GPIO.OUT)
        GPIO.output(pin, GPIO.HIGH)
        time.sleep(0.5)
        GPIO.output(pin, GPIO.LOW)
        print(f"  ✓ Pin {pin} tested")
    
    print("\nAll GPIO tests passed!")
    
except Exception as e:
    print(f"Error: {e}")

finally:
    GPIO.cleanup()
    print("GPIO cleanup complete")