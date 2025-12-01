# test_serial.py
import serial
import time

# Try different baud rates
BAUD_RATES = [9600, 19200, 38400, 57600, 115200, 230400]

for baud in BAUD_RATES:
    print(f"\nTrying baud rate: {baud}")
    try:
        ser = serial.Serial('/dev/ttyUSB0', baud, timeout=2)
        time.sleep(2)
        ser.write(b"STATUS\n")
        time.sleep(1)
        
        if ser.in_waiting > 0:
            data = ser.read(ser.in_waiting)
            print(f"Response: {data}")
            if b"SYSTEM" in data or b"READY" in data or b"STATUS" in data:
                print(f"✓ Correct baud rate found: {baud}")
                break
        ser.close()
    except Exception as e:
        print(f"Error at {baud}: {e}")

time.sleep(0.5)