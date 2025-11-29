# sensor_check.py
# Test script for ultrasonic sensor debugging

import serial
import time
import sys
import glob

def list_serial_ports():
    """List available serial ports"""
    if sys.platform.startswith('win'):
        ports = ['COM%s' % (i + 1) for i in range(256)]
    elif sys.platform.startswith('linux') or sys.platform.startswith('cygwin'):
        ports = glob.glob('/dev/tty[A-Za-z]*')
    elif sys.platform.startswith('darwin'):
        ports = glob.glob('/dev/tty.*')
    else:
        raise EnvironmentError('Unsupported platform')
    
    result = []
    for port in ports:
        try:
            s = serial.Serial(port)
            s.close()
            result.append(port)
        except (OSError, serial.SerialException):
            pass
    return result

class SensorTester:
    def __init__(self, port=None, baudrate=115200):
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        
    def connect(self):
        """Connect to Arduino"""
        if self.port is None:
            ports = list_serial_ports()
            if not ports:
                print("No serial ports found!")
                return False
            self.port = ports[0]
            print(f"Auto-selecting port: {self.port}")
        
        try:
            self.ser = serial.Serial(self.port, self.baudrate, timeout=1)
            time.sleep(2)  # Wait for Arduino to reset
            print(f"Connected to {self.port} at {self.baudrate} baud")
            return True
        except serial.SerialException as e:
            print(f"Failed to connect to {self.port}: {e}")
            return False
    
    def send_command(self, command):
        """Send command to Arduino"""
        if not self.ser or not self.ser.is_open:
            print("Not connected to Arduino!")
            return False
        
        try:
            self.ser.write(f"{command}\n".encode())
            print(f"Sent: {command}")
            return True
        except Exception as e:
            print(f"Error sending command: {e}")
            return False
    
    def read_sensor_continuously(self, duration=30):
        """Read sensor data continuously"""
        if not self.ser:
            print("Not connected!")
            return
        
        print(f"Reading sensor data for {duration} seconds...")
        print("Place objects at different distances from the sensor")
        print("Press Ctrl+C to stop early\n")
        
        start_time = time.time()
        try:
            while time.time() - start_time < duration:
                if self.ser.in_waiting > 0:
                    line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        print(f"[{time.time()-start_time:.1f}s] {line}")
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\nStopped by user")
    
    def test_cup_detection(self):
        """Test cup detection specifically"""
        if not self.ser:
            print("Not connected!")
            return
        
        print("Testing cup detection...")
        print("1. Make sure no objects are near the sensor")
        print("2. Place a cup under the sensor")
        print("3. Remove the cup")
        print("4. Watch for detection events")
        print("Press Ctrl+C to stop\n")
        
        try:
            while True:
                if self.ser.in_waiting > 0:
                    line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        # Highlight important events
                        if any(keyword in line for keyword in ['CUP_DETECTED', 'COUNTDOWN', 'DISTANCE', 'DEBUG']):
                            print(f"*** {line} ***")
                        else:
                            print(line)
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\nTest stopped")
    
    def manual_commands(self):
        """Interactive command mode"""
        if not self.ser:
            print("Not connected!")
            return
        
        print("Manual command mode - type commands to send to Arduino")
        print("Commands: STATUS, MODE WATER, RESET, CAL, FLOWCAL")
        print("Type 'quit' to exit\n")
        
        while True:
            try:
                cmd = input("Arduino> ").strip()
                if cmd.lower() == 'quit':
                    break
                if cmd:
                    self.send_command(cmd)
                    # Read response
                    time.sleep(0.5)
                    while self.ser.in_waiting > 0:
                        line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                        if line:
                            print(f"Arduino: {line}")
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"Error: {e}")
    
    def calibrate_sensor(self):
        """Help calibrate the sensor distance threshold"""
        if not self.ser:
            print("Not connected!")
            return
        
        print("Sensor Calibration Mode")
        print("1. Make sure nothing is near the sensor")
        print("2. Place a cup at the desired detection distance")
        print("3. Note the distance readings")
        print("4. Adjust CUP_DETECT_THRESHOLD_CM in Arduino code")
        print("Press Ctrl+C to stop\n")
        
        try:
            empty_readings = []
            cup_readings = []
            state = "empty"  # empty, cup, done
            
            while state != "done":
                if self.ser.in_waiting > 0:
                    line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    if line and "DISTANCE" in line:
                        print(line)
                        
                        # Extract distance value
                        try:
                            parts = line.split()
                            for i, part in enumerate(parts):
                                if part == "distance:" and i+1 < len(parts):
                                    distance = float(parts[i+1].replace('cm', ''))
                                    if state == "empty":
                                        empty_readings.append(distance)
                                        if len(empty_readings) >= 5:
                                            print(f"Empty average: {sum(empty_readings)/len(empty_readings):.1f}cm")
                                            input("Place cup now and press Enter...")
                                            state = "cup"
                                    elif state == "cup":
                                        cup_readings.append(distance)
                                        if len(cup_readings) >= 5:
                                            print(f"Cup average: {sum(cup_readings)/len(cup_readings):.1f}cm")
                                            state = "done"
                        except (ValueError, IndexError):
                            pass
                time.sleep(0.1)
            
            if empty_readings and cup_readings:
                empty_avg = sum(empty_readings) / len(empty_readings)
                cup_avg = sum(cup_readings) / len(cup_readings)
                print(f"\n--- CALIBRATION RESULTS ---")
                print(f"Empty sensor reading: {empty_avg:.1f}cm")
                print(f"Cup detected at: {cup_avg:.1f}cm")
                print(f"Recommended threshold: {(empty_avg + cup_avg) / 2:.1f}cm")
                print("Update CUP_DETECT_THRESHOLD_CM in Arduino code with this value")
                
        except KeyboardInterrupt:
            print("\nCalibration stopped")
    
    def close(self):
        """Close serial connection"""
        if self.ser and self.ser.is_open:
            self.ser.close()
            print("Serial connection closed")

def main():
    print("=== Ultrasonic Sensor Diagnostic Tool ===")
    print()
    
    # List available ports
    ports = list_serial_ports()
    if not ports:
        print("No serial ports found! Check Arduino connection.")
        return
    
    print("Available ports:")
    for i, port in enumerate(ports):
        print(f"  {i+1}. {port}")
    
    # Select port
    if len(ports) == 1:
        port = ports[0]
        print(f"Using {port} (only port available)")
    else:
        try:
            choice = int(input(f"Select port (1-{len(ports)}): ")) - 1
            port = ports[choice]
        except (ValueError, IndexError):
            print("Invalid selection, using first port")
            port = ports[0]
    
    # Create tester and connect
    tester = SensorTester(port)
    if not tester.connect():
        return
    
    try:
        while True:
            print("\n=== TEST MENU ===")
            print("1. Continuous sensor reading (30s)")
            print("2. Cup detection test")
            print("3. Sensor calibration")
            print("4. Manual commands")
            print("5. Exit")
            
            try:
                choice = input("Select test (1-5): ").strip()
                
                if choice == '1':
                    tester.read_sensor_continuously()
                elif choice == '2':
                    tester.test_cup_detection()
                elif choice == '3':
                    tester.calibrate_sensor()
                elif choice == '4':
                    tester.manual_commands()
                elif choice == '5':
                    break
                else:
                    print("Invalid choice")
                    
            except KeyboardInterrupt:
                print("\nReturning to menu...")
                continue
                
    finally:
        tester.close()
        print("Goodbye!")

if __name__ == "__main__":
    main()