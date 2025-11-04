import RPi.GPIO as GPIO
import time

# Shared CLK pin
CLK = 5

# Individual DIO pins
DIO_PINS = [16, 21, 20, 4]

GPIO.setmode(GPIO.BCM)
GPIO.setup(CLK, GPIO.OUT)

for dio in DIO_PINS:
    GPIO.setup(dio, GPIO.OUT)

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
        GPIO.output(dio, (data >> i) & 1)
        time.sleep(0.00002)
        GPIO.output(clk,1)
        time.sleep(0.00002)
    
    # ACK bit
    GPIO.output(clk,0)
    GPIO.setup(dio, GPIO.IN)
    time.sleep(0.00002)
    GPIO.output(clk,1)
    time.sleep(0.00002)
    GPIO.setup(dio, GPIO.OUT)

def show_number(dio, value, brightness=1):
    digits = [
        SEG[(value//1000)%10],
        SEG[(value//100)%10],
        SEG[(value//10)%10],
        SEG[value%10]
    ]

    # Command: auto-increment address
    start(CLK, dio)
    write_byte(CLK, dio, 0x40)
    stop(CLK, dio)

    # Set starting address 0xC0
    start(CLK, dio)
    write_byte(CLK, dio, 0xC0)
    for d in digits:
        write_byte(CLK, dio, d)
    stop(CLK, dio)

    # Set brightness
    start(CLK, dio)
    write_byte(CLK, dio, 0x88 + brightness)
    stop(CLK, dio)

def clear_display(dio):
    start(CLK, dio)
    write_byte(CLK, dio, 0x40)
    stop(CLK, dio)

    start(CLK, dio)
    write_byte(CLK, dio, 0xC0)
    for _ in range(4):
        write_byte(CLK, dio, 0x00)
    stop(CLK, dio)

    start(CLK, dio)
    write_byte(CLK, dio, 0x88)
    stop(CLK, dio)

try:
    timers = [1000, 2000, 3000, 4000]

    while True:
        for i, dio in enumerate(DIO_PINS):
            show_number(dio, timers[i], brightness=1)
            timers[i] = max(0, timers[i] - 1)

        time.sleep(1)

except KeyboardInterrupt:
    for dio in DIO_PINS:
        clear_display(dio)
    GPIO.cleanup()
    print("\nDisplays cleared & GPIO cleaned up.")
