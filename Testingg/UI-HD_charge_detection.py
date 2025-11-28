# kiosk_ui_full_v2.py
# Full UI prototype for: Solar-Powered Smart Vending and Charging Station (UI-only)

import tkinter as tk
from tkinter import messagebox
import firebase_admin
from firebase_admin import credentials, db
import time
import json
import os
import re
import sys
import glob

# Try to import firebase_helpers, but continue if not available
try:
    from firebase_helpers import append_audit_log, deduct_charge_balance_transactionally
    FIREBASE_HELPERS_AVAILABLE = True
except ImportError:
    print("WARN: firebase_helpers not available - running in offline mode")
    FIREBASE_HELPERS_AVAILABLE = False

# hardware integration
try:
    from hardware_gpio import HardwareGPIO
    HARDWARE_GPIO_AVAILABLE = True
except ImportError:
    print("WARN: hardware_gpio not available - using simulation mode")
    HARDWARE_GPIO_AVAILABLE = False

# load pinmap for hardware_gpio
BASE = os.path.dirname(__file__)
PINMAP_PATH = os.path.join(BASE, 'pinmap.json')
try:
    with open(PINMAP_PATH, 'r', encoding='utf-8') as _f:
        _pinmap = json.load(_f)
except Exception:
    _pinmap = None

# ---------------- Configuration ----------------
DATABASE_URL = "https://kiosk-testing-22bf4-default-rtdb.firebaseio.com/"

# AUTO-DETECT FIREBASE SERVICE KEY
def find_firebase_key():
    """Automatically find the Firebase service account key file."""
    possible_names = [
        "firebase-key.json",
        "kiosk-testing-22bf4-firebase-adminsdk-fbsvc-2c5b11e75d.json",  # Your actual file
        "serviceAccountKey.json",
        "firebase-adminsdk*.json"
    ]
    
    for name in possible_names:
        if name.endswith('*.json'):
            # Handle wildcard patterns
            matches = glob.glob(name)
            if matches:
                return matches[0]
        elif os.path.exists(name):
            return name
    
    # Try to find any JSON file that looks like a Firebase key
    for file in os.listdir('.'):
        if file.endswith('.json') and ('firebase' in file.lower() or 'admin' in file.lower()):
            return file
    
    return None

SERVICE_KEY = find_firebase_key()

if SERVICE_KEY and os.path.exists(SERVICE_KEY):
    print(f"INFO: Found Firebase key file: {SERVICE_KEY}")
    try:
        # Test if the key file is valid
        with open(SERVICE_KEY, 'r') as f:
            key_data = json.load(f)
        FIREBASE_AVAILABLE = True
        print("INFO: Firebase key file is valid JSON")
    except Exception as e:
        print(f"ERROR: Firebase key file is invalid: {e}")
        FIREBASE_AVAILABLE = False
        SERVICE_KEY = None
else:
    print("WARN: No Firebase key file found")
    FIREBASE_AVAILABLE = False
    SERVICE_KEY = None

# Timing / conversion constants
WATER_SECONDS_PER_LITER = 10
WATER_DB_WRITE_INTERVAL = 2
CHARGE_DB_WRITE_INTERVAL = 10
UNPLUG_GRACE_SECONDS = 60
NO_CUP_TIMEOUT = 10

# Charging detection tuning
PLUG_THRESHOLD = 0.10
UNPLUG_THRESHOLD = 0.07
UNPLUG_GRACE_SECONDS = 30
CONFIRM_SAMPLES = 3
SAMPLE_INTERVAL = 0.5

# Coin to seconds mapping (charging)
COIN_MAP = {1: 60, 5: 300, 10: 600}

# Coin to ml mapping (water)
WATER_COIN_MAP = {1: 50, 5: 250, 10: 500}

# Default starting balances for newly registered members (seconds)
DEFAULT_WATER_BAL = 600
DEFAULT_CHARGE_BAL = 1200

# ------------------------------------------------

# Initialize Firebase Admin only if available
users_ref = None
slots_ref = None
firebase_app = None

# Add this diagnostic code before your Firebase initialization
# Replace the diagnose_firebase_issue function with this version:
def diagnose_firebase_issue():
    if not SERVICE_KEY or not os.path.exists(SERVICE_KEY):
        print("ERROR: Firebase key file not found")
        return False
    
    try:
        with open(SERVICE_KEY, 'r') as f:
            key_data = json.load(f)
        
        required_fields = ['type', 'project_id', 'private_key_id', 'private_key', 'client_email']
        missing = [field for field in required_fields if field not in key_data]
        
        if missing:
            print(f"ERROR: Firebase key missing fields: {missing}")
            return False
            
        print("SUCCESS: Firebase key structure is valid")
        return True
        
    except json.JSONDecodeError:
        print("ERROR: Firebase key is not valid JSON")
        return False
    except Exception as e:
        print(f"ERROR: Could not read Firebase key: {e}")
        return False

# Also replace the diagnostic call section:
# Run the diagnostic (remove Unicode characters from print statements)
if not diagnose_firebase_issue():
    print("FIREBASE ERROR: Firebase will not be available")

if FIREBASE_AVAILABLE and SERVICE_KEY:
    try:
        print(f"INFO: Initializing Firebase with {SERVICE_KEY}...")
        cred = credentials.Certificate(SERVICE_KEY)
        firebase_app = firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})
        users_ref = db.reference("users")
        slots_ref = db.reference("slots")
        print("INFO: Firebase initialized successfully")
        
        # Test connection with better error handling
        try:
            test_slots = slots_ref.get()
            if test_slots is None:
                print("INFO: No existing slots found - initializing fresh database")
            else:
                print(f"INFO: Firebase connection test successful. Found {len(test_slots)} slots.")
        except Exception as e:
            print(f"WARN: Firebase connection test failed: {e}")
            # Don't fail completely - continue with offline mode
            FIREBASE_AVAILABLE = False
            
        # Ensure slots node exists (slot1..slot4) with robust error handling
        if FIREBASE_AVAILABLE:
            for i in range(1, 5):
                slot_key = f"slot{i}"
                try:
                    slot_data = slots_ref.child(slot_key).get()
                    if slot_data is None:
                        slots_ref.child(slot_key).set({
                            "status": "inactive", 
                            "current_user": "none",
                            "last_updated": int(time.time() * 1000)
                        })
                        print(f"INFO: Created slot {slot_key}")
                    else:
                        print(f"INFO: Slot {slot_key} exists: {slot_data.get('status', 'unknown')}")
                except Exception as e:
                    print(f"WARN: Could not initialize slot {slot_key}: {e}")
                    # Continue with other slots
                    
    except Exception as e:
        print(f"ERROR: Firebase initialization failed: {e}")
        print("INFO: Falling back to offline mode")
        FIREBASE_AVAILABLE = False
        users_ref = None
        slots_ref = None
        if firebase_app:
            try:
                firebase_admin.delete_app(firebase_app)
            except:
                pass
            firebase_app = None
else:
    print("INFO: Running in offline mode - no Firebase connectivity")

# ----------------- Helper Functions -----------------
def user_exists(uid):
    if not FIREBASE_AVAILABLE or users_ref is None:
        return False
    try:
        return users_ref.child(uid).get() is not None
    except Exception as e:
        print(f"ERROR checking user existence: {e}")
        return False

def create_nonmember(uid):
    """Create a minimal user node as non-member (Guest)."""
    if not FIREBASE_AVAILABLE or users_ref is None:
        print(f"INFO: Offline mode - would create nonmember: {uid}")
        return
        
    try:
        users_ref.child(uid).set({
            "type": "nonmember",
            "name": "Guest",
            "student_id": "",
            "water_balance": None,
            "charge_balance": 0,
            "occupied_slot": "none",
            "charging_status": "idle",
            "slot_status": {}
        })
        print(f"INFO: Created nonmember user: {uid}")
    except Exception as e:
        print(f"ERROR creating nonmember: {e}")

def read_user(uid):
    if not FIREBASE_AVAILABLE or users_ref is None:
        # Return a mock user for offline testing
        return {
            "type": "nonmember",
            "name": "Guest",
            "student_id": "",
            "water_balance": None,
            "charge_balance": 0,
            "occupied_slot": "none",
            "charging_status": "idle",
            "slot_status": {}
        }
    
    try:
        user_data = users_ref.child(uid).get()
        if user_data is None:
            return {
                "type": "nonmember", 
                "name": "Guest",
                "student_id": "",
                "water_balance": None,
                "charge_balance": 0,
                "occupied_slot": "none",
                "charging_status": "idle",
                "slot_status": {}
            }
        return user_data
    except Exception as e:
        print(f"ERROR reading user {uid}: {e}")
        return None

def write_user(uid, data: dict):
    if not FIREBASE_AVAILABLE or users_ref is None:
        print(f"INFO: Offline mode - would update user {uid}: {data}")
        return
        
    try:
        users_ref.child(uid).update(data)
    except Exception as e:
        print(f"ERROR writing user {uid}: {e}")

def read_slot(slot):
    if not FIREBASE_AVAILABLE or slots_ref is None:
        # Return mock slot data for offline testing
        return {"status": "inactive", "current_user": "none"}
    
    try:
        slot_data = slots_ref.child(slot).get()
        if slot_data is None:
            return {"status": "inactive", "current_user": "none"}
        return slot_data
    except Exception as e:
        print(f"ERROR reading slot {slot}: {e}")
        return {"status": "inactive", "current_user": "none"}

def write_slot(slot, data: dict):
    if not FIREBASE_AVAILABLE or slots_ref is None:
        print(f"INFO: Offline mode - would update slot {slot}: {data}")
        return
        
    try:
        slots_ref.child(slot).update(data)
    except Exception as e:
        print(f"ERROR writing slot {slot}: {e}")

def seconds_to_min_display(sec):
    if sec is None:
        return "N/A"
    return f"{sec//60}m {sec%60}s"

def water_seconds_to_liters(sec):
    if sec is None:
        return "N/A"
    try:
        ml = float(sec or 0)
        liters = ml / 1000.0
        return f"{liters:.2f} L"
    except Exception:
        return "N/A"

# Mock append_audit_log if not available
if not FIREBASE_HELPERS_AVAILABLE:
    def append_audit_log(actor, action, meta=None):
        print(f"AUDIT: {actor} - {action} - {meta}")

# ----------------- Session Manager -----------------
class SessionManager:
    def __init__(self, controller):
        self.controller = controller
        self.sessions = {}  # slot -> session data
        
    def start_session(self, slot, uid, initial_balance):
        """Start a new charging session"""
        self.sessions[slot] = {
            'uid': uid,
            'remaining': initial_balance,
            'start_time': time.time(),
            'active': True
        }
        
    def stop_session(self, slot):
        """Stop a charging session"""
        if slot in self.sessions:
            self.sessions[slot]['active'] = False
            # Don't delete immediately for audit purposes
            
    def get_remaining_time(self, slot):
        """Get remaining time for a session"""
        if slot in self.sessions and self.sessions[slot]['active']:
            return self.sessions[slot]['remaining']
        return 0
        
    def update_remaining_time(self, slot, new_remaining):
        """Update remaining time for a session"""
        if slot in self.sessions:
            self.sessions[slot]['remaining'] = new_remaining

