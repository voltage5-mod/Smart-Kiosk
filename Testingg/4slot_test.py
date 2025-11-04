import RPi.GPIO as GPIO
import time

# TM1637 CLOCK shared pin
CLK = 5

# TM1637 DIO pins per slot (based on your pinmap)
DIO_PINS = [16, 21, 20, 4]

# Setup GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setup(CLK, GPIO.OUT)
for dio in DIO_PINS:
    GPIO.setup(dio, GPIO.OUT)

# TM1637 digit segments
SEG = [0x3f,0x06,0x5b,0x4f,0x66,0x6d,0x7d,0x07,0x7f,0x6f]

def start(clk, dio):
    GPIO.output(clk,1); GPIO.output(dio,1)
    GPIO.output(dio,0); GPIO.output(clk,0)

def stop(clk, dio):
    GPIO.output(clk,0); GPIO.output(dio,0)
    GPIO.output(clk,1); GPIO.output(dio,1)

def write_byte(clk, dio, data):
    for i in range(8):
        GPIO.output(clk,0)
        GPIO.output(dio,(data>>i)&1)
        GPIO.output(clk,1)
    GPIO.output(clk,0)
    GPIO.setup(dio, GPIO.IN)
    time.sleep(0.00005)
    GPIO.setup(dio, GPIO.OUT)

def display_number(dio, num, brightness=1):
    start(CLK, dio)
    write_byte(CLK, dio, 0x40)  # auto-address
    stop(CLK, dio)
    start(CLK, dio)
    write_byte(CLK, dio, 0xc0)

    digits = [
        SEG[(num//1000)%10],
        SEG[(num//100)%10],
        SEG[(num//10)%10],
        SEG[num%10]
    ]
    for d in digits:
        write_byte(CLK, dio, d)
    stop(CLK, dio)

    # Set brightness (0-7) low to protect your eyes 😎
    start(CLK, dio)
    write_byte(CLK, dio, 0x88 + brightness)
    stop(CLK, dio)

try:
    # Start values for each slot
    timers = [1000, 2000, 3000, 4000]

    while True:
        for i, dio in enumerate(DIO_PINS):
            display_number(dio, timers[i], brightness=1)
            timers[i] = max(0, timers[i] - 1)
        time.sleep(1)

except KeyboardInterrupt:
    GPIO.cleanup()
