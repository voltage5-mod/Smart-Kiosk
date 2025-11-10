import spidev
import time
import RPi.GPIO as GPIO

# ==============================
# GPIO PIN CONFIGURATION
# ==============================
# Relay GPIO pins per slot (use BCM numbering)
RELAYS = [17, 22, 24, 26]  # Slot 1–4 Power Relays

# Setup GPIO
GPIO.setmode(GPIO.BCM)
for pin in RELAYS:
    GPIO.setup(pin, GPIO.OUT)
    GPIO.output(pin, GPIO.LOW)  # all OFF initially

# ==============================
# MCP3008 Configuration
# ==============================
spi = spidev.SpiDev()
spi.open(0, 0)  # Bus 0, CE0
spi.max_speed_hz = 1350000

# Function to read MCP3008 channel
def read_channel(channel):
    if channel < 0 or channel > 7:
        return -1
    adc = spi.xfer2([1, (8 + channel) << 4, 0])
    data = ((adc[1] & 3) << 8) + adc[2]
    return data

# Convert ADC value to voltage (3.3V reference)
def to_voltage(adc_value, vref=3.3):
    return (adc_value * vref) / 1023.0

# Convert voltage to current (ACS712 5A version, 185mV/A)
def to_current(voltage, offset=2.5, sensitivity=0.185):
    return (voltage - offset) / sensitivity

# ==============================
# MAIN TEST LOOP
# ==============================
try:
    print("Starting Relay + Current Sensor Test...")
    print("Press Ctrl+C to stop.\n")

    # Turn ON all slot relays for full test
    for pin in RELAYS:
        GPIO.output(pin, GPIO.HIGH)
    print("All relays ON. Measuring current...\n")

    while True:
        readings = []
        for ch in range(4):  # channels 0–3
            adc_val = read_channel(ch)
            voltage = to_voltage(adc_val)
            current = to_current(voltage)
            readings.append(round(current, 3))
        print(f"I1={readings[0]}A | I2={readings[1]}A | I3={readings[2]}A | I4={readings[3]}A")
        time.sleep(0.5)

except KeyboardInterrupt:
    print("\nExiting test...")
    for pin in RELAYS:
        GPIO.output(pin, GPIO.LOW)
    spi.close()
    GPIO.cleanup()
    print("All relays OFF. GPIO cleaned up.")
