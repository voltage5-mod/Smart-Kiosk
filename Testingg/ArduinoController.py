import serial, time

ser = serial.Serial('/dev/ttyACM0', 115200, timeout=1)
time.sleep(2)
print("Connected to Arduino")

def send(cmd):
    ser.write((cmd + "\n").encode())
    time.sleep(0.2)
    while ser.in_waiting:
        print(ser.readline().decode().strip())

try:
    while True:
        send("STATUS")      # ask current state
        time.sleep(2)
        if ser.in_waiting:
            line = ser.readline().decode().strip()
            if line.startswith("COIN"):
                print("Coin inserted →", line)
            elif "CUP_DETECTED" in line:
                print("Cup detected, dispensing started")
            elif "DISPENSE_COMPLETE" in line:
                print("Dispensing complete")
except KeyboardInterrupt:
    ser.write(b"RESET\n")
    ser.close()