# ----------------- Tkinter UI -----------------
class KioskApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Smart Kiosk - Prototype (v2)")

        # === FULLSCREEN MODE for Raspberry Pi ===
        self.attributes("-fullscreen", True)     # fullscreen on boot
        self.config(cursor="none")               # hide cursor (kiosk mode)
        self.bind("<Escape>", lambda e: self.attributes("-fullscreen", False))  # ESC exit (debug)
        self.bind("<F11>", lambda e: self.attributes("-fullscreen", True))      # F11 restore fullscreen

        # Safe fallback geometry (for dev PC)
        self.geometry("800x480")
        self.minsize(640, 360)
        self.resizable(True, True)

        # current session variables
        self.active_uid = None
        self.active_slot = None
        self.charging_task = None
        self.water_task = None

        container = tk.Frame(self)
        # pack container so it expands with window
        container.pack(fill="both", expand=True)

        # configure expansion behavior for the container and child frames
        try:
            self.grid_rowconfigure(0, weight=1)
            self.grid_columnconfigure(0, weight=1)
        except Exception:
            pass
        try:
            container.grid_rowconfigure(0, weight=1)
            container.grid_columnconfigure(0, weight=1)
        except Exception:
            pass

        self.frames = {}
        for F in (ScanScreen, RegisterChoiceScreen, RegisterScreen, MainScreen, SlotSelectScreen, ChargingScreen, WaterScreen):
            frame = F(container, self)
            self.frames[F] = frame
            frame.grid(row=0, column=0, sticky="nsew")

        # instantiate hardware interface (available on Pi). Store on controller for screens to use.
        try:
            if _pinmap:
                self.hw = HardwareGPIO(pinmap=_pinmap, mode='auto')
                try:
                    self.hw.setup()
                except Exception:
                    pass
            else:
                self.hw = HardwareGPIO(pinmap=None, mode='sim')
                self.hw.setup()
        except Exception:
            # fallback to simulation
            self.hw = HardwareGPIO(pinmap=None, mode='sim')
            try:
                self.hw.setup()
            except Exception:
                pass

        # ========== FIXED ARDUINO LISTENER INITIALIZATION ==========
        self.arduino_listener = None
        self.arduino_available = False
        
        try:
            # Try multiple possible import paths
            try:
                from ArduinoListener import ArduinoListener
                ARDUINO_MODULE = "ArduinoListener"
            except ImportError as e:
                print(f"WARN: ArduinoListener import failed: {e}")
                ARDUINO_MODULE = None
            
            if ARDUINO_MODULE:
                print(f"INFO: Found ArduinoListener module: {ARDUINO_MODULE}")
                
                try:
                    # Use the correct parameter name 'event_callback'
                    self.arduino_listener = ArduinoListener(event_callback=self._arduino_event_callback)
                    print("INFO: ArduinoListener initialized with event_callback")
                    
                    if hasattr(self.arduino_listener, 'start'):
                        try:
                            self.arduino_listener.start()
                            self.arduino_available = True
                            print("INFO: ArduinoListener started successfully")
                        except Exception as start_error:
                            print(f"WARN: ArduinoListener start() failed: {start_error}")
                            self.arduino_available = False
                except Exception as e:
                    print(f"ERROR: ArduinoListener initialization failed: {e}")
                    self.arduino_listener = None
                    
            else:
                print("WARN: ArduinoListener module not found in any location")
                
        except Exception as e:
            print(f"ERROR: Unexpected error during ArduinoListener initialization: {e}")
            self.arduino_listener = None

        # Initialize ArduinoListener for water service hardware integration
        if not self.arduino_listener:
            print("INFO: ArduinoListener not available - using simulation mode for water service")

        # session manager for per-slot sessions
        try:
            self.session_manager = SessionManager(self)
        except Exception:
            self.session_manager = None

        # coin counters per-uid (list of inserted coin amounts)
        self.coin_counters = {}
        # Ensure the initial visible screen is the ScanScreen (raise it above others)
        try:
            self.show_frame(ScanScreen)
        except Exception:
            pass

    def send_arduino_command(self, command):
        """Safely send command to Arduino if available."""
        if not self.arduino_available or not self.arduino_listener:
            print(f"DEBUG: Arduino not available, cannot send: {command}")
            return False
            
        try:
            if hasattr(self.arduino_listener, 'send_command'):
                result = self.arduino_listener.send_command(command)
                print(f"INFO: Sent Arduino command: {command} -> {result}")
                return result
            elif hasattr(self.arduino_listener, 'write'):
                # Alternative method name
                result = self.arduino_listener.write(command)
                print(f"INFO: Sent Arduino command via write(): {command} -> {result}")
                return result
            else:
                print(f"WARN: ArduinoListener has no send_command or write method")
                return False
        except Exception as e:
            print(f"ERROR sending command to Arduino: {e}")
            return False
    
    def is_arduino_connected(self):
        """Check if Arduino is connected and responsive."""
        if not self.arduino_available or not self.arduino_listener:
            return False
            
        try:
            if hasattr(self.arduino_listener, 'is_connected'):
                return self.arduino_listener.is_connected()
            elif hasattr(self.arduino_listener, 'connected'):
                return self.arduino_listener.connected
            else:
                # If no connection check method, assume connected if initialized
                return True
        except Exception:
            return False

    def refresh_all_user_info(self):
        # iterate frames and refresh embedded UserInfoFrame instances
        try:
            for f in self.frames.values():
                try:
                    ui = getattr(f, 'user_info', None)
                    if ui is not None:
                        try:
                            ui.refresh()
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass

    def record_coin_insert(self, uid, amount, seconds):
        # Run UI-updates and popup on the Tk main loop to avoid thread issues
        def _handle():
            try:
                # track recent coin inserts for display
                # 'value' is generic: for WaterScreen it's mL, for Charging it's seconds
                rec = self.coin_counters.get(uid, {'coins': 0, 'value': 0, 'amount': 0})
                rec['coins'] += 1
                rec['value'] += seconds
                rec['amount'] += amount
                self.coin_counters[uid] = rec
            except Exception:
                rec = {'coins': 0, 'value': 0, 'amount': 0}
            # show a small popup summarizing the inserted coins and current balance for the active service
            try:
                user = read_user(uid) or {}
                # determine active service screen
                active = getattr(self, 'current_frame', None)
                if active == 'WaterScreen':
                    # water balance: members use water_balance (mL), non-members use temp_water_time (mL)
                    if user.get('type') == 'member':
                        bal = user.get('water_balance', 0) or 0
                    else:
                        bal = user.get('temp_water_time', 0) if user.get('temp_water_time', None) is not None else rec.get('value', 0)
                    # show liters conversion for readability
                    liters = (bal or 0) / 1000.0
                    # rec['amount'] is the total peso inserted; ensure it's an int
                    try:
                        peso_total = int(rec.get('amount', 0))
                    except Exception:
                        peso_total = rec.get('amount', 0)
                    msg = f"Coins inserted: ₱{peso_total}\nTotal water volume: {bal} mL (~{liters:.2f} L)"
                elif active in ('ChargingScreen', 'SlotSelectScreen'):
                    bal = user.get('charge_balance', 0) or 0
                    mins = bal // 60
                    secs = bal % 60
                    msg = f"Coins inserted: ₱{rec.get('amount',0)}\nCharging balance: {mins}m {secs}s"
                else:
                    # fallback: show both balances
                    wbal = user.get('water_balance', 0) or 0
                    cbal = user.get('charge_balance', 0) or 0
                    msg = (f"Coins inserted: ₱{rec.get('amount',0)}\n"
                           f"Water volume: {wbal} mL\nCharging time: {cbal} s")
                try:
                    messagebox.showinfo("Coin Inserted", msg)
                except Exception:
                    print(f"INFO: Coin Inserted popup: {msg}")
            except Exception:
                pass

        try:
            # schedule on main thread
            try:
                self.after(0, _handle)
            except Exception:
                # if after isn't available, run directly
                _handle()
        except Exception:
            pass

    def show_coin_popup(self, uid, peso: int = None, added_ml: int = None, total_ml: int = None):
        """Display a simple coin popup WITHOUT blocking UI updates."""
        def _do():
            # ONLY show popup for valid coins (1, 5, 10)
            valid_coins = [1, 5, 10]
            if peso not in valid_coins:
                print(f"D EBUG: Skipping popup for invalid coin: P{peso}")
                return
                
            parts = []
            if peso is not None:
                parts.append(f"Inserted: P{peso}")
            if added_ml is not None and added_ml > 0:
                parts.append(f"Added: {added_ml} mL")
            if total_ml is not None:
                parts.append(f"Total: {total_ml} mL")
            msg = "\n".join(parts) if parts else "Coin event"
            
            # Show popup but don't update balance here (already done above)
            try:
                messagebox.showinfo("Coin Inserted", msg)
                print(f"POPUP SHOWN: {msg}")
            except Exception as e:
                print(f"POPUP ERROR: {e} - {msg}")

        try:
            # Use short delay to ensure UI updates first
            self.after(100, _do)
        except Exception as e:
            print(f"Error scheduling popup: {e}")

    def show_totals_popup(self, uid, total_coins: int, total_credit_ml: int):
        """Show a simple totals popup: total inserted coins (₱) and total credit (mL)."""
        def _do_totals():
            try:
                parts = [f"Total Coins Inserted: ₱{total_coins}", f"Total Credit: {total_credit_ml} mL (~{total_credit_ml/1000.0:.2f} L)"]
                msg = "\n".join(parts)
                try:
                    messagebox.showinfo("Coin Totals", msg)
                except Exception:
                    print("POPUP:", msg)
            except Exception:
                pass

        try:
            self.after(0, _do_totals)
        except Exception:
            _do_totals()

    def _arduino_event_callback(self, event, value):
        """Enhanced central dispatcher for ArduinoListener events with real-time UI updates."""
        print(f"DEBUG: Arduino event received: {event} = {value}")
        
        try:
            # Handle COIN events centrally - they should work from any screen
            if event == 'coin' and value is not None:
                print(f"COIN DETECTED: P{value}")
                uid = self.active_uid
                if uid:
                    # Convert coin value to water mL based on your coin map
                    water_ml = {
                        1: 50,   # ₱1 = 50mL
                        5: 250,  # ₱5 = 250mL  
                        10: 500  # ₱10 = 500mL
                    }.get(value, 0)
                    
                    # UPDATE BALANCE FIRST (IMMEDIATELY)
                    user = read_user(uid)
                    if user:
                        current_frame = getattr(self, 'current_frame', None)
                        
                        if current_frame == 'WaterScreen':
                            # Water mode - update water balance
                            if user.get("type") == "member":
                                current_balance = user.get("water_balance", 0) or 0
                                new_balance = current_balance + water_ml
                                write_user(uid, {"water_balance": new_balance})
                                print(f"Updated member water balance: {current_balance} + {water_ml} = {new_balance}mL")
                            else:
                                current_balance = user.get("temp_water_time", 0) or 0
                                new_balance = current_balance + water_ml
                                write_user(uid, {"temp_water_time": new_balance})
                                print(f"Updated guest water balance: {current_balance} + {water_ml} = {new_balance}mL")
                            
                            # IMMEDIATELY update WaterScreen display
                            ws = self.frames.get(WaterScreen)
                            if ws:
                                try:
                                    ws.time_var.set(str(new_balance))
                                    if new_balance > 0:
                                        ws.status_lbl.config(text=f"Balance: {new_balance}mL - Place cup to start")
                                    else:
                                        ws.status_lbl.config(text="Insert coins to buy water")
                                    ws.update_idletasks()
                                except Exception as e:
                                    print(f"Error updating WaterScreen UI: {e}")
                        
                        elif current_frame in ('SlotSelectScreen', 'ChargingScreen'):
                            # Charging mode - update charging balance
                            charge_seconds = {
                                1: 60,   # ₱1 = 60 seconds
                                5: 300,  # ₱5 = 300 seconds  
                                10: 600  # ₱10 = 600 seconds
                            }.get(value, 0)
                            
                            if charge_seconds > 0:
                                current_balance = user.get("charge_balance", 0) or 0
                                new_balance = current_balance + charge_seconds
                                write_user(uid, {"charge_balance": new_balance})
                                print(f"Updated charging balance: {current_balance} + {charge_seconds} = {new_balance}s")
                                
                                # IMMEDIATELY update ChargingScreen display
                                cs = self.frames.get(ChargingScreen)
                                if cs:
                                    try:
                                        cs.time_var.set(str(new_balance))
                                        if hasattr(cs, 'remaining'):
                                            cs.remaining = new_balance
                                        cs.update_idletasks()
                                    except Exception as e:
                                        print(f"Error updating ChargingScreen UI: {e}")
                                
                                # Update SlotSelectScreen if active
                                ss = self.frames.get(SlotSelectScreen)
                                if ss and current_frame == 'SlotSelectScreen':
                                    try:
                                        ss.refresh()
                                    except Exception as e:
                                        print(f"Error updating SlotSelectScreen: {e}")
                        
                        # REFRESH ALL USER INFO (including top bar)
                        self.refresh_all_user_info()
                        
                        # THEN show the coin popup (after UI is updated)
                        if current_frame == 'WaterScreen':
                            self.show_coin_popup(uid, peso=value, added_ml=water_ml, total_ml=new_balance)
                        elif current_frame in ('SlotSelectScreen', 'ChargingScreen'):
                            charge_seconds = {1: 60, 5: 300, 10: 600}.get(value, 0)
                            if charge_seconds > 0:
                                mins = new_balance // 60
                                secs = new_balance % 60
                                msg = f"Coin inserted: ₱{value}\nCharging time: {mins}m {secs}s"
                                try:
                                    messagebox.showinfo("Coin Inserted", msg)
                                except Exception:
                                    print(f"POPUP: {msg}")
                    
                return  # Don't route coin events to screens
                
            # Route other events to appropriate screens
            if event in ['cup_detected', 'cup_removed', 'dispense_start', 'dispense_done', 'credit_left']:
                # Water-related events - forward to WaterScreen
                ws = self.frames.get(WaterScreen)
                if ws and hasattr(ws, 'handle_arduino_event'):
                    try:
                        ws.handle_arduino_event(event, value)
                    except Exception as e:
                        print(f"ERROR in WaterScreen event handler: {e}")
                else:
                    print(f"WARN: WaterScreen not available for event: {event}")
                    
            elif event in ['current_sensor', 'plug_status', 'charging_event']:
                # Charging-related events - forward to ChargingScreen  
                cs = self.frames.get(ChargingScreen)
                if cs and hasattr(cs, 'handle_arduino_event'):
                    try:
                        cs.handle_arduino_event(event, value)
                    except Exception as e:
                        print(f"ERROR in ChargingScreen event handler: {e}")
                else:
                    print(f"WARN: ChargingScreen not available for event: {event}")
                    
            else:
                print(f"INFO: Unhandled Arduino event type: {event} = {value}")
                
        except Exception as e:
            print(f"ERROR in Arduino event dispatcher: {e}")

    def show_frame(self, cls):
        # record current frame name for context (used by coin popups)
        try:
            self.current_frame = cls.__name__
        except Exception:
            self.current_frame = None
        frame = self.frames[cls]
        
        # Safe refresh - only if the frame has a refresh method
        if hasattr(frame, "refresh"):
            try:
                frame.refresh()
            except Exception as e:
                print(f"WARN: Error refreshing {cls.__name__}: {e}")
                
        # Enhanced Arduino mode switching with better error handling
        if self.arduino_available:
            try:
                # Water screen -> MODE WATER, Charging flow -> MODE CHARGE
                if cls.__name__ == 'WaterScreen':
                    if self.send_arduino_command('MODE WATER'):
                        print('INFO: Switched Arduino to WATER mode')
                    else:
                        print('WARN: Failed to switch Arduino to WATER mode')
                elif cls.__name__ in ('SlotSelectScreen', 'ChargingScreen'):
                    if self.send_arduino_command('MODE CHARGE'):
                        print('INFO: Switched Arduino to CHARGE mode')
                    else:
                        print('WARN: Failed to switch Arduino to CHARGE mode')
            except Exception as e:
                print(f'ERROR during Arduino mode switch: {e}')
                
        frame.tkraise()
    
    def cleanup(self):
        """Gracefully shutdown resources on app exit."""
        # Stop all background jobs in all frames
        try:
            for frame in self.frames.values():
                # Stop charging-related jobs
                if hasattr(frame, '_tick_job') and frame._tick_job:
                    try:
                        self.after_cancel(frame._tick_job)
                    except Exception:
                        pass
                if hasattr(frame, '_wait_job') and frame._wait_job:
                    try:
                        self.after_cancel(frame._wait_job)
                    except Exception:
                        pass
                if hasattr(frame, '_hw_monitor_job') and frame._hw_monitor_job:
                    try:
                        self.after_cancel(frame._hw_monitor_job)
                    except Exception:
                        pass
                if hasattr(frame, '_poll_timeout_job') and frame._poll_timeout_job:
                    try:
                        self.after_cancel(frame._poll_timeout_job)
                    except Exception:
                        pass
                # Stop water-related jobs
                if hasattr(frame, '_water_job') and frame._water_job:
                    try:
                        self.after_cancel(frame._water_job)
                    except Exception:
                        pass
                if hasattr(frame, '_water_nocup_job') and frame._water_nocup_job:
                    try:
                        self.after_cancel(frame._water_nocup_job)
                    except Exception:
                        pass
        except Exception as e:
            print(f"WARN: Error stopping background jobs: {e}")
        
        # Stop Arduino listener
        try:
            if self.arduino_available and self.arduino_listener is not None:
                if hasattr(self.arduino_listener, 'stop'):
                    self.arduino_listener.stop()
                    print("INFO: ArduinoListener stopped.")
                elif hasattr(self.arduino_listener, 'close'):
                    self.arduino_listener.close()
                    print("INFO: ArduinoListener closed.")
        except Exception as e:
            print(f"WARN: Error stopping ArduinoListener: {e}")

