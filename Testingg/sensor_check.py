# sensor_debug.py
# Force Arduino to output sensor data for debugging

import serial
import time
import sys
import glob

class SensorDebugger:
    def __init__(self, port=None, baudrate=115200):
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        
    def find_arduino(self):
        """Find and connect to Arduino"""
        ports = []
        if sys.platform.startswith('win'):
            ports = ['COM%s' % (i + 1) for i in range(256)]
        elif sys.platform.startswith('linux') or sys.platform.startswith('cygwin'):
            ports = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')
        elif sys.platform.startswith('darwin'):
            ports = glob.glob('/dev/tty.usb*') + glob.glob('/dev/tty.usbmodem*')
        
        for port in ports:
            try:
                print(f"Trying {port}...")
                ser = serial.Serial(port, self.baudrate, timeout=1)
                time.sleep(2)  # Wait for Arduino reset
                
                # Test communication
                ser.write(b"PING\n")
                time.sleep(0.5)
                
                # Read any response
                response = ""
                while ser.in_waiting > 0:
                    line = ser.readline().decode('utf-8', errors='ignore').strip()
                    response += line + " "
                
                if response:
                    print(f"SUCCESS: Found Arduino on {port} - Response: {response}")
                    self.ser = ser
                    self.port = port
                    return True
                else:
                    ser.close()
                    
            except (serial.SerialException, OSError):
                continue
        
        print("ERROR: No Arduino found!")
        return False
    
    def force_sensor_output(self):
        """Force Arduino to continuously output sensor readings"""
        if not self.ser:
            print("ERROR: Not connected to Arduino!")
            return False
        
        print("Starting forced sensor output...")
        
        # Send command to start sensor readings
        self.ser.write(b"STATUS\n")
        time.sleep(1)
        
        # Clear any existing data
        self.ser.reset_input_buffer()
        
        print("Reading sensor data for 30 seconds...")
        print("Move objects in front of the sensor to test detection")
        print("Press Ctrl+C to stop\n")
        
        start_time = time.time()
        readings_count = 0
        
        try:
            while time.time() - start_time < 30:
                # Send ping every 2 seconds to keep Arduino active
                if int(time.time() - start_time) % 2 == 0:
                    self.ser.write(b"PING\n")
                
                # Read all available data
                while self.ser.in_waiting > 0:
                    line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        readings_count += 1
                        print(f"[{readings_count}] {line}")
                
                time.sleep(0.1)
                
        except KeyboardInterrupt:
            print("\nStopped by user")
        
        print(f"Total readings received: {readings_count}")
        return readings_count > 0
    
    def test_sensor_directly(self):
        """Upload and run a simple sensor test sketch"""
        print("Testing sensor with direct commands...")
        
        if not self.ser:
            print("ERROR: Not connected to Arduino!")
            return False
        
        # Send reset command
        self.ser.write(b"RESET\n")
        time.sleep(2)
        
        # Clear buffer
        self.ser.reset_input_buffer()
        
        print("Sending test sequence...")
        test_commands = [
            "STATUS",
            "MODE WATER",
            "CAL",
        ]
        
        for cmd in test_commands:
            print(f"Sending: {cmd}")
            self.ser.write(f"{cmd}\n".encode())
            time.sleep(2)
            
            # Read responses
            response_lines = []
            while self.ser.in_waiting > 0:
                line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    response_lines.append(line)
                    print(f"Response: {line}")
            
            if not response_lines:
                print("No response from Arduino!")
        
        return len(response_lines) > 0
    
    def monitor_raw_output(self, duration=60):
        """Monitor raw serial output from Arduino"""
        if not self.ser:
            print("ERROR: Not connected to Arduino!")
            return False
        
        print(f"Monitoring raw Arduino output for {duration} seconds...")
        print("This shows everything the Arduino sends")
        print("Press Ctrl+C to stop\n")
        
        start_time = time.time()
        line_count = 0
        
        try:
            while time.time() - start_time < duration:
                if self.ser.in_waiting > 0:
                    line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        line_count += 1
                        print(f"LINE {line_count}: {line}")
                else:
                    # If no data for 5 seconds, send a ping
                    if time.time() - start_time > 5 and line_count == 0:
                        print("No data received - sending PING...")
                        self.ser.write(b"PING\n")
                        time.sleep(1)
                
                time.sleep(0.1)
                
        except KeyboardInterrupt:
            print("\nMonitoring stopped")
        
        print(f"Total lines received: {line_count}")
        return line_count > 0
    
    def check_serial_connection(self):
        """Basic serial connection test"""
        if not self.ser:
            print("ERROR: No serial connection!")
            return False
        
        print("Testing serial connection...")
        
        # Test 1: Check if port is open
        if not self.ser.is_open:
            print("FAIL: Serial port is not open")
            return False
        print("PASS: Serial port is open")
        
        # Test 2: Send test command
        try:
            self.ser.write(b"TEST\n")
            print("PASS: Can write to serial port")
        except Exception as e:
            print(f"FAIL: Cannot write to serial port: {e}")
            return False
        
        # Test 3: Check if we can read
        time.sleep(0.5)
        if self.ser.in_waiting > 0:
            data = self.ser.read(self.ser.in_waiting)
            print(f"PASS: Can read from serial port - Data: {data}")
        else:
            print("INFO: No immediate response from Arduino (this may be normal)")
        
        return True
    
    def close(self):
        """Close serial connection"""
        if self.ser and self.ser.is_open:
            self.ser.close()
            print("Serial connection closed")

def main():
    print("=== Arduino Sensor Debugger ===")
    print()
    
    debugger = SensorDebugger()
    
    # Try to find Arduino automatically
    if not debugger.find_arduino():
        print("Please check:")
        print("1. Arduino is connected via USB")
        print("2. Correct drivers are installed")
        print("3. No other program is using the serial port")
        return
    
    try:
        while True:
            print("\n=== DEBUG MENU ===")
            print("1. Test serial connection")
            print("2. Monitor raw Arduino output")
            print("3. Force sensor output")
            print("4. Send test commands")
            print("5. Exit")
            
            try:
                choice = input("Select option (1-5): ").strip()
                
                if choice == '1':
                    debugger.check_serial_connection()
                elif choice == '2':
                    debugger.monitor_raw_output(30)
                elif choice == '3':
                    debugger.force_sensor_output()
                elif choice == '4':
                    debugger.test_sensor_directly()
                elif choice == '5':
                    break
                else:
                    print("Invalid choice")
                    
            except KeyboardInterrupt:
                print("\nReturning to menu...")
                continue
                
    finally:
        debugger.close()
        print("Debug session ended")

if __name__ == "__main__":
    main()