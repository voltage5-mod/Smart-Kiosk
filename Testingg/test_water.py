import serial
import time
import threading

class WaterTester:
    def __init__(self, port='/dev/ttyUSB0', baud=115200):
        self.ser = None
        self.running = False
        self.port = port
        self.baud = baud
        
    def connect(self):
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=1)
            time.sleep(2)  # Wait for Arduino to reset
            print(f"Connected to {self.port}")
            return True
        except Exception as e:
            print(f"Failed to connect: {e}")
            return False
            
    def start_monitor(self):
        def monitor():
            while self.running and self.ser:
                if self.ser.in_waiting > 0:
                    line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        print(f"ARDUINO: {line}")
                        
        self.running = True
        self.monitor_thread = threading.Thread(target=monitor, daemon=True)
        self.monitor_thread.start()
        
    def send_command(self, cmd):
        if self.ser:
            full_cmd = cmd + "\n"
            self.ser.write(full_cmd.encode())
            print(f"SENT: {cmd}")
            
    def test_sequence(self):
        print("\n=== TESTING WATER SYSTEM ===")
        
        # Test 1: Add credit
        print("\n1. Adding 500mL credit...")
        self.send_command("ADD500")
        time.sleep(1)
        
        # Test 2: Check status
        print("\n2. Checking status...")
        self.send_command("STATUS")
        time.sleep(1)
        
        # Test 3: Force start dispensing
        print("\n3. Starting dispensing...")
        self.send_command("START")
        time.sleep(2)
        
        # Test 4: Check status during dispensing
        print("\n4. Checking status during dispensing...")
        self.send_command("STATUS")
        time.sleep(5)
        
        # Test 5: Stop
        print("\n5. Stopping...")
        self.send_command("STOP")
        time.sleep(1)
        
        # Test 6: Final status
        print("\n6. Final status...")
        self.send_command("STATUS")
        
    def manual_test(self):
        print("\n=== MANUAL TEST MODE ===")
        print("Commands: ADD100, ADD500, START, STOP, STATUS, RESET, EXIT")
        
        while True:
            cmd = input("Enter command: ").strip().upper()
            if cmd == "EXIT":
                break
            elif cmd in ["ADD100", "ADD500", "START", "STOP", "STATUS", "RESET"]:
                self.send_command(cmd)
            else:
                print("Invalid command")

if __name__ == "__main__":
    tester = WaterTester()
    
    if tester.connect():
        tester.start_monitor()
        
        # Wait a bit for initial messages
        time.sleep(2)
        
        # Run automated test
        tester.test_sequence()
        
        # Then run manual test
        tester.manual_test()
        
        tester.running = False
    else:
        print("Could not connect to Arduino")