# --------- Screen: Scan (manual UID input) ----------
class ScanScreen(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#2c3e50")
        self.controller = controller
        tk.Label(self, text="Welcome to MSEUFCi Kiosk", font=("Arial", 28, "bold"),
                 fg="white", bg="#2c3e50").pack(pady=(20, 5))
        tk.Label(self, text="Enter RFID UID (type or paste) and press Scan", font=("Arial", 14),
                 fg="white", bg="#2c3e50").pack(pady=(0, 12))
        self.uid_entry = tk.Entry(self, font=("Arial", 18), width=36)
        self.uid_entry.pack(pady=5)
        # bind Enter key so USB RFID scanners that send an Enter will trigger scan
        self.uid_entry.bind('<Return>', lambda event: self.scan())
        btn_frame = tk.Frame(self, bg="#2c3e50")
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="Scan", font=("Arial", 16, "bold"),
                  bg="#27ae60", fg="white", width=14, command=self.scan).grid(row=0, column=0, padx=10)
        tk.Button(btn_frame, text="Clear", font=("Arial", 16, "bold"),
                  bg="#c0392b", fg="white", width=10, command=self.clear).grid(row=0, column=1, padx=10)
        self.info = tk.Label(self, text="", fg="white", bg="#2c3e50", font=("Arial", 12))
        self.info.pack(pady=(20,0))

    def clear(self):
        self.uid_entry.delete(0, tk.END)
        self.info.config(text="")

    def scan(self):
        uid = self.uid_entry.get().strip()
        if not uid:
            # no popup: show inline message on this screen
            try:
                self.info.config(text="Please enter an RFID UID.")
            except Exception:
                print("WARN: Please enter an RFID UID.")
            return

        if not user_exists(uid):
            # create minimal non-member then show register choice screen
            create_nonmember(uid)
            self.controller.active_uid = uid
            self.controller.show_frame(RegisterChoiceScreen)
            return

        # exists -> go to main (if non-member present, main will have Register-as-Member button)
        self.controller.active_uid = uid
        self.controller.show_frame(MainScreen)

    def refresh(self):
        # FIXED: Removed the problematic user_info refresh call
        # self.user_info.refresh()  # This line was causing the AttributeError
        
        # Clear any existing messages
        self.info.config(text="")
        
        # Test Arduino connection if needed
        self.test_arduino_connection()
        
    def test_arduino_connection(self):
        """Test Arduino connection status"""
        try:
            if hasattr(self.controller, 'arduino_available'):
                status = "Connected" if self.controller.arduino_available else "Disconnected"
                print(f"Arduino Status: {status}")
        except Exception:
            pass

# --------- Screen: RegisterChoice (for new UID) ----------
class RegisterChoiceScreen(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#34495e")
        self.controller = controller
        tk.Label(self, text="Card not registered", font=("Arial", 24, "bold"),
                 fg="white", bg="#34495e").pack(pady=20)
        tk.Label(self, text="Register now to be a Member with free allowances,\nor continue as Guest (non-member).",
                 font=("Arial", 14), fg="white", bg="#34495e").pack(pady=8)
        btn_frame = tk.Frame(self, bg="#34495e")
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="Register", font=("Arial", 14, "bold"),
                  bg="#16a085", fg="white", width=16, command=self.request_registration).grid(row=0, column=0, padx=10)
        tk.Button(btn_frame, text="Use as Guest", font=("Arial", 14, "bold"),
                  bg="#f39c12", fg="white", width=12, command=self.use_guest).grid(row=0, column=1, padx=10)
        tk.Button(btn_frame, text="Subscription", font=("Arial", 14, "bold"),
                  bg="#9b59b6", fg="white", width=12, command=self.request_subscription).grid(row=0, column=2, padx=10)

    def use_guest(self):
        # silent flow: proceed as guest without popup
        print("INFO: Proceeding as Guest (non-member).")
        self.controller.show_frame(MainScreen)

    def request_subscription(self):
        """Called when a not-registered user requests a subscription. This writes a subscription request
        entry in the DB so admins are notified in the dashboard.
        """
        uid = self.controller.active_uid
        if not uid:
            print("WARN: No UID in session. Scan first.")
            return
        # ensure a minimal non-member row exists
        if not user_exists(uid):
            create_nonmember(uid)
        # write a subscription_requests entry with timestamp and pending status
        if FIREBASE_AVAILABLE:
            try:
                req_ref = db.reference("subscription_requests")
                # use epoch milliseconds for timestamps
                ts = int(time.time() * 1000)
                req_ref.child(uid).set({
                    "timestamp": ts,
                    "status": "pending"
                })
                # audit
                try:
                    append_audit_log(actor=uid, action='subscription_request', meta={'uid': uid, 'ts': ts})
                except Exception:
                    pass
                # non-blocking notification
                print("INFO: Subscription request sent. Admin has been notified.")
            except Exception as e:
                print(f"ERROR: Failed to submit subscription request: {e}")
        else:
            print("INFO: Offline mode - subscription request simulated")
        # go to main screen (user can still use guest flows)
        self.controller.show_frame(MainScreen)

    def request_registration(self):
        """Called when a not-registered user taps Register on the kiosk.
        Instead of immediately creating a full member, create a registration_requests
        entry so admins are notified and can perform registration from the dashboard.
        """
        uid = self.controller.active_uid
        if not uid:
            print("WARN: No UID in session. Scan first.")
            return
        # ensure a minimal non-member row exists
        if not user_exists(uid):
            create_nonmember(uid)
        # write a registration_requests entry with timestamp and pending status
        if FIREBASE_AVAILABLE:
            try:
                req_ref = db.reference("registration_requests")
                # use epoch milliseconds for timestamps
                ts = int(time.time() * 1000)
                req_ref.child(uid).set({
                    "timestamp": ts,
                    "status": "pending"
                })
                # audit
                try:
                    append_audit_log(actor=uid, action='registration_request', meta={'uid': uid, 'ts': ts})
                except Exception:
                    pass
                print("INFO: Registration request sent. Admin has been notified.")
            except Exception as e:
                print(f"ERROR: Failed to submit registration request: {e}")
        else:
            print("INFO: Offline mode - registration request simulated")
        # return to main screen (user can still use guest flows)
        self.controller.show_frame(MainScreen)

    def refresh(self):
        pass

# --------- Screen: Register (for new members) ----------
class RegisterScreen(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#16a085")
        self.controller = controller
        tk.Label(self, text="Register New Member", font=("Arial", 22, "bold"),
                 fg="white", bg="#16a085").pack(pady=10)
        tk.Label(self, text="Name", font=("Arial", 14), fg="white", bg="#16a085").pack()
        self.name_entry = tk.Entry(self, font=("Arial", 16), width=30)
        self.name_entry.pack(pady=5)
        tk.Label(self, text="Student ID", font=("Arial", 14), fg="white", bg="#16a085").pack()
        self.id_entry = tk.Entry(self, font=("Arial", 16), width=30)
        self.id_entry.pack(pady=5)

        btn_frame = tk.Frame(self, bg="#16a085")
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="Register", font=("Arial", 14, "bold"),
                  bg="#2980b9", fg="white", width=12, command=self.register).grid(row=0, column=0, padx=10)
        tk.Button(btn_frame, text="Cancel", font=("Arial", 14, "bold"),
                  bg="#c0392b", fg="white", width=12, command=self.cancel).grid(row=0, column=1, padx=10)

        self.msg = tk.Label(self, text="", fg="white", bg="#16a085", font=("Arial", 12))
        self.msg.pack(pady=6)

    def register(self):
        uid = self.controller.active_uid
        name = self.name_entry.get().strip()
        sid = self.id_entry.get().strip()
        if not name or not sid:
            try:
                self.msg.config(text="Please provide name and student ID.")
            except Exception:
                print("WARN: Please provide name and student ID.")
            return
        # update DB -> member
        write_user(uid, {
            "type": "member",
            "name": name,
            "student_id": sid,
            "water_balance": DEFAULT_WATER_BAL,
            "charge_balance": DEFAULT_CHARGE_BAL,
            "occupied_slot": "none",
            "charging_status": "idle",
            "slot_status": {}
        })
        try:
            append_audit_log(actor=uid, action='register_member', meta={'uid': uid, 'name': name, 'student_id': sid})
        except Exception:
            pass
        print("INFO: Registration successful.")
        self.controller.show_frame(MainScreen)

    def cancel(self):
        # stay as nonmember and go to main
        self.controller.show_frame(MainScreen)

    def refresh(self):
        self.name_entry.delete(0, tk.END)
        self.id_entry.delete(0, tk.END)
        self.msg.config(text="")

