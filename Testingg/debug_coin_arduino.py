import serial
import time

def debug_coin_arduino():
    """See what code is actually running on the coin Arduino"""
    
    print("DEBUGGING COIN ARDUINO ON /dev/ttyUSB0")
    print("=" * 60)
    
    try:
        ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=1)
        time.sleep(2)  # Wait for reset
        
        # Clear buffer
        ser.reset_input_buffer()
        
        # Send a newline to trigger any startup messages
        ser.write(b"\n")
        
        print("Listening for ANY messages from coin Arduino...")
        print("If you see 'COIN_ARDUINO_READY', the coin code is running")
        print("If you see nothing, the Arduino may have different code")
        print("-" * 60)
        
        # Listen for messages
        start_time = time.time()
        while time.time() - start_time < 5:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    print(f"RECEIVED: {line}")
                    
                    # Check what kind of code is running
                    if "COIN" in line.upper():
                        print("   COIN CODE: Coin detection code detected!")
                    if "WATER" in line.upper():
                        print("   WRONG CODE: Water code detected (wrong Arduino?)")
                    if "ULTRASONIC" in line.upper() or "CUP" in line.upper():
                        print("   WRONG CODE: Water system code detected (wrong Arduino?)")
            
            time.sleep(0.1)
        
        print("-" * 60)
        print("IF NO COIN CODE: The Arduino may have:")
        print("   - Different code uploaded")
        print("   - No code uploaded")
        print("   - Water system code instead of coin code")
        
        ser.close()
        
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    debug_coin_arduino()