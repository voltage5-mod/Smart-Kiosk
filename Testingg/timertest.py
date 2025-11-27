import RPi.GPIO as GPIO
import time

CLK = 5   # GPIO 5 (pin 29)
DIO = 16   # GPIO 16 (pin 36)

GPIO.setmode(GPIO.BCM)
GPIO.setup(CLK, GPIO.OUT)
GPIO.setup(DIO, GPIO.OUT)

# ----- low-level helper functions -----
def start():
    GPIO.output(CLK, 1)
    GPIO.output(DIO, 1)
    GPIO.output(DIO, 0)
    GPIO.output(CLK, 0)

def stop():
    GPIO.output(CLK, 0)
    GPIO.output(DIO, 0)
    GPIO.output(CLK, 1)
    GPIO.output(DIO, 1)

def write_byte(data):
    for i in range(8):
        GPIO.output(CLK, 0)
        GPIO.output(DIO, (data >> i) & 1)
        GPIO.output(CLK, 1)
    GPIO.output(CLK, 0)
    GPIO.setup(DIO, GPIO.IN)
    time.sleep(0.00005)
    GPIO.setup(DIO, GPIO.OUT)

# Segment map for digits 
SEGMENTS = [0x3f,0x06,0x5b,0x4f,0x66,0x6d,0x7d,0x07,0x7f,0x6f]

# ----- send data to TM1637 -----
def display_digits(d1, d2, d3, d4):
    start()
    write_byte(0x40)  # auto-increment mode
    stop()
    start()
    write_byte(0xc0)  # start address = 0
    write_byte(SEGMENTS[d1])
    write_byte(SEGMENTS[d2])
    write_byte(SEGMENTS[d3])
    write_byte(SEGMENTS[d4])
    stop()
    start()
    write_byte(0x88 + 7)  # brightness 
    stop()

# ----- test sequence -----
try:
    while True:
        display_digits(1,2,3,4)
        time.sleep(2)
        display_digits(8,8,8,8)
        time.sleep(2)
        display_digits(0,0,0,0)
        time.sleep(2)
except KeyboardInterrupt:
    GPIO.cleanup()
