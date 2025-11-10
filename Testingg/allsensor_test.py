import spidev
import time
import RPi.GPIO as GPIO

# ==============================
# GPIO PIN CONFIGURATION
# ==============================
RELAYS = [17, 22, 24, 26]  # Slot 1–4 Power Relays

GPIO.setmode(GPIO.BCM)
for pin in RELAYS:
    GPIO.setup(pin, GPIO.OUT)
    GPIO.output(pin, GPIO.HIGH)  # All relays OFF initially (active LOW)

# ==============================
# MCP3008 Configuration
# ==============================
spi = spidev.SpiDev()
spi.open(0, 0)
spi.max_speed_hz = 1350000

def read_channel(channel):
    if channel < 0 or channel > 7:
        return -1
    adc = spi.xfer2([1, (8 + channel) << 4, 0])
    data = ((adc[1] & 3) << 8) + adc[2]
    return data

def to_voltage(adc_value, vref=3.3):
    return (adc_value * vref) / 1023.0

def to_current(voltage, offset=2.5, sensitivity=0.185):
    return (voltage - offset) / sensitivity

# ==============================
# MAIN TEST LOOP
# ==============================
try:
    print("Starting Active-LOW Relay + Current Sensor Test...")
    print("Press Ctrl+C to stop.\n")

    # Turn ON all slot relays (active LOW)
    for pin in RELAYS:
        GPIO.output(pin, GPIO.LOW)
    print("All relays ON. Measuring current...\n")

    while True:
        readings = []
        for ch in range(4):
            adc_val = read_channel(ch)
            voltage = to_voltage(adc_val)
            current = to_current(voltage)
            readings.append(round(current, 3))
        print(f"I1={readings[0]}A | I2={readings[1]}A | I3={readings[2]}A | I4={readings[3]}A")
        time.sleep(0.5)

except KeyboardInterrupt:
    print("\nExiting test...")
    for pin in RELAYS:
        GPIO.output(pin, GPIO.HIGH)  # Turn all relays OFF
    spi.close()
    GPIO.cleanup()
    print("All relays OFF. GPIO cleaned up.")