# --------- Helper: top user details area ----------
class UserInfoFrame(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#222f3e", height=80)
        self.controller = controller
        self.pack_propagate(False)
        self.name_lbl = tk.Label(self, text="Name: -", font=("Arial", 14, "bold"), fg="white", bg="#222f3e")
        self.name_lbl.pack(anchor="w", padx=10, pady=4)
        self.info_lbl = tk.Label(self, text="UID: -    Student ID: -", font=("Arial", 12), fg="white", bg="#222f3e")
        self.info_lbl.pack(anchor="w", padx=10)
        self.bal_lbl = tk.Label(self, text="Water: -    Charge: -", font=("Arial", 12), fg="white", bg="#222f3e")
        self.bal_lbl.pack(anchor="w", padx=10, pady=(0,6))

    def refresh(self):
        uid = self.controller.active_uid
        if not uid:
            self.name_lbl.config(text="Name: -")
            self.info_lbl.config(text="UID: -    Student ID: -")
            self.bal_lbl.config(text="Water: -    Charge: -")
            return
        user = read_user(uid)
        if not user:
            self.name_lbl.config(text="Name: -")
            self.info_lbl.config(text=f"UID: {uid}    Student ID: -")
            self.bal_lbl.config(text="Water: -    Charge: -")
            return
        # show Guest if non-member
        name = user.get("name", "Guest") if user.get("type") == "member" else "Guest"
        sid = user.get("student_id", "")
        # For members, show water_balance; for non-members, show any temporary purchased water (persisted as temp_water_time)
        if user.get("type") == "member":
            wbal = user.get("water_balance", None)
        else:
            wbal = user.get("temp_water_time", 0) or 0
        cbal = user.get("charge_balance", 0)
        self.name_lbl.config(text=f"Name: {name}")
        self.info_lbl.config(text=f"UID: {uid}    Student ID: {sid if sid else '-'}")
        self.bal_lbl.config(text=f"Water: {water_seconds_to_liters(wbal)}    Charge: {seconds_to_min_display(cbal)}")

# --------- Screen: Main / Balance & Options ----------
class MainScreen(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#34495e")
        self.controller = controller
        # top user info
        self.user_info = UserInfoFrame(self, controller)
        self.user_info.pack(fill="x")

        body = tk.Frame(self, bg="#34495e")
        body.pack(fill="both", expand=True, pady=8)
        tk.Label(body, text="Select Service", font=("Arial", 24, "bold"), fg="white", bg="#34495e").pack(pady=12)
        btn_frame = tk.Frame(body, bg="#34495e")
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="Water Vendo", font=("Arial", 18, "bold"),
                  bg="#2980b9", fg="white", width=20, height=2, command=lambda: controller.show_frame(WaterScreen)).grid(row=0, column=0, padx=10, pady=8)
        tk.Button(btn_frame, text="Phone Charging", font=("Arial", 18, "bold"),
                  bg="#27ae60", fg="white", width=20, height=2, command=lambda: controller.show_frame(SlotSelectScreen)).grid(row=0, column=1, padx=10, pady=8)

        # small register as member (for existing non-members)
        self.register_small = tk.Button(self, text="Register as Member", font=("Arial", 10, "underline"),
                                        fg="white", bg="#34495e", bd=0, command=self.goto_register)
        # Unlock-my-slot shortcut (created but not packed here; shown only for the user who owns a slot)
        self.unlock_my_slot = tk.Button(self, text="", font=("Arial", 12, "bold"),
                                        bg="#f39c12", fg="white", command=self._unlock_my_slot)
        # End-charging shortcut (ends charging session and releases slot)
        self.end_session_btn = tk.Button(self, text="End Charging Session", font=("Arial", 12, "bold"),
                                         bg="#c0392b", fg="white", command=self._end_charging_session)
        self.register_small.pack(side="bottom", pady=10)

        tk.Button(self, text="Logout", font=("Arial", 12, "bold"), bg="#c0392b", fg="white",
                  command=self.logout).pack(side="bottom", pady=6)

    def goto_register(self):
        uid = self.controller.active_uid
        if not uid:
            print("WARN: No user; scan first.")
            return
        user = read_user(uid)
        if user and user.get("type") == "member":
            print("INFO: User is already a member.")
            return
        # Instead of opening a local registration UI, submit a registration request
        # to the admin dashboard via the Realtime Database so admins can approve.
        try:
            if FIREBASE_AVAILABLE:
                req_ref = db.reference(f"registration_requests/{uid}")
                existing = req_ref.get()
                if existing and existing.get('status') == 'pending':
                    print("INFO: Registration request already pending.")
                    return
                ts = int(time.time() * 1000)
                req_ref.set({
                    'timestamp': ts,
                    'status': 'pending'
                })
                # add an audit entry so admins get a clear log
                append_audit_log(actor=uid, action='registration_request', meta={'ts': ts, 'uid': uid})
                print("INFO: Registration request submitted. An admin will review it shortly.")
                # disable the button locally until admins process the request
                self.register_small.config(text='Registration Requested', state='disabled')
            else:
                print("INFO: Offline mode - registration request simulated")
                self.register_small.config(text='Registration Requested', state='disabled')
        except Exception as e:
            print('Error submitting registration request:', e)
            print('ERROR: Failed to submit registration request. Please try again later.')

    def logout(self):
        """Safe logout that handles refresh errors"""
        # Clear the active UID
        self.controller.active_uid = None
        
        # Use try-except to handle any refresh errors
        try:
            self.controller.show_frame(ScanScreen)
        except Exception as e:
            print(f"WARN: Error during logout: {e}")
            # Fallback: directly show the scan screen
            scan_frame = self.controller.frames.get(ScanScreen)
            if scan_frame:
                scan_frame.tkraise()

    def refresh(self):
        self.user_info.refresh()
        # show register button only if user exists and is non-member
        uid = self.controller.active_uid
        if uid:
            user = read_user(uid)
            # check registration request status to update button text/state
            if FIREBASE_AVAILABLE:
                try:
                    req = db.reference(f"registration_requests/{uid}").get()
                except Exception:
                    req = None
            else:
                req = None

            if user and user.get("type") == "nonmember":
                self.register_small.lift()
                # default to allowing registration request
                btn_text = 'Register as Member'
                btn_state = 'normal'
                if req:
                    status = req.get('status', '')
                    if status == 'pending':
                        btn_text = 'Registration Requested'
                        btn_state = 'disabled'
                    elif status == 'assigned':
                        btn_text = 'Registered'
                        btn_state = 'disabled'
                    elif status in ('dismissed', 'rejected'):
                        btn_text = 'Register as Member'
                        btn_state = 'normal'
                self.register_small.config(text=btn_text, state=btn_state)
            else:
                self.register_small.config(state="disabled")
            # synchronize controller.active_slot with persisted value so the app knows
            occ = user.get("occupied_slot", "none") if user else "none"
            # normalize empty/None
            if not occ:
                occ = "none"
            # set controller state for the active slot belonging to this user
            if occ != "none":
                self.controller.active_slot = occ
                # show the unlock-my-slot shortcut
                self.unlock_my_slot.config(text=f"Unlock {occ}")
                try:
                    # pack the button above the register_small button
                    self.unlock_my_slot.pack(side="bottom", pady=4)
                    # show end session button just above unlock button
                    self.end_session_btn.pack(side="bottom", pady=4)
                except Exception:
                    pass
            else:
                # hide unlock button if not owning a slot
                try:
                    self.unlock_my_slot.pack_forget()
                    self.end_session_btn.pack_forget()
                except Exception:
                    pass
        else:
            self.register_small.config(state="disabled")
            try:
                self.unlock_my_slot.pack_forget()
            except Exception:
                pass

    def _end_charging_session(self):
        """End charging session for the currently logged-in user.
        Prefer calling ChargingScreen.stop_session() to ensure local tick cleanup; fall back to DB updates.
        """
        uid = self.controller.active_uid
        if not uid:
            print("WARN: No user; scan first.")
            return
        # attempt to call stop_session on the ChargingScreen instance so local timers are cancelled
        try:
            charging_frame = self.controller.frames.get(ChargingScreen)
            if charging_frame:
                # call stop_session which already handles DB writes and local tick cancellation
                charging_frame.stop_session()
                return
        except Exception:
            pass
        # fallback: perform DB updates directly
        user = read_user(uid)
        slot = (user.get("occupied_slot") if user else None) or "none"
        write_user(uid, {"charging_status": "idle", "occupied_slot": "none"})
        if slot != "none":
            write_slot(slot, {"status": "inactive", "current_user": "none"})
            if FIREBASE_AVAILABLE and users_ref:
                users_ref.child(uid).child("slot_status").update({slot: "inactive"})
        self.controller.active_slot = None
        print("INFO: Charging session ended.")

    def _unlock_my_slot(self):
        """Called when the logged-in user taps the Unlock Slot shortcut on MainScreen.
        This only unlocks the slot assigned to the currently logged-in user and does not stop charging.
        """
        uid = self.controller.active_uid
        if not uid:
            print("WARN: No user; scan first.")
            return
        user = read_user(uid)
        if not user:
            print("WARN: User record missing.")
            return
        slot = user.get("occupied_slot", "none") or "none"
        if slot == "none":
            print("INFO: No slot assigned to this user.")
            try:
                self.unlock_my_slot.pack_forget()
            except Exception:
                pass
            return
        # Update DB to unlock the slot but keep the user's charging_status untouched
        write_slot(slot, {"status": "inactive", "current_user": "none"})
        if FIREBASE_AVAILABLE and users_ref:
            users_ref.child(uid).child("slot_status").update({slot: "inactive"})
        write_user(uid, {"occupied_slot": "none"})
        print(f"INFO: {slot} unlocked. You may unplug your device.")
        # update controller and UI
        self.controller.active_slot = None
        try:
            self.unlock_my_slot.pack_forget()
        except Exception:
            pass

# --------- Screen: Slot Selection (1-5) ----------
class SlotSelectScreen(tk.Frame):
    def __init__(self, parent, controller):
        # change background to match MainScreen
        super().__init__(parent, bg="#34495e")
        self.controller = controller
        # top user info so details persist while selecting slot
        self.user_info = UserInfoFrame(self, controller)
        self.user_info.pack(fill="x")

        tk.Label(self, text="Select Charging Slot", font=("Arial", 22, "bold"),
                 fg="white", bg="#34495e").pack(pady=6)
        # allow adding coins before selecting slot (coins shown here per request)
        hw = getattr(controller, 'hw', None)
        self.coin_frame_top = tk.LabelFrame(self, text=("Coinslot - add charge before slot" if hw else "Coinslot (simulate) - add charge before slot"), font=("Arial", 12, "bold"),
                                            fg="white", bg="#34495e", bd=2, labelanchor="n")
        self.coin_frame_top.pack(pady=6)
        # status label to show recent coin inserts and expected time
        # Use grid for children inside the coin frame to avoid mixing pack/grid
        self.coin_status_lbl = tk.Label(self.coin_frame_top, text="", fg="white", bg="#34495e")
        # place status on the first row spanning available columns
        self.coin_status_lbl.grid(row=0, column=0, columnspan=3, pady=(4, 0))
        if not hw:
            # place coin buttons on the second row
            tk.Button(self.coin_frame_top, text="₱1", font=("Arial", 12, "bold"), bg="#f39c12", fg="white", width=8,
                      command=lambda: self.insert_coin(1)).grid(row=1, column=0, padx=6, pady=6)
            tk.Button(self.coin_frame_top, text="₱5", font=("Arial", 12, "bold"), bg="#e67e22", fg="white", width=8,
                      command=lambda: self.insert_coin(5)).grid(row=1, column=1, padx=6, pady=6)
            tk.Button(self.coin_frame_top, text="₱10", font=("Arial", 12, "bold"), bg="#d35400", fg="white", width=8,
                      command=lambda: self.insert_coin(10)).grid(row=1, column=2, padx=6, pady=6)
        else:
            tk.Label(self.coin_frame_top, text="Hardware coin acceptor active — use physical coins/cards", fg="white", bg="#34495e").grid(row=1, column=0, columnspan=3, pady=6)

        self.slot_buttons = {}
        grid = tk.Frame(self, bg="#34495e")
        grid.pack(pady=8)
        for i in range(1, 5):
            btn = tk.Button(grid, text=f"Slot {i}\n(Checking...)", font=("Arial", 14, "bold"),
                            bg="#95a5a6", fg="black", width=14, height=2,
                            command=lambda s=i: self.select_slot(s))
            btn.grid(row=(i-1)//3, column=(i-1)%3, padx=10, pady=8)
            self.slot_buttons[f"slot{i}"] = btn
        # move Back button up so it is visible on non-fullscreen displays
        tk.Button(self, text="Back", font=("Arial", 14, "bold"), bg="#c0392b", fg="white",
                  command=lambda: controller.show_frame(MainScreen)).pack(pady=6, anchor='nw', padx=8)

    def refresh(self):
        # refresh user info and slot statuses
        self.user_info.refresh()
        # only show the top coin frame when no slot currently assigned to this session
        if self.controller.active_slot:
            try:
                self.coin_frame_top.pack_forget()
            except Exception:
                pass
        else:
            try:
                self.coin_frame_top.pack(pady=6)
            except Exception:
                pass
        for i in range(1, 5):
            key = f"slot{i}"
            slot = read_slot(key)
            # Default values
            text = f"Slot {i}\nFree"
            color = "#2ecc71"  # green for free
            try:
                if slot is None:
                    # keep defaults
                    pass
                else:
                    status = slot.get("status", "inactive")
                    cur = slot.get("current_user", "none")
                    uid = self.controller.active_uid
                    if cur != "none":
                        # assigned to someone
                        if cur == uid:
                            # assigned to current user
                            text = f"Slot {i}\nIn Use"
                            color = "#95a5a6"  # neutral
                        else:
                            text = f"Slot {i}\nOccupied"
                            color = "#e74c3c"  # red
                    else:
                        # no current_user assigned
                        if status == "active":
                            text = f"Slot {i}\nIn Use"
                            color = "#e74c3c"
                        else:
                            text = f"Slot {i}\nFree"
                            color = "#2ecc71"
            except Exception:
                # on any error keep defaults
                text = f"Slot {i}\nFree"
                color = "#2ecc71"
            try:
                self.slot_buttons[key].config(text=text, bg=color)
            except Exception:
                pass
        # show coin status for current user if any
        try:
            uid = self.controller.active_uid
            if uid:
                rec = self.controller.coin_counters.get(uid)
                if rec:
                    # show value: for water this is mL, for charging it's seconds
                    val = rec.get('value', 0)
                    # if user is member and this is water screen we show mL; otherwise show seconds
                    self.coin_status_lbl.config(text=f"Coins inserted: {rec.get('coins',0)} (≈ {val})")
                else:
                    self.coin_status_lbl.config(text="")
            else:
                self.coin_status_lbl.config(text="")
        except Exception:
            pass

    def select_slot(self, i):
        uid = self.controller.active_uid
        if not uid:
            print("WARN: No user; scan first before selecting a slot.")
            return
        # require a positive charge balance before allowing slot assignment
        user = read_user(uid)
        cb = user.get("charge_balance", 0) if user else 0
        if (cb or 0) <= 0:
            print("WARN: No charge balance; insert coins before selecting a slot.")
            return
        slot_key = f"slot{i}"
        slot = read_slot(slot_key)
        # if the slot is already active or assigned to someone else, prevent selection
        if slot is not None:
            cur = slot.get("current_user", "none")
            status = slot.get("status", "inactive")
            if cur != "none" and cur != uid:
                print(f"WARN: {slot_key} is already assigned to another user.")
                return
            if status == "active" and cur != uid:
                print(f"WARN: {slot_key} is currently in use. Please choose another slot.")
                return
        # assign slot to user (assigned, not active)
        write_user(uid, {"occupied_slot": slot_key})
        if FIREBASE_AVAILABLE and users_ref:
            users_ref.child(uid).child("slot_status").update({slot_key: "inactive"})
        write_slot(slot_key, {"status": "inactive", "current_user": uid})
        try:
            append_audit_log(actor=uid, action='assign_slot', meta={'slot': slot_key})
        except Exception:
            pass
        self.controller.active_slot = slot_key
        # disable/hide top coin slot to prevent adding coins after selection (require top-up beforehand)
        try:
            self.coin_frame_top.pack_forget()
        except Exception:
            pass
        print(f"INFO: You selected {slot_key}. Please plug your device and press Start Charging.")
        self.controller.show_frame(ChargingScreen)

    def insert_coin(self, amount):
        """Add charging credit with immediate UI update."""
        uid = self.controller.active_uid
        if not uid:
            print("WARN: No user; scan first.")
            return
            
        add = COIN_MAP.get(amount, 0)
        user = read_user(uid)
        newbal = (user.get("charge_balance", 0) or 0) + add
        write_user(uid, {"charge_balance": newbal})
        
        # UPDATE UI IMMEDIATELY
        self.time_var.set(str(newbal))
        if hasattr(self, 'remaining'):
            self.remaining = newbal
        
        print(f"INFO: ₱{amount} added => {add} seconds to charging balance.")
        
        # Record coin and refresh globally
        try:
            self.controller.record_coin_insert(uid, amount, add)
            self.controller.refresh_all_user_info()
        except Exception:
            pass


# --------- Screen: Charging ----------
class ChargingScreen(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#34495e")
        self.controller = controller
        # user info visible while charging
        self.user_info = UserInfoFrame(self, controller)
        self.user_info.pack(fill="x")

        body = tk.Frame(self, bg="#34495e")
        # expand so the main content can be centered vertically between the user info above and buttons below
        body.pack(expand=True, fill='both', pady=12)

        # Large centered header that shows the active charging slot (e.g. "Charging Slot 4")
        self.slot_lbl = tk.Label(body, text="Charging Slot -", font=("Arial", 28, "bold"), fg="white", bg="#34495e")
        # place the header slightly above center and give breathing room before the time label
        self.slot_lbl.pack(pady=(20, 12))

        # Time display (kept below the header)
        self.time_var = tk.StringVar(value="0")
        tk.Label(body, text="Time Left (sec)", font=("Arial", 14), fg="white", bg="#34495e").pack(pady=(6, 2))
        tk.Label(body, textvariable=self.time_var, font=("Arial", 28, "bold"), fg="white", bg="#34495e").pack(pady=(0, 12))

        btn_frame = tk.Frame(body, bg="#34495e")
        btn_frame.pack(pady=8)
        # Back button: allow user to return to Main screen while charging continues
        tk.Button(btn_frame, text="Back", font=("Arial", 12, "bold"),
                  bg="#95a5a6", fg="white", width=10, command=lambda: controller.show_frame(MainScreen)).grid(row=0, column=0, padx=6)
        # Start/Unlock/Stop controls: aligned in a single row for a clean layout
        tk.Button(btn_frame, text="Start Charging", font=("Arial", 14, "bold"),
                  bg="#2980b9", fg="white", width=14, command=self.start_charging).grid(row=0, column=1, padx=6)
        tk.Button(btn_frame, text="Unlock Slot", font=("Arial", 14, "bold"),
                  bg="#f39c12", fg="white", width=14, command=self.unlock_slot).grid(row=0, column=2, padx=6)
        tk.Button(btn_frame, text="Stop Session", font=("Arial", 12, "bold"),
                  bg="#c0392b", fg="white", width=14, command=self.stop_session).grid(row=0, column=3, padx=6)

        # local countdown state
        self.db_acc = 0
        self.is_charging = False
        self.unplug_time = None
        self.remaining = 0  # local remaining seconds for responsive countdown
        self._tick_job = None
        # hardware/monitoring
        self.tm = None
        self._wait_job = None
        self._hw_monitor_job = None
        self._poll_timeout_job = None
        # consecutive-sample counter to avoid spurious single-sample triggers
        self._charge_consecutive = 0
        # rolling sample buffers / hit timestamps for threshold-based detection
        self._charge_samples = []
        # timestamps when plug-threshold was observed
        self._plug_hits = []
        # timestamps when unplug (below threshold) was observed while charging
        self._unplug_hits = []
        # uid that started the charging session. Stored so background ticks/monitors
        # continue even if the user logs out from the UI (controller.active_uid cleared).
        self.charging_uid = None
        # the specific slot this ChargingScreen instance is currently managing.
        # Set when start_charging() is called so timers/monitors act only on the
        # intended slot even if controller.active_slot changes while session runs.
        self.charging_slot = None
        # session validity flag: if False, all background timers should stop immediately
        # This ensures old sessions stop even if timer callbacks are queued in the event loop
        self._session_valid = False
        # unique session ID that increments on each session start
        # Used to prevent old queued callbacks from updating display with stale data
        self._session_id = 0
        self._current_session_id = None

    def _get_session_uid(self):
        """Return the uid owning the active charging session.
        Priority: explicit charging_uid set at start_charging -> controller.active_uid -> slot.current_user in DB.
        """
        uid = getattr(self, 'charging_uid', None) or getattr(self.controller, 'active_uid', None)
        if uid:
            return uid
        # try to infer from the assigned slot in the DB
        slot = getattr(self.controller, 'active_slot', None)
        if slot:
            try:
                s = read_slot(slot)
                if s:
                    return s.get('current_user')
            except Exception:
                pass
        return None

    def refresh(self):
        uid = self.controller.active_uid
        slot = self.controller.active_slot or "none"
        
        # CRITICAL: If the active user has changed (different from the session owner),
        # IMMEDIATELY INVALIDATE THE SESSION to stop all background timers
        if uid and self.charging_uid and uid != self.charging_uid:
            print(f"[CHARGING] User changed: was {self.charging_uid}, now {uid}. Invalidating session and resetting ChargingScreen state.")
            # Mark session as invalid so any running callbacks will exit immediately
            self._session_valid = False
            # Cancel all running timers
            try:
                if self._tick_job is not None:
                    self.after_cancel(self._tick_job)
                    self._tick_job = None
            except Exception:
                pass
            try:
                if self._wait_job is not None:
                    self.after_cancel(self._wait_job)
                    self._wait_job = None
            except Exception:
                pass
            try:
                if self._hw_monitor_job is not None:
                    self.after_cancel(self._hw_monitor_job)
                    self._hw_monitor_job = None
            except Exception:
                pass
            try:
                if self._poll_timeout_job is not None:
                    self.after_cancel(self._poll_timeout_job)
                    self._poll_timeout_job = None
            except Exception:
                pass
            # Reset all state variables
            self.is_charging = False
            self.charging_uid = None
            self.charging_slot = None
            self.remaining = 0
            self.db_acc = 0
            self.unplug_time = None
            self.tm = None
            self._charge_consecutive = 0
            self._charge_samples = []
            self._plug_hits = []
            self._unplug_hits = []
            # CRITICAL: Immediately clear the time display to prevent showing old session's remaining time
            try:
                self.time_var.set("0")
            except Exception:
                pass
        
        # Display a concise charging label. Show "Charging Slot X" instead of 'In use' or 'Occupied'.
        display_text = f"Charging Slot {slot[4:] if slot and slot.startswith('slot') else slot}"
        display_bg = self.cget('bg')
        self.slot_lbl.config(text=display_text, bg=display_bg)
        # ensure top user details update when charging screen appears
        try:
            self.user_info.refresh()
        except Exception:
            pass
        # If this screen is shown fresh, ensure session totals are initialized
        try:
            if not hasattr(self, 'total_coins'):
                self.total_coins = 0
            if not hasattr(self, 'total_credit'):
                self.total_credit = 0
        except Exception:
            pass
        if uid:
            user = read_user(uid)
            cb = user.get("charge_balance", 0) or 0
            self.time_var.set(str(cb))
            # keep local remaining in sync when not actively charging
            # If DB reports charging_status == 'charging' AND this is OUR session, ensure local tick loop is running
            if user.get("charging_status") == "charging" and self.charging_uid == uid:
                # if we are not currently running a local tick, start one so time continues while user navigates
                if not self.is_charging:
                    self._session_valid = True  # Mark session as valid before starting
                    self.is_charging = True
                    self.remaining = cb
                    self.db_acc = 0
                    if self._tick_job is None:
                        self._charging_tick()
                else:
                    # already charging locally; keep remaining in sync when tick isn't running
                    if self._tick_job is None:
                        self.remaining = cb
            else:
                if not self.is_charging and self._tick_job is None:
                    # sync remaining only if no active tick loop
                    self.remaining = cb
        else:
            self.time_var.set("0")
        # do not change is_charging or unplug_time here; refresh should be safe to call

    def insert_coin(self, amount):
        uid = self.controller.active_uid
        if not uid:
            print("WARN: No user; scan first.")
            return
        add = COIN_MAP.get(amount, 0)
        user = read_user(uid)
        newbal = (user.get("charge_balance", 0) or 0) + add
        write_user(uid, {"charge_balance": newbal})
        try:
            append_audit_log(actor=uid, action='insert_coin', meta={'amount': amount, 'added_seconds': add, 'new_balance': newbal})
        except Exception:
            pass
        print(f"INFO: ₱{amount} added => {add} seconds.")
        # if currently charging, also update the responsive remaining timer
        if self.is_charging:
            self.remaining += add
            self.time_var.set(str(self.remaining))
            # if a session manager has the active slot, update session remaining too
            try:
                sm = getattr(self.controller, 'session_manager', None)
                slot = self.controller.active_slot
                if sm and slot and slot in sm.sessions:
                    sm.sessions[slot]['remaining'] = sm.sessions[slot].get('remaining', 0) + add
            except Exception:
                pass
        else:
            self.refresh()
        # record coin insert and refresh UI globally
        try:
            self.controller.record_coin_insert(uid, amount, add)
            self.controller.refresh_all_user_info()
        except Exception:
            pass

    def start_charging(self):
        uid = self.controller.active_uid
        if not uid:
            print("WARN: No user; scan first.")
            return
        user = read_user(uid)
        cb = user.get("charge_balance", 0) or 0
        if cb <= 0:
            print("WARN: No charge balance; please add coins to charging balance.")
            return
        slot = self.controller.active_slot
        # remember session owner so background timers continue even if UI user logs out
        self.charging_uid = uid
        # Mark session as VALID so timer callbacks will execute
        self._session_valid = True
        # Generate new unique session ID to invalidate old queued callbacks
        self._session_id += 1
        self._current_session_id = self._session_id
        # bind this charging UI instance to the selected slot so subsequent timers/monitors
        # target the correct slot even if controller.active_slot changes while the session runs
        self.charging_slot = slot
        # For hardware-driven slots (slot1), do not mark DB as 'charging' yet because
        # the session must only start when current is detected. Mark 'pending' so
        # other systems know the user requested charging. For non-hardware or fallback
        # start immediately mark as 'charging'.

        hw = getattr(self.controller, 'hw', None)
        try:
            # if this slot has hardware mappings (ACS712 channel + relay), mark pending and wait for detection
            hw_supported = bool(slot and hw is not None and slot in (hw.pinmap.get('acs712_channels') or {}))
            if hw_supported:
                write_user(uid, {"charging_status": "pending"})
            else:
                write_user(uid, {"charging_status": "charging"})
        except Exception:
            pass
        try:
            append_audit_log(actor=uid, action='start_charging', meta={'slot': slot})
        except Exception:
            pass
        if slot:
            if FIREBASE_AVAILABLE and users_ref:
                users_ref.child(uid).child("slot_status").update({slot: "active"})
            write_slot(slot, {"status": "active", "current_user": uid})

        # prepare local state but if hardware available for slot1, enable power and wait for current
        self.db_acc = 0
        self.remaining = cb
        self.time_var.set(str(self.remaining))

        # hw already assigned above; continue with hardware path if this slot is hardware-mapped
        if slot and hw is not None and slot in (hw.pinmap.get('acs712_channels') or {}):
            # Cancel any running countdown/ticks so timer doesn't run while waiting for plug
            try:
                if self._tick_job is not None:
                    try:
                        self.after_cancel(self._tick_job)
                    except Exception:
                        pass
                    self._tick_job = None
            except Exception:
                pass
            self.is_charging = False
            self.unplug_time = None

            # power the slot so the user can plug in
            # Ensure ACS712 baseline is calibrated (same behavior as test_slot1)
            try:
                if slot not in getattr(hw, '_baseline', {}):
                    try:
                        # non-blocking calibration notice
                        print(f"INFO: Calibrating current sensor for {slot}. Ensure nothing is plugged into the port.")
                    except Exception:
                        pass
                    try:
                        cal = hw.calibrate_zero(slot, samples=30, delay=0.05)
                        print('Calibration result:', cal)
                    except Exception as e:
                        print('Calibration failed:', e)
            except Exception:
                pass
            try:
                hw.relay_on(slot)
            except Exception:
                pass
            # init TM1637 display for countdown (if present) - use the specific slot's display
            try:
                if hasattr(hw, 'tm1637_init_slot'):
                    self.tm = hw.tm1637_init_slot(slot)
                else:
                    # fallback for older hardware_gpio without per-slot init
                    self.tm = hw.tm1637_init()
                if hasattr(self.tm, 'set_brightness'):
                    try:
                        self.tm.set_brightness(1)
                    except Exception:
                        pass
            except Exception:
                self.tm = None

            # Unlock slot for a short window (5 seconds) to allow plugging
            try:
                hw.lock_slot(slot, lock=True)
            except Exception:
                pass
            try:
                self.slot_lbl.config(text=f"{slot} - UNLOCKED: please plug in (5s)")
            except Exception:
                pass

            def _end_unlock_and_start_poll():
                try:
                    hw.lock_slot(slot, lock=False)
                except Exception:
                    pass
                try:
                    self.slot_lbl.config(text=f"{slot} - Waiting for device...")
                except Exception:
                    pass
                # start polling loop to detect charging start
                # reset consecutive-sample counter and rolling sample buffer, then schedule the polling loop
                self._charge_consecutive = 0
                try:
                    self._charge_samples = []
                    self._plug_hits = []
                    self._unplug_hits = []
                except Exception:
                    pass
                if self._wait_job is None:
                    try:
                        # sample every 500ms so we can collect 4 samples within 2s as requested
                        self._wait_job = self.after(500, self._poll_for_charging_start)
                    except Exception:
                        self._wait_job = None
                # start a 1-minute timeout: if no charging detected within 60s, end session
                try:
                    if self._poll_timeout_job is not None:
                        try:
                            self.after_cancel(self._poll_timeout_job)
                        except Exception:
                            pass
                        self._poll_timeout_job = None
                    self._poll_timeout_job = self.after(60000, self._poll_no_detect_timeout)
                except Exception:
                    self._poll_timeout_job = None

            try:
                self.after(5000, _end_unlock_and_start_poll)
            except Exception:
                if self._wait_job is None:
                    self._wait_job = self.after(1000, self._poll_for_charging_start)
            return

        # fallback: begin charging immediately (no hardware)
        self.is_charging = True
        if self._tick_job is None:
            self._charging_tick()

        # Reset the plug start time when charging starts successfully
        if hasattr(self, '_plug_start_time'):   
            del self._plug_start_time

    def _charging_tick(self):
        """Improved charging tick with better state management"""
        self._tick_job = None
        
        # Enhanced safety checks
        if not getattr(self, '_session_valid', False):
            print("[TICK] Session invalid, stopping")
            return
            
        if not self.is_charging:
            print("[TICK] Not charging, stopping tick")
            return
            
        my_session_id = getattr(self, '_current_session_id', None)
        if my_session_id != self._current_session_id:
            print("[TICK] Session ID mismatch, stopping")
            return
            
        uid = self._get_session_uid()
        if not uid:
            print("[TICK] No UID, stopping")
            return

        # Check if we should be charging (device plugged)
        if self.unplug_time and (time.time() - self.unplug_time) >= UNPLUG_GRACE_SECONDS:
            print("[TICK] Unplug timeout reached in tick, stopping")
            self.stop_session()
            return
            
        # Only count down if actually charging (not in unplug grace period)
        if not self.unplug_time:
            t = self.remaining
            if t <= 0:
                print("[TICK] Time finished")
                self._end_charging_due_to_time()
                return
                
            # Decrement only if actually charging
            self.remaining = max(0, t - 1)
            self.time_var.set(str(self.remaining))
            print(f"[TICK] Time remaining: {self.remaining}s")
            
            # Update hardware display
            try:
                if getattr(self, 'tm', None) is not None:
                    self.tm.show_time(self.remaining)
            except Exception:
                pass
                
            # Periodic DB update
            self.db_acc += 1
            if self.db_acc >= CHARGE_DB_WRITE_INTERVAL:
                try:
                    write_user(uid, {"charge_balance": self.remaining})
                    self.db_acc = 0
                except Exception as e:
                    print(f"[TICK] DB update error: {e}")

        # Schedule next tick
        try:
            self._tick_job = self.after(1000, self._charging_tick)
        except Exception as e:
            print(f"[TICK] Error scheduling next tick: {e}")
            self._tick_job = None

    def _end_charging_due_to_time(self):
        """Clean up when charging time is exhausted"""
        uid = self._get_session_uid()
        slot = self.charging_slot or self.controller.active_slot
        
        print(f"[SESSION END] Time finished for {uid} in {slot}")
        
        try:
            write_user(uid, {"charging_status": "idle", "charge_balance": 0, "occupied_slot": "none"})
            if slot:
                write_slot(slot, {"status": "inactive", "current_user": "none"})
                if FIREBASE_AVAILABLE and users_ref:
                    users_ref.child(uid).child("slot_status").update({slot: "inactive"})
        except Exception as e:
            print(f"Error updating DB: {e}")
        
        # Hardware cleanup
        try:
            hw = getattr(self.controller, 'hw', None)
            if hw is not None and slot:
                hw.relay_off(slot)
                hw.lock_slot(slot, lock=False)
        except Exception as e:
            print(f"Error with hardware cleanup: {e}")
        
        self.is_charging = False
        self.charging_uid = None
        if self.controller.active_slot == slot:
            self.controller.active_slot = None
            
        self.controller.show_frame(MainScreen)

    def _poll_for_charging_start(self):
        
        """Robust plug detection with filtering for noisy sensors"""
        self._wait_job = None
        
        # Safety checks
        if not getattr(self, '_session_valid', False):
            return
            
        my_session_id = getattr(self, '_current_session_id', None)
        if my_session_id != self._current_session_id:
            return
            
        slot = self.charging_slot or self.controller.active_slot
        hw = getattr(self.controller, 'hw', None)
        uid = self._get_session_uid()
        
        if not slot or not hw or not uid:
            return

        try:
            cur = hw.read_current(slot)
            amps = float(cur.get('amps', 0) or 0)
            
            # MOVING AVERAGE FILTER - reduce noise
            if not hasattr(self, 'plug_readings'):
                self.plug_readings = []
            
            self.plug_readings.append(amps)
            if len(self.plug_readings) > 3:  # Smaller buffer for faster response
                self.plug_readings.pop(0)
            
            filtered_amps = sum(self.plug_readings) / len(self.plug_readings)
            
            print(f"[PLUG DETECT] Raw: {amps:.3f}A, Filtered: {filtered_amps:.3f}A, Threshold: {PLUG_THRESHOLD}A")
            
            # FASTER DETECTION: Use filtered reading
            if filtered_amps >= PLUG_THRESHOLD:
                self._charge_consecutive += 1
                print(f"[PLUG DETECT] Above threshold - consecutive: {self._charge_consecutive}")
            else:
                self._charge_consecutive = 0
                
            # Only require 2 consecutive filtered samples for faster detection
            if self._charge_consecutive >= 2 and not self.is_charging:
                print(f"[PLUG DETECT] Starting charging - detected {self._charge_consecutive} filtered samples")
                self._start_charging_confirmed(uid, slot, filtered_amps)
                return
                
        except Exception as e:
            print(f"[PLUG DETECT] Error reading current: {e}")
            self._charge_consecutive = 0
        
        # 30-second auto-start fallback
        if not hasattr(self, '_plug_start_time'):
            self._plug_start_time = time.time()
        
        elapsed = time.time() - self._plug_start_time
        remaining = 30 - int(elapsed)
        
        if remaining > 0:
            print(f"[PLUG DETECT] Auto-start in {remaining}s if no detection...")
            # Continue polling with normal interval
            try:
                self._wait_job = self.after(1000, self._poll_for_charging_start)
            except Exception:
                self._wait_job = None
        else:
            # 30-second timeout reached - auto-start
            print(f"[PLUG DETECT] Auto-start after 30s timeout")
            self._start_charging_confirmed(uid, slot, PLUG_THRESHOLD)  # Use threshold value
            
    def _start_charging_confirmed(self, uid, slot, amps):
        """Start charging once detection is confirmed"""
        try:
            write_user(uid, {"charging_status": "charging"})
            append_audit_log(actor=uid, action='charging_detected', 
                            meta={'slot': slot, 'amps': amps, 'samples': self._charge_consecutive})
        except Exception as e:
            print(f"Error updating charging status: {e}")
        
        # Start charging state
        self.is_charging = True
        self._charge_consecutive = 0  # Reset for unplug detection
        
        try:
            user = read_user(uid)
            self.remaining = user.get('charge_balance', self.remaining) or self.remaining
            self.slot_lbl.config(text=f"{slot} - CHARGING")
        except Exception as e:
            print(f"Error starting charging UI: {e}")
        
        # Start tick loop and monitoring
        if self._tick_job is None:
            self._charging_tick()
        if self._hw_monitor_job is None:
            self._hw_monitor_job = self.after(500, self._hardware_unplug_monitor)
        
        # Cancel poll timeout
        try:
            if self._poll_timeout_job is not None:
                self.after_cancel(self._poll_timeout_job)
                self._poll_timeout_job = None
        except Exception:
            pass

    def _hardware_unplug_monitor(self):
     
        """Robust unplug detection with filtering for noisy sensors"""
        self._hw_monitor_job = None
        
        # Safety checks
        if not getattr(self, '_session_valid', False):
            return
            
        my_session_id = getattr(self, '_current_session_id', None)
        if my_session_id != self._current_session_id:
            return
            
        slot = self.charging_slot or self.controller.active_slot
        hw = getattr(self.controller, 'hw', None)
        uid = self._get_session_uid()
        
        if not slot or not hw or not uid or not self.is_charging:
            try:
                self._hw_monitor_job = self.after(1000, self._hardware_unplug_monitor)
            except Exception:
                pass
            return

        try:
            cur = hw.read_current(slot)
            amps = float(cur.get('amps', 0) or 0)
            
            # MOVING AVERAGE FILTER - reduce noise
            if not hasattr(self, 'unplug_readings'):
                self.unplug_readings = []
            
            self.unplug_readings.append(amps)
            if len(self.unplug_readings) > 5:  # Keep last 5 readings
                self.unplug_readings.pop(0)
            
            filtered_amps = sum(self.unplug_readings) / len(self.unplug_readings)
            
            # Store charging reference when we first detect proper charging
            if not hasattr(self, 'charging_reference') and filtered_amps >= PLUG_THRESHOLD:
                self.charging_reference = filtered_amps
                print(f"[UNPLUG MON] Set charging reference: {filtered_amps:.3f}A")
            
            print(f"[UNPLUG MON] Raw: {amps:.3f}A, Filtered: {filtered_amps:.3f}A, Plug: {PLUG_THRESHOLD}A, Unplug: {UNPLUG_THRESHOLD}A")
            
            # DUAL DETECTION: Absolute threshold + percentage drop
            current_below_threshold = filtered_amps < UNPLUG_THRESHOLD
            significant_drop = False
            
            if hasattr(self, 'charging_reference') and self.charging_reference > 0:
                current_ratio = filtered_amps / self.charging_reference
                significant_drop = current_ratio < 0.6  # 40% drop = likely unplugged
                print(f"[UNPLUG MON] Current ratio: {current_ratio:.1%}")
            
            # UNPLUG DETECTION: Require BOTH conditions for reliability
            if current_below_threshold and significant_drop:
                # Confirmed unplug - current is low AND dropped significantly
                if not self.unplug_time:
                    self.unplug_time = time.time()
                    print(f"[UNPLUG MON] Confirmed unplug (low + drop), starting {UNPLUG_GRACE_SECONDS}s grace")
                else:
                    idle_time = time.time() - self.unplug_time
                    if idle_time >= UNPLUG_GRACE_SECONDS:
                        print(f"[UNPLUG MON] Grace period expired ({idle_time:.0f}s), stopping session")
                        self.stop_session()
                        # DON'T RETURN HERE - let the method continue to schedule next monitor
                    else:
                        print(f"[UNPLUG MON] Still unplugged - {UNPLUG_GRACE_SECONDS - int(idle_time)}s remaining")
            
            # RE-PLUG DETECTION: Current must be clearly above threshold
            elif filtered_amps > (PLUG_THRESHOLD + 0.02):  # 0.02A buffer above plug threshold
                # Confirmed re-plug - current is clearly above threshold
                if self.unplug_time:
                    print(f"[UNPLUG MON] Confirmed re-plug (above {PLUG_THRESHOLD + 0.02:.3f}A), resuming charging")
                    # Update charging reference
                    self.charging_reference = filtered_amps
                self.unplug_time = None
                
            # GRAY ZONE: Maintain current state, don't trigger false events
            else:
                if self.unplug_time:
                    idle_time = time.time() - self.unplug_time
                    print(f"[UNPLUG MON] Gray zone ({filtered_amps:.3f}A) - maintaining state, {UNPLUG_GRACE_SECONDS - int(idle_time)}s remaining")
                # Don't change unplug_time in gray zone
                    
        except Exception as e:
            print(f"[UNPLUG MON] Error: {e}")
            self.unplug_time = None
            # Reset readings on error
            if hasattr(self, 'unplug_readings'):
                self.unplug_readings = []

        # Continue monitoring - THIS MUST ALWAYS EXECUTE
        try:
            self._hw_monitor_job = self.after(1000, self._hardware_unplug_monitor)
        except Exception:
            self._hw_monitor_job = None

    def _poll_no_detect_timeout(self):
        """Called when no device is detected within the allowed window after unlock."""
        self._poll_timeout_job = None
        # Capture session ID at start; exit silently if stale
        my_session_id = getattr(self, '_current_session_id', None)
        # SAFETY: If session has been invalidated (user changed), exit immediately
        if not getattr(self, '_session_valid', False):
            return
        # Only proceed if this callback belongs to current session
        if my_session_id != self._current_session_id:
            return
        slot = self.charging_slot or self.controller.active_slot
        uid = self._get_session_uid()
        if not uid:
            try:
                s = read_slot(slot)
                uid = s.get('current_user') if s else None
            except Exception:
                uid = None
        try:
            # ensure DB cleanup and notify user
            if uid:
                write_user(uid, {"charging_status": "idle", "occupied_slot": "none"})
        except Exception:
            pass
        if slot:
            try:
                write_slot(slot, {"status": "inactive", "current_user": "none"})
            except Exception:
                pass
        try:
            append_audit_log(actor=uid, action='charge_no_device_detected', meta={'slot': slot})
        except Exception:
            pass
        try:
            print("INFO: No device detected within the allowed time. Session ended.")
        except Exception:
            pass
        # ensure hardware relays off
        try:
            hw = getattr(self.controller, 'hw', None)
            if hw is not None:
                try:
                    hw.relay_off(slot)
                except Exception:
                    pass
                try:
                    hw.lock_slot(slot, lock=False)
                except Exception:
                    pass
        except Exception:
            pass
        # clear any accumulated consecutive-sample counter
        try:
            self._charge_consecutive = 0
        except Exception:
            pass
        # clear UI state (only if it refers to this session's slot)
        try:
            if self.controller.active_slot == slot:
                self.controller.active_slot = None
        except Exception:
            try:
                self.controller.active_slot = None
            except Exception:
                pass
        self.controller.show_frame(MainScreen)

    def unlock_slot(self):
        uid = self._get_session_uid()
        slot = self.charging_slot or self.controller.active_slot
        if not uid or not slot:
            print("WARN: No slot assigned.")
            return
            
        # ADD SAFETY CHECK:
        if FIREBASE_AVAILABLE and users_ref:
            try:
                users_ref.child(uid).child("slot_status").update({slot: "inactive"})
                print(f"INFO: {slot} unlocked. Please unplug your device when ready.")
            except Exception as e:
                print(f"ERROR unlocking slot: {e}")
        else:
            print("INFO: Offline mode - slot unlocked locally")
            
        # update header info but do not stop charging; unlocking does not equal unplug
        try:
            self.user_info.refresh()
        except Exception:
            pass

    def stop_session(self):
        uid = self._get_session_uid()
        slot = self.charging_slot or self.controller.active_slot
        if uid:
            # cancel periodic tick if running
            if self._tick_job is not None:
                try:
                    self.after_cancel(self._tick_job)
                except Exception:
                    pass
                self._tick_job = None
            write_user(uid, {"charging_status": "idle"})
            write_user(uid, {"occupied_slot": "none"})
            if slot:
                write_slot(slot, {"status": "inactive", "current_user": "none"})
                if FIREBASE_AVAILABLE and users_ref:
                    users_ref.child(uid).child("slot_status").update({slot: "inactive"})
                # clear assigned slot
                self.controller.active_slot = None
            # turn off hardware relays and cancel monitors if hardware is present
            try:
                hw = getattr(self.controller, 'hw', None)
                if hw is not None:
                    try:
                        hw.relay_off(slot)
                    except Exception:
                        pass
                    try:
                        hw.lock_slot(slot, lock=False)
                    except Exception:
                        pass
            except Exception:
                pass
            # cancel hardware monitor jobs
            try:
                if self._wait_job is not None:
                    try:
                        self.after_cancel(self._wait_job)
                    except Exception:
                        pass
                    self._wait_job = None
                if self._hw_monitor_job is not None:
                    try:
                        self.after_cancel(self._hw_monitor_job)
                    except Exception:
                        pass
                    self._hw_monitor_job = None
                self.tm = None
                self.unplug_time = None
                # clear consecutive-sample counter when stopping
                try:
                    self._charge_consecutive = 0
                except Exception:
                    pass
                # cancel poll timeout job if any
                try:
                    if self._poll_timeout_job is not None:
                        try:
                            self.after_cancel(self._poll_timeout_job)
                        except Exception:
                            pass
                        self._poll_timeout_job = None
                except Exception:
                    pass
            except Exception:
                pass
            try:
                append_audit_log(actor=uid, action='stop_charging', meta={'slot': slot})
            except Exception:
                pass
        # clear stored session uid so future ticks don't reference this session
        try:
            self.charging_uid = None
        except Exception:
            pass
        # also clear the bound charging slot so this screen is ready for the next session
        try:
            self.charging_slot = None
        except Exception:
            pass
        # CRITICAL: Mark session as invalid to prevent any queued callbacks from executing
        try:
            self._session_valid = False
        except Exception:
            pass
        print("INFO: Charging session stopped.")
        self.controller.show_frame(MainScreen)

# --------- Screen: Water ----------
class WaterScreen(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#2980b9")
        self.controller = controller
        self.user_info = UserInfoFrame(self, controller)
        self.user_info.pack(fill="x")

        body = tk.Frame(self, bg="#2980b9")
        body.pack(expand=True, pady=12)
        
        tk.Label(body, text="Water Dispensing", font=("Arial", 22, "bold"), 
                 fg="white", bg="#2980b9").pack(pady=6)
        
        self.status_lbl = tk.Label(body, text="Place cup to start", font=("Arial", 16), 
                                  fg="white", bg="#2980b9")
        self.status_lbl.pack(pady=6)
        
        self.time_var = tk.StringVar(value="0")
        tk.Label(body, text="Water Balance (mL)", font=("Arial", 14), 
                fg="white", bg="#2980b9").pack()
        tk.Label(body, textvariable=self.time_var, font=("Arial", 28, "bold"), 
                fg="white", bg="#2980b9").pack(pady=6)

        # Debug info
        self.debug_var = tk.StringVar(value="Status: Ready")
        tk.Label(body, textvariable=self.debug_var, font=("Arial", 10), 
                fg="yellow", bg="#2980b9").pack(pady=4)

        # Control buttons - ALWAYS VISIBLE for testing
        btn_frame = tk.Frame(body, bg="#2980b9")
        btn_frame.pack(pady=8)
        
        # SIMULATION BUTTONS - Always show these for testing
        sim_frame = tk.LabelFrame(body, text="Manual Controls (Testing)", 
                                 font=("Arial", 12, "bold"), fg="white", bg="#2980b9")
        sim_frame.pack(pady=10)
        
        # Coin buttons
        coin_subframe = tk.Frame(sim_frame, bg="#2980b9")
        coin_subframe.pack(pady=5)
        tk.Button(coin_subframe, text="₱1 - Add 50mL", font=("Arial", 10), 
                  bg="#f39c12", fg="white", width=12,
                  command=lambda: self.insert_coin_water(1)).pack(side="left", padx=5)
        tk.Button(coin_subframe, text="₱5 - Add 250mL", font=("Arial", 10), 
                  bg="#e67e22", fg="white", width=12,
                  command=lambda: self.insert_coin_water(5)).pack(side="left", padx=5)
        tk.Button(coin_subframe, text="₱10 - Add 500mL", font=("Arial", 10), 
                  bg="#d35400", fg="white", width=12,
                  command=lambda: self.insert_coin_water(10)).pack(side="left", padx=5)
        
        # Cup simulation buttons
        cup_subframe = tk.Frame(sim_frame, bg="#2980b9")
        cup_subframe.pack(pady=5)
        tk.Button(cup_subframe, text="PLACE CUP", font=("Arial", 12, "bold"),
                  bg="#27ae60", fg="white", width=15,
                  command=self.place_cup).pack(side="left", padx=5)
        tk.Button(cup_subframe, text="REMOVE CUP", font=("Arial", 12, "bold"),
                  bg="#f39c12", fg="white", width=15,
                  command=self.remove_cup).pack(side="left", padx=5)
        
        # Session control
        ctrl_subframe = tk.Frame(sim_frame, bg="#2980b9")
        ctrl_subframe.pack(pady=5)
        tk.Button(ctrl_subframe, text="START DISPENSING", font=("Arial", 12, "bold"),
                  bg="#2980b9", fg="white", width=18,
                  command=self.start_dispensing).pack(side="left", padx=5)
        tk.Button(ctrl_subframe, text="STOP SESSION", font=("Arial", 12, "bold"),
                  bg="#c0392b", fg="white", width=15,
                  command=self.stop_session).pack(side="left", padx=5)

        # Navigation
        nav_frame = tk.Frame(body, bg="#2980b9")
        nav_frame.pack(pady=10)
        tk.Button(nav_frame, text="← Back to Main", font=("Arial", 12),
                  bg="#95a5a6", fg="white", width=15,
                  command=lambda: controller.show_frame(MainScreen)).pack()

        # State variables
        self.cup_present = False
        self.last_cup_time = None
        self.temp_water_time = 0
        self._water_job = None
        self._water_nocup_job = None
        self._water_db_acc = 0
        self._water_remaining = 0
        self.is_dispensing = False
        
        # Test Arduino connection
        self.test_arduino_connection()

    def handle_arduino_event(self, event, value):
        """Handle Arduino events in WaterScreen with immediate UI updates."""
        print(f"WaterScreen received: {event} = {value}")
        
        try:
            if event == 'coin' and value is not None:
                # Convert coin to mL and update balance IMMEDIATELY
                coin_to_ml = {1: 50, 5: 250, 10: 500}
                added_ml = coin_to_ml.get(value, 0)
                
                if added_ml > 0:
                    uid = self.controller.active_uid
                    if uid:
                        user = read_user(uid)
                        new_balance = 0
                        
                        if user and user.get("type") == "member":
                            current = user.get("water_balance", 0) or 0
                            new_balance = current + added_ml
                            write_user(uid, {"water_balance": new_balance})
                            print(f"Updated member water balance: {current} + {added_ml} = {new_balance}mL")
                        else:
                            current = user.get("temp_water_time", 0) or 0
                            new_balance = current + added_ml
                            write_user(uid, {"temp_water_time": new_balance})
                            self.temp_water_time = new_balance
                            print(f"Updated guest water balance: {current} + {added_ml} = {new_balance}mL")
                        
                        # CRITICAL: Update UI IMMEDIATELY
                        self.time_var.set(str(new_balance))
                        if new_balance > 0:
                            self.status_lbl.config(text=f"Balance: {new_balance}mL - Place cup to start")
                        else:
                            self.status_lbl.config(text="Insert coins to buy water")
                        
                        # Force UI refresh
                        self.update_idletasks()
                        
                        # Refresh user info in top bar
                        self.controller.refresh_all_user_info()
                        
                        # Then show popup
                        self.controller.show_coin_popup(uid, peso=value, added_ml=added_ml, total_ml=new_balance)
                        
            elif event == 'cup_detected':
                self.cup_present = True
                self.last_cup_time = time.time()
                self.status_lbl.config(text="Cup detected - Ready to dispense")
                self.debug_var.set("Cup placed automatically")
                
                # Cancel any previous timeouts
                if self._water_nocup_job:
                    self.after_cancel(self._water_nocup_job)
                    self._water_nocup_job = None
                    
            elif event == 'cup_removed':
                self.cup_present = False
                self.is_dispensing = False
                self.status_lbl.config(text="Cup removed")
                self.debug_var.set("Cup removed - Session paused")
                
                # Stop dispensing if active
                if self._water_job:
                    self.after_cancel(self._water_job)
                    self._water_job = None
                    
                # Start timeout counter
                self.last_cup_time = time.time()
                if self._water_nocup_job is None:
                    self._water_nocup_job = self.after(1000, self._check_cup_timeout)
                    
            elif event == 'dispense_start':
                self.is_dispensing = True
                self.status_lbl.config(text="DISPENSING WATER...")
                self.debug_var.set("Dispensing started automatically")
                
            elif event == 'dispense_done':
                dispensed_ml = value
                self._end_dispensing(f"Dispensing completed: {dispensed_ml}mL")
                
            elif event == 'credit_left':
                remaining_ml = value
                self.debug_var.set(f"Credit left: {remaining_ml}mL")
                
                # Update display with remaining credit
                uid = self.controller.active_uid
                if uid:
                    user = read_user(uid)
                    if user and user.get("type") == "member":
                        write_user(uid, {"water_balance": remaining_ml})
                    else:
                        write_user(uid, {"temp_water_time": remaining_ml})
                        self.temp_water_time = remaining_ml
                    
                    # Update UI immediately
                    self.time_var.set(str(remaining_ml))
                    if remaining_ml > 0:
                        self.status_lbl.config(text=f"Balance: {remaining_ml}mL - Place cup to continue")
                    else:
                        self.status_lbl.config(text="Dispensing completed")
                    self.update_idletasks()
                    self.controller.refresh_all_user_info()
                    
            elif event == 'dispense_progress':
                # Handle progress updates during dispensing
                if isinstance(value, dict):
                    dispensed_ml = value.get('dispensed', 0)
                    remaining_ml = value.get('remaining', 0)
                    
                    # Update display with progress
                    self.time_var.set(str(remaining_ml))
                    self.status_lbl.config(text=f"Dispensing... {remaining_ml}mL left")
                    self.debug_var.set(f"Progress: {dispensed_ml:.1f}mL dispensed")
                    self.update_idletasks()
                    
            elif event == 'system_ready':
                self.debug_var.set("Arduino system ready")
                self.status_lbl.config(text="System ready - Insert coins")
                
            elif event == 'calibration_done':
                self.debug_var.set("Calibration completed")
                
            elif event == 'coin_water':
                # Direct coin water event from Arduino
                added_ml = value
                uid = self.controller.active_uid
                if uid and added_ml > 0:
                    user = read_user(uid)
                    new_balance = 0
                    
                    if user and user.get("type") == "member":
                        current = user.get("water_balance", 0) or 0
                        new_balance = current + added_ml
                        write_user(uid, {"water_balance": new_balance})
                    else:
                        current = user.get("temp_water_time", 0) or 0
                        new_balance = current + added_ml
                        write_user(uid, {"temp_water_time": new_balance})
                        self.temp_water_time = new_balance
                    
                    # Update UI
                    self.time_var.set(str(new_balance))
                    self.status_lbl.config(text=f"Balance: {new_balance}mL - Place cup to start")
                    self.update_idletasks()
                    self.controller.refresh_all_user_info()
                    
            elif event == 'coin_inserted':
                # Coin inserted event (before credit)
                peso = value
                self.debug_var.set(f"Coin detected: ₱{peso}")
                
            # Handle debug messages from Arduino
            elif 'debug' in event.lower() or '[debug]' in str(value).lower():
                # Extract the debug message for display
                debug_msg = str(value)
                if 'ultrasonic' in debug_msg.lower() or 'distance' in debug_msg.lower():
                    self.debug_var.set(f"Sensor: {debug_msg[-20:]}")
                elif 'cup detected' in debug_msg.lower():
                    self.debug_var.set(f"Cup: {debug_msg[-15:]}")
                    
        except Exception as e:
            print(f"ERROR in WaterScreen event handler: {e}")
            self.debug_var.set(f"Event error: {e}")
            
    def test_arduino_connection(self):
        """Test if Arduino is connected and working"""
        try:
            al = getattr(self.controller, 'arduino_listener', None)
            if al and hasattr(al, 'is_connected'):
                status = "Connected" if al.is_connected() else "Disconnected"
                self.debug_var.set(f"Arduino: {status}")
            else:
                self.debug_var.set("Arduino: Not available - Using simulation")
        except Exception as e:
            self.debug_var.set(f"Arduino: Error - {str(e)}")

    def refresh(self):
        """Update WaterScreen display with current balance."""
        try:
            self.user_info.refresh()
            self.test_arduino_connection()
            
            uid = self.controller.active_uid
            if not uid:
                self.time_var.set("0")
                self.status_lbl.config(text="Please scan RFID first")
                return
                
            user = read_user(uid)
            if user.get("type") == "member":
                wb = user.get("water_balance", 0) or 0
                self.time_var.set(str(wb))
                status_text = f"Balance: {wb}mL - Place cup to start" if wb > 0 else "No water balance"
                self.status_lbl.config(text=status_text)
            else:
                temp = user.get("temp_water_time", 0) or 0
                self.temp_water_time = temp
                self.time_var.set(str(temp))
                if temp <= 0:
                    self.status_lbl.config(text="Insert coins to buy water")
                else:
                    self.status_lbl.config(text=f"Balance: {temp}mL - Place cup to start")
                    
            # Force immediate UI update
            self.update_idletasks()
            
        except Exception as e:
            print(f"Error in WaterScreen.refresh(): {e}")
            self.debug_var.set(f"Refresh error: {e}")

    def insert_coin_water(self, amount):
        """Add water credit when coins are inserted"""
        uid = self.controller.active_uid
        if not uid:
            self.debug_var.set("ERROR: No user - Scan RFID first")
            return
            
        add_ml = WATER_COIN_MAP.get(amount, 0)
        user = read_user(uid)
        
        if user.get("type") == "member":
            current = user.get("water_balance", 0) or 0
            new_balance = current + add_ml
            write_user(uid, {"water_balance": new_balance})
            self.debug_var.set(f"Added {add_ml}mL - Total: {new_balance}mL")
        else:
            current = user.get("temp_water_time", 0) or 0
            new_balance = current + add_ml
            write_user(uid, {"temp_water_time": new_balance})
            self.temp_water_time = new_balance
            self.debug_var.set(f"Purchased {add_ml}mL - Total: {new_balance}mL")
            
        # Show popup
        self.controller.show_coin_popup(uid, peso=amount, added_ml=add_ml, total_ml=new_balance)
        self.refresh()

    def place_cup(self):
        """Simulate placing a cup - start water dispensing"""
        uid = self.controller.active_uid
        if not uid:
            self.debug_var.set("ERROR: No user - Scan RFID first")
            return
            
        user = read_user(uid)
        if user.get("type") == "member":
            balance = user.get("water_balance", 0) or 0
            if balance <= 0:
                self.debug_var.set("ERROR: No water balance - Add coins first")
                return
        else:
            if self.temp_water_time <= 0:
                self.debug_var.set("ERROR: No purchased water - Add coins first")
                return
                
        self.cup_present = True
        self.last_cup_time = time.time()
        self.status_lbl.config(text="Cup detected - Ready to dispense")
        self.debug_var.set("Cup placed - Click START DISPENSING")
        
        # Cancel any previous timeouts
        if self._water_nocup_job:
            self.after_cancel(self._water_nocup_job)
            self._water_nocup_job = None

    def start_dispensing(self):
        """Start the water dispensing process"""
        if not self.cup_present:
            self.debug_var.set("ERROR: Place cup first")
            return
            
        uid = self.controller.active_uid
        if not uid:
            return
            
        user = read_user(uid)
        if user.get("type") == "member":
            self._water_remaining = user.get("water_balance", 0) or 0
        else:
            self._water_remaining = self.temp_water_time
            
        if self._water_remaining <= 0:
            self.debug_var.set("ERROR: No water credit")
            return
            
        self.is_dispensing = True
        self.status_lbl.config(text="DISPENSING WATER...")
        self.debug_var.set(f"Dispensing started - {self._water_remaining}mL remaining")
        
        # Start the dispensing timer
        if self._water_job is None:
            self._water_job = self.after(1000, self._dispense_tick)

    def _dispense_tick(self):
        """Timer tick for water dispensing"""
        if not self.cup_present or not self.is_dispensing:
            self._water_job = None
            self.status_lbl.config(text="Dispensing paused")
            return
            
        if self._water_remaining <= 0:
            self._end_dispensing("Water finished")
            return
            
        # Dispense 100mL per second (adjust as needed)
        dispense_amount = min(100, self._water_remaining)
        self._water_remaining -= dispense_amount
        self.time_var.set(str(self._water_remaining))
        
        # Update DB periodically
        uid = self.controller.active_uid
        if uid and self._water_remaining % 500 == 0:  # Update every 500mL
            user = read_user(uid)
            if user.get("type") == "member":
                write_user(uid, {"water_balance": self._water_remaining})
            else:
                write_user(uid, {"temp_water_time": self._water_remaining})
                self.temp_water_time = self._water_remaining
        
        self.status_lbl.config(text=f"Dispensing... {self._water_remaining}mL left")
        
        # Continue dispensing
        self._water_job = self.after(1000, self._dispense_tick)

    def _end_dispensing(self, message):
        """End the dispensing session"""
        self.is_dispensing = False
        self.cup_present = False
        self.status_lbl.config(text=message)
        self.debug_var.set("Dispensing complete")
        
        uid = self.controller.active_uid
        if uid:
            user = read_user(uid)
            # Only set balance to zero if this is a completion, not a pause
            if "completed" in message.lower() or "finished" in message.lower():
                if user and user.get("type") == "member":
                    write_user(uid, {"water_balance": 0})
                else:
                    write_user(uid, {"temp_water_time": 0})
                    self.temp_water_time = 0
                    
        if self._water_job:
            self.after_cancel(self._water_job)
            self._water_job = None
            
        if self._water_nocup_job:
            self.after_cancel(self._water_nocup_job)
            self._water_nocup_job = None
            
        # Auto-return to main after 3 seconds only if dispensing completed
        if "completed" in message.lower():
            self.after(3000, lambda: self.controller.show_frame(MainScreen))
            
    def remove_cup(self):
        """Simulate removing the cup"""
        self.cup_present = False
        self.is_dispensing = False
        self.status_lbl.config(text="Cup removed")
        self.debug_var.set("Cup removed - Session paused")
        
        # Stop dispensing
        if self._water_job:
            self.after_cancel(self._water_job)
            self._water_job = None
            
        # Start timeout counter
        self.last_cup_time = time.time()
        if self._water_nocup_job is None:
            self._water_nocup_job = self.after(1000, self._check_cup_timeout)

    def _check_cup_timeout(self):
        """Check if cup has been removed for too long"""
        if self.cup_present:
            self._water_nocup_job = None
            return
            
        elapsed = time.time() - (self.last_cup_time or time.time())
        if elapsed >= NO_CUP_TIMEOUT:
            self.debug_var.set("Session ended - No cup detected")
            self.stop_session()
        else:
            time_left = NO_CUP_TIMEOUT - int(elapsed)
            self.status_lbl.config(text=f"Cup removed - Auto-end in {time_left}s")
            self._water_nocup_job = self.after(1000, self._check_cup_timeout)

    def stop_session(self):
        """Manually stop the water session"""
        self.is_dispensing = False
        self.cup_present = False
        
        # Cancel all jobs
        if self._water_job:
            self.after_cancel(self._water_job)
            self._water_job = None
        if self._water_nocup_job:
            self.after_cancel(self._water_nocup_job)
            self._water_nocup_job = None
            
        self.debug_var.set("Session stopped manually")
        self.controller.show_frame(MainScreen)

# ----------------- Run App -----------------
if __name__ == "__main__":
    app = KioskApp()
    app.protocol("WM_DELETE_WINDOW", lambda: (app.cleanup(), app.destroy()))
    app.mainloop()