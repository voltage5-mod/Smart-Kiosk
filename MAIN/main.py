import tkinter as tk
import json
import serial
import threading
import time
from ui.account_screen import AccountScreen
from ui.main_menu import MainMenu
from ui.water_mode import WaterMode
from ui.charging_mode import ChargingMode
import hardware_gpio
from utils.session_manager import SessionManager
from utils.coin_handler import CoinHandler

class WaterVendoApp:
    def __init__(self):
        self.window = tk.Tk()
        self.window.title("Water and Charging Vendo System")
        self.window.geometry("1024x768")
        
        # Load pinmap
        with open('pinmap.json', 'r') as f:
            self.pinmap = json.load(f)
        
        # Initialize hardware
        self.hardware = hardware_gpio.HardwareGPIO(self.pinmap, mode='auto')
        self.hardware.setup()
        
        # Initialize session manager
        self.session = SessionManager()
        
        # Initialize coin handler
        self.coin_handler = CoinHandler(self.hardware, self.session)
        
        # Initialize Firebase (will be loaded in account screen)
        self.firebase = None
        
        # Container for screens
        self.container = tk.Frame(self.window)
        self.container.pack(fill="both", expand=True)
        
        # Initialize all screens
        self.screens = {}
        self.init_screens()
        
        # Setup Arduino communication
        self.arduino = None
        self.arduino_listening = False
        self.setup_arduino()
        
        # Show account screen first
        self.show_screen('account')
        
        # Handle window close
        self.window.protocol("WM_DELETE_WINDOW", self.on_close)
    
    def init_screens(self):
        """Initialize all application screens"""
        screens = [
            ('account', AccountScreen),
            ('main_menu', MainMenu),
            ('water_mode', WaterMode),
            ('charging_mode', ChargingMode),
        ]
        
        for name, ScreenClass in screens:
            screen = ScreenClass(self.container, self)
            self.screens[name] = screen
            screen.place(x=0, y=0, width=1024, height=768)
    
    def setup_arduino(self):
        """Setup Arduino serial connection"""
        try:
            port = self.pinmap.get('arduino_usb', '/dev/ttyACM0')
            self.arduino = serial.Serial(port, 9600, timeout=0.1)
            print(f"Arduino connected on {port}")
            
            # Start listener thread
            self.arduino_listening = True
            self.arduino_thread = threading.Thread(target=self.listen_arduino)
            self.arduino_thread.daemon = True
            self.arduino_thread.start()
            
        except Exception as e:
            print(f"Arduino connection failed: {e}")
            self.arduino = None
    
    def listen_arduino(self):
        """Listen for Arduino messages in background thread"""
        while self.arduino_listening:
            try:
                if self.arduino and self.arduino.in_waiting:
                    # Read all available bytes
                    data = self.arduino.read(self.arduino.in_waiting)
                    if data:
                        self.process_arduino_data(data.decode('utf-8', errors='ignore'))
                
                # Small sleep to prevent CPU overuse
                time.sleep(0.01)
                
            except serial.SerialException as e:
                print(f"Serial error: {e}")
                time.sleep(1)
            except Exception as e:
                print(f"Arduino listener error: {e}")
                time.sleep(0.1)
    
    def process_arduino_data(self, data):
        """Process incoming Arduino data"""
        lines = data.strip().split('\n')
        for line in lines:
            line = line.strip()
            if line:
                self.process_arduino_message(line)
    
    def process_arduino_message(self, message):
        """Process individual Arduino messages"""
        # Print for debugging
        print(f"Arduino: {message}")
        
        # Coin messages
        if message.startswith("COIN:"):
            try:
                coin_value = int(message.split(":")[1])
                self.handle_coin(coin_value)
            except (ValueError, IndexError):
                print(f"Invalid coin message: {message}")
        
        # Flow sensor messages
        elif message.startswith("FLOW:"):
            try:
                pulses = int(message.split(":")[1])
                self.handle_flow(pulses)
            except (ValueError, IndexError):
                print(f"Invalid flow message: {message}")
        
        # Cup sensor messages
        elif message.startswith("CUP:"):
            try:
                status = message.split(":")[1]
                self.handle_cup(status)
            except IndexError:
                print(f"Invalid cup message: {message}")
        
        # Pump status messages
        elif message.startswith("PUMP:"):
            # Just log pump status
            pass
        
        # Valve status messages
        elif message.startswith("VALVE:"):
            # Just log valve status
            pass
    
    def handle_coin(self, coin_value):
        """Handle coin insertion"""
        # Pass to coin handler
        if hasattr(self, 'coin_handler'):
            self.coin_handler.process_coin(coin_value)
        
        # Update any active screen that shows coin info
        if hasattr(self, 'screens'):
            current_screen = self.get_current_screen()
            if current_screen and hasattr(current_screen, 'update_coin_display'):
                current_screen.update_coin_display()
    
    def handle_flow(self, pulses):
        """Handle flow sensor pulses"""
        # Update water mode screen if active
        water_screen = self.screens.get('water_mode')
        if water_screen and hasattr(water_screen, 'add_flow_pulses'):
            water_screen.add_flow_pulses(pulses)
    
    def handle_cup(self, status):
        """Handle cup sensor status"""
        # Update water mode screen if active
        water_screen = self.screens.get('water_mode')
        if water_screen and hasattr(water_screen, 'set_cup_status'):
            water_screen.set_cup_status(status == "PRESENT")
    
    def send_to_arduino(self, command):
        """Send command to Arduino"""
        try:
            if self.arduino and self.arduino.is_open:
                self.arduino.write(f"{command}\n".encode())
                return True
        except Exception as e:
            print(f"Failed to send to Arduino: {e}")
        return False
    
    def get_current_screen(self):
        """Get the currently visible screen"""
        for screen_name, screen in self.screens.items():
            try:
                # Check if screen is currently raised
                if screen.winfo_ismapped():
                    return screen
            except:
                pass
        return None
    
    def show_screen(self, screen_name):
        """Switch to specified screen"""
        if screen_name in self.screens:
            self.screens[screen_name].tkraise()
            # Call screen activation method if it exists
            if hasattr(self.screens[screen_name], 'on_show'):
                self.screens[screen_name].on_show()
    
    def on_close(self):
        """Cleanup on application close"""
        # Stop Arduino listener
        self.arduino_listening = False
        
        # Close serial connection
        if self.arduino:
            try:
                self.arduino.close()
            except:
                pass
        
        # Cleanup hardware
        if self.hardware:
            self.hardware.cleanup()
        
        # Close window
        self.window.destroy()

if __name__ == "__main__":
    app = WaterVendoApp()
    app.window.mainloop()