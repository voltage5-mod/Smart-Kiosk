import serial
import time
import sys

def test_coin_arduino(port='/dev/ttyUSB0', baudrate=115200):
    """Test the coin Arduino hardware directly"""
    
    print(f"Testing Coin Arduino on {port}")
    print("=" * 50)
    
    try:
        # Connect to Arduino
        ser = serial.Serial(port, baudrate, timeout=1)
        time.sleep(2)  # Wait for Arduino reset
        
        print("Connected to Arduino")
        
        # Clear buffer
        ser.reset_input_buffer()
        
        # Send test command
        ser.write(b"TEST\n")
        
        # Read for 5 seconds to see initial state
        print("Listening for Arduino messages...")
        print("Insert coins now to test detection")
        print("-" * 50)
        
        start_time = time.time()
        message_count = 0
        
        while time.time() - start_time < 10:  # Listen for 10 seconds
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    message_count += 1
                    print(f"📨 [{message_count}] {line}")
                    
                    # Check for specific messages
                    if "COIN_ARDUINO_READY" in line:
                        print("Coin Arduino is initialized")
                    elif "Pulse" in line:
                        print("OIN PULSE DETECTED!")
                    elif "COIN_INSERTED" in line:
                        print("COIN VALUE DETECTED!")
            
            time.sleep(0.1)
        
        print("-" * 50)
        if message_count == 0:
            print("NO MESSAGES RECEIVED - Arduino may not be running coin code")
        else:
            print(f"Received {message_count} messages - Communication working")
            
        ser.close()
        
    except Exception as e:
        print(f"Connection failed: {e}")

def check_serial_ports():
    """Check what's on each serial port"""
    ports = ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyACM0', '/dev/ttyACM1']
    
    print("🔍 Scanning serial ports...")
    for port in ports:
        try:
            ser = serial.Serial(port, 115200, timeout=1)
            time.sleep(2)
            ser.write(b"\n")  # Send empty line to trigger response
            
            # Read any available data
            messages = []
            start_time = time.time()
            while time.time() - start_time < 3:
                if ser.in_waiting > 0:
                    line = ser.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        messages.append(line)
                time.sleep(0.1)
            
            ser.close()
            
            if messages:
                print(f"{port}: ACTIVE - {len(messages)} messages")
                for msg in messages[:3]:  # Show first 3 messages
                    print(f"{msg}")
            else:
                print(f"{port}: No response")
                
        except Exception as e:
            print(f"{port}: Cannot connect - {e}")

if __name__ == "__main__":
    print("=== COIN ARDUINO HARDWARE DIAGNOSTIC ===")
    
    # First, scan all ports
    check_serial_ports()
    print("\n" + "="*50 + "\n")
    
    # Then test the coin Arduino
    test_coin_arduino('/dev/ttyUSB0')