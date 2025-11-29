import serial
import time
import sys
import glob

def list_serial_ports():
    """Lists available serial ports"""
    if sys.platform.startswith('win'):
        ports = ['COM%s' % (i + 1) for i in range(256)]
    elif sys.platform.startswith('linux') or sys.platform.startswith('cygwin'):
        ports = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')
    elif sys.platform.startswith('darwin'):
        ports = glob.glob('/dev/tty.usb*') + glob.glob('/dev/ttyACM*')
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

class CoinTester:
    def __init__(self, port=None, baudrate=115200):
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        self.running = False
        
    def connect(self):
        """Connect to Arduino"""
        if self.port is None:
            ports = list_serial_ports()
            if not ports:
                print("No serial ports found!")
                return False
            
            print("Available ports:")
            for i, port in enumerate(ports):
                print(f"{i+1}. {port}")
            
            try:
                choice = int(input("Select port number: ")) - 1
                self.port = ports[choice]
            except (ValueError, IndexError):
                print("Invalid selection")
                return False
        
        try:
            print(f"Connecting to {self.port} at {self.baudrate} baud...")
            self.ser = serial.Serial(self.port, self.baudrate, timeout=1)
            time.sleep(2)  # Wait for Arduino to reset
            
            # Clear any existing data
            self.ser.reset_input_buffer()
            
            # Test connection
            self.ser.write(b"STATUS\n")
            time.sleep(0.5)
            
            # Read any initial messages
            print("Reading initial messages...")
            while self.ser.in_waiting > 0:
                line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                print(f"INIT: {line}")
            
            print(f"Connected to {self.port} successfully!")
            return True
            
        except Exception as e:
            print(f"Failed to connect: {e}")
            return False
    
    def start_monitoring(self):
        """Start monitoring coin events"""
        if not self.ser or not self.ser.is_open:
            print("Not connected to Arduino!")
            return
        
        self.running = True
        print("\n=== COIN TESTER STARTED ===")
        print("Insert coins to test detection")
        print("Press Ctrl+C to stop")
        print("=" * 30)
        
        try:
            while self.running:
                if self.ser.in_waiting > 0:
                    line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        self.process_line(line)
                time.sleep(0.01)
                
        except KeyboardInterrupt:
            print("\nStopping coin tester...")
        finally:
            self.stop()
    
    def process_line(self, line):
        """Process incoming serial data"""
        print(f"RAW: {line}")
        
        # Handle different message types
        if "COIN_ARDUINO_READY" in line:
            print("✓ Coin Arduino is ready!")
        
        elif "Pulse detected" in line:
            print(f"⚡ Coin pulse detected: {line}")
        
        elif "Processing" in line and "pulses" in line:
            print(f"{line}")
        
        elif "COIN_INSERTED" in line:
            try:
                parts = line.split()
                if len(parts) >= 2:
                    coin_value = int(parts[1])
                    print(f"COIN DETECTED: ₱{coin_value}")
            except ValueError:
                print(f"Could not parse coin value: {line}")
        
        elif "COIN_WATER" in line:
            try:
                parts = line.split()
                if len(parts) >= 2:
                    ml_value = int(parts[1])
                    print(f"Water credit: {ml_value}mL")
            except ValueError:
                print(f"Could not parse water value: {line}")
        
        elif "COIN_UNKNOWN" in line:
            print(f"Unknown coin detected: {line}")
        
        elif "DEBUG:" in line:
            print(f"{line}")
        
        elif line.strip():  # Only print non-empty lines
            print(f"{line}")
    
    def send_command(self, command):
        """Send command to Arduino"""
        if not self.ser or not self.ser.is_open:
            print("Not connected to Arduino!")
            return False
        
        try:
            full_cmd = command + "\n"
            self.ser.write(full_cmd.encode())
            print(f"Sent: {command}")
            return True
        except Exception as e:
            print(f"Error sending command: {e}")
            return False
    
    def stop(self):
        """Stop the tester"""
        self.running = False
        if self.ser and self.ser.is_open:
            self.ser.close()
            print("Serial port closed")

def main():
    print("=== COIN SLOT TESTER ===")
    print("This tool tests the coin slot Arduino communication")
    
    tester = CoinTester()
    
    # Try to connect
    if not tester.connect():
        print("Failed to connect to Arduino")
        return
    
    # Main monitoring loop
    try:
        tester.start_monitoring()
    except Exception as e:
        print(f"Error during monitoring: {e}")
    finally:
        tester.stop()

if __name__ == "__main__":
    main()