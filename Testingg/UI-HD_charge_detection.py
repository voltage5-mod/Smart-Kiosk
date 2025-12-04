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
    """Convert water balance in mL to liters for display."""
    if sec is None:
        return "N/A"
    try:
        # Convert to float first, handle both int and string
        if isinstance(sec, str):
            ml = float(sec) if sec.strip() else 0.0
        else:
            ml = float(sec or 0)
        liters = ml / 1000.0
        return f"{liters:.2f} L"
    except (ValueError, TypeError) as e:
        print(f"WARN: Could not convert water balance '{sec}' to liters: {e}")
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


# KioskApp class in UI-HD_charge_detection.py
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

        # ========== HARDWARE CONTROLLER INITIALIZATION ==========
        print("INFO: Initializing HardwareGPIO controller...")
        self.hw = None
        try:
            if _pinmap:
                print(f"INFO: Using pinmap from {PINMAP_PATH}")
                # Initialize with active-high relays (adjust based on your wiring)
                self.hw = HardwareGPIO(pinmap=_pinmap, mode='auto', relay_active_high=False)
                try:
                    self.hw.setup()
                    print("SUCCESS: HardwareGPIO initialized in AUTO mode")
                    
                    # Test hardware initialization
                    print("INFO: Testing hardware communication...")
                    try:
                        # Try to read from ADC channel 0
                        test_adc = self.hw.read_adc(0)
                        print(f"INFO: Hardware test - ADC channel 0: {test_adc}")
                    except Exception as e:
                        print(f"WARN: ADC test failed: {e}")
                        
                except Exception as e:
                    print(f"ERROR: HardwareGPIO setup failed: {e}")
                    print("FALLBACK: Using simulation mode")
                    self.hw = HardwareGPIO(pinmap=None, mode='sim')
                    self.hw.setup()
            else:
                print("WARN: No pinmap found, using simulation mode")
                self.hw = HardwareGPIO(pinmap=None, mode='sim')
                self.hw.setup()
        except Exception as e:
            print(f"CRITICAL ERROR: Hardware initialization failed: {e}")
            import traceback
            traceback.print_exc()
            print("FALLBACK: Creating simulation hardware")
            self.hw = HardwareGPIO(pinmap=None, mode='sim')
            try:
                self.hw.setup()
            except Exception:
                pass
        
        print(f"INFO: Hardware controller status: {'Available' if self.hw else 'Not available'}")
        
        # Test hardware for debugging
        if self.hw:
            try:
                print("DEBUG: Testing hardware relay control...")
                # Quick test of relay control (simulation only)
                if self.hw.mode == 'sim':
                    self.hw.relay_on('slot1')
                    time.sleep(0.1)
                    self.hw.relay_off('slot1')
                    print("DEBUG: Hardware simulation test passed")
            except Exception as e:
                print(f"DEBUG: Hardware test error: {e}")

        # ========== FIXED ARDUINO LISTENER INITIALIZATION ==========
        self.arduino_listener = None
        self.arduino_available = False

        try:
            from ArduinoListener import ArduinoListener
            
            # Set specific port for MAIN Arduino (water/coin)
            main_arduino_ports = ["/dev/ttyACM0"]  # Force this port
            
            self.arduino_listener = ArduinoListener(
                event_callback=self._arduino_event_callback,
                port_candidates=main_arduino_ports  # Force ACM0
            )
            print("INFO: ArduinoListener initialized for ACM0 (water/coin)")
            
            if hasattr(self.arduino_listener, 'start'):
                try:
                    self.arduino_listener.start()
                    self.arduino_available = True
                    print("INFO: ArduinoListener started successfully on ACM0")
                except Exception as start_error:
                    print(f"WARN: ArduinoListener start() failed: {start_error}")
                    self.arduino_available = False
        except Exception as e:
            print(f"ERROR: ArduinoListener initialization failed: {e}")
            self.arduino_listener = None

        # ========== TIMER DISPLAY ARDUINO INITIALIZATION ==========
        self.timer_serial = None
        self.timer_available = False
        self.setup_timer_displays()

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
        # Clear any existing messages
        self.info.config(text="")
        
        # Test Arduino connection if needed
        self.test_arduino_connection()
        
        # Clear the entry field when returning to scan screen
        self.uid_entry.delete(0, tk.END)
        
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
        # For members, show water_balance; for non-members, show any temporary purchased water
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
        # refresh user info and slot statuses
        self.user_info.refresh()
        
        # Ensure Arduino is in CHARGING mode for slot selection
        try:
            if hasattr(self.controller, 'arduino_available') and self.controller.arduino_available:
                self.controller.send_arduino_command('CHARGING')
        except Exception as e:
            print(f"WARN: Could not set Arduino mode: {e}")
        
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
        
        # Update slot buttons with current status
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
        
        # show coin status for current user if any (charging balance)
        try:
            uid = self.controller.active_uid
            if uid:
                user = read_user(uid)
                if user:
                    charge_balance = user.get("charge_balance", 0) or 0
                    if charge_balance > 0:
                        mins = charge_balance // 60
                        secs = charge_balance % 60
                        self.coin_status_lbl.config(text=f"Charging balance: {mins}m {secs}s")
                    else:
                        self.coin_status_lbl.config(text="Insert coins to add charging time")
                else:
                    self.coin_status_lbl.config(text="")
            else:
                self.coin_status_lbl.config(text="")
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
        
        # Simple balance display (no coin frame)
        self.balance_frame = tk.Frame(self, bg="#34495e")
        self.balance_frame.pack(pady=8)

        self.balance_lbl = tk.Label(self.balance_frame, text="", fg="#f39c12", bg="#34495e", 
                                   font=("Arial", 14, "bold"))
        self.balance_lbl.pack()

        self.slot_buttons = {}
        grid = tk.Frame(self, bg="#34495e")
        grid.pack(pady=8)
        
        # Create 4 slot buttons in a 2x2 grid
        for i in range(1, 5):
            btn = tk.Button(grid, text=f"Slot {i}\n(Checking...)", font=("Arial", 14, "bold"),
                            bg="#95a5a6", fg="black", width=14, height=2,
                            command=lambda s=i: self.select_slot(s))
            row = (i-1) // 2  # 2 buttons per row
            col = (i-1) % 2   # 2 columns
            btn.grid(row=row, column=col, padx=10, pady=8)
            self.slot_buttons[f"slot{i}"] = btn
        
        # Back button
        tk.Button(self, text="Back", font=("Arial", 14, "bold"), bg="#c0392b", fg="white",
                  command=lambda: controller.show_frame(MainScreen)).pack(pady=6, anchor='nw', padx=8)

    def _update_timer_display(self):
        """Update all 4 timer displays based on current slot status"""
        if not self.controller.timer_available:
            return
            
        for i in range(1, 5):
            slot_key = f"slot{i}"
            slot = read_slot(slot_key)
            
            if not slot:
                # Slot doesn't exist in DB, show as available
                self.controller.send_timer_command(f"SLOT{i}:-")
                continue
                
            status = slot.get("status", "inactive")
            current_user = slot.get("current_user", "none")
            
            if status == "active" and current_user != "none":
                # Slot is actively charging - show remaining time
                user = read_user(current_user)
                if user:
                    remaining = user.get("charge_balance", 0)
                    if remaining > 0:
                        self.controller.send_timer_command(f"SLOT{i}:{remaining}")
                    else:
                        self.controller.send_timer_command(f"SLOT{i}:-")
                else:
                    self.controller.send_timer_command(f"SLOT{i}:-")
            elif current_user != "none":
                # Slot assigned but not active (waiting for plug)
                self.controller.send_timer_command(f"SLOT{i}:-")
            else:
                # Slot is free/available
                self.controller.send_timer_command(f"SLOT{i}:-")

    def refresh(self):
        """Refresh the slot selection screen with current status"""
        # refresh user info
        self.user_info.refresh()
        
        # Update timer displays
        self._update_timer_display()
        
        # Update each slot button based on current status
        for i in range(1, 5):
            key = f"slot{i}"
            slot = read_slot(key)
            
            # Default values
            text = f"Slot {i}\nFree"
            color = "#2ecc71"  # green for free
            enabled = True
            
            try:
                if slot is None:
                    # Slot doesn't exist in DB, show as available but disabled
                    text = f"Slot {i}\nError"
                    color = "#95a5a6"
                    enabled = False
                else:
                    status = slot.get("status", "inactive")
                    cur = slot.get("current_user", "none")
                    uid = self.controller.active_uid
                    
                    if cur != "none":
                        # Slot assigned to someone
                        if cur == uid:
                            # Assigned to current user
                            if status == "active":
                                text = f"Slot {i}\nCharging"
                                color = "#3498db"  # blue for charging
                            else:
                                text = f"Slot {i}\nAssigned"
                                color = "#f39c12"  # orange for assigned
                            enabled = True  # User can select their own assigned slot
                        else:
                            # Assigned to another user
                            if status == "active":
                                text = f"Slot {i}\nOccupied"
                                color = "#e74c3c"  # red for occupied
                            else:
                                text = f"Slot {i}\nIn Use"
                                color = "#e67e22"  # darker orange
                            enabled = False  # Cannot select other user's slot
                    else:
                        # No current_user assigned
                        if status == "active":
                            text = f"Slot {i}\nIn Use"
                            color = "#e74c3c"
                            enabled = False
                        else:
                            text = f"Slot {i}\nFree"
                            color = "#2ecc71"
                            enabled = True
            except Exception as e:
                print(f"Error reading slot {key}: {e}")
                text = f"Slot {i}\nError"
                color = "#95a5a6"
                enabled = False
            
            # Update button appearance
            try:
                btn = self.slot_buttons[key]
                btn.config(text=text, bg=color, 
                          state="normal" if enabled else "disabled")
            except Exception as e:
                print(f"Error updating slot button {key}: {e}")
        
        # Update balance information
        try:
            uid = self.controller.active_uid
            if uid:
                user = read_user(uid)
                if user:
                    # Show charging balance
                    cb = user.get("charge_balance", 0) or 0
                    minutes = cb // 60
                    seconds = cb % 60
                    
                    if minutes > 0:
                        balance_text = f"Charging Balance: {minutes}m {seconds}s"
                    else:
                        balance_text = f"Charging Balance: {seconds}s"
                    
                    self.balance_lbl.config(text=balance_text)
                else:
                    self.balance_lbl.config(text="")
            else:
                self.balance_lbl.config(text="")
        except Exception as e:
            print(f"Error updating balance display: {e}")
            self.balance_lbl.config(text="")

    def select_slot(self, i):
        """Handle slot selection"""
        uid = self.controller.active_uid
        if not uid:
            print("WARN: No user; scan first before selecting a slot.")
            return
        
        # Check user has positive charge balance
        user = read_user(uid)
        cb = user.get("charge_balance", 0) if user else 0
        if (cb or 0) <= 0:
            print("WARN: No charge balance; insert coins before selecting a slot.")
            # Show error message to user
            self.balance_lbl.config(text="ERROR: Add coins first!", fg="#e74c3c")
            self.after(2000, lambda: self.refresh())  # Reset after 2 seconds
            return
        
        slot_key = f"slot{i}"
        slot = read_slot(slot_key)
        
        # Validate slot is available
        if slot is not None:
            cur = slot.get("current_user", "none")
            status = slot.get("status", "inactive")
            
            if cur != "none" and cur != uid:
                print(f"WARN: {slot_key} is already assigned to another user.")
                # Update button to show it's occupied
                self.slot_buttons[slot_key].config(text=f"Slot {i}\nOccupied", bg="#e74c3c")
                return
            
            if status == "active" and cur != uid:
                print(f"WARN: {slot_key} is currently in use. Please choose another slot.")
                self.slot_buttons[slot_key].config(text=f"Slot {i}\nIn Use", bg="#e74c3c")
                return
        
        # Update timer display to show slot is assigned (but not yet charging)
        if self.controller.timer_available:
            # Show remaining time on the physical display
            self.controller.send_timer_command(f"SLOT{i}:{cb}")
        
        # Assign slot to user
        try:
            write_user(uid, {"occupied_slot": slot_key})
            
            if FIREBASE_AVAILABLE and users_ref:
                users_ref.child(uid).child("slot_status").update({slot_key: "inactive"})
            
            write_slot(slot_key, {"status": "inactive", "current_user": uid})
            
            try:
                append_audit_log(actor=uid, action='assign_slot', meta={'slot': slot_key})
            except Exception:
                pass
            
            self.controller.active_slot = slot_key
            
            # Update button to show it's assigned
            self.slot_buttons[slot_key].config(text=f"Slot {i}\nAssigned", bg="#f39c12")
            
            print(f"INFO: You selected {slot_key}. Please plug your device and press Start Charging.")
            
            # Brief delay before switching to charging screen (shows confirmation)
            self.after(1000, lambda: self.controller.show_frame(ChargingScreen))
            
        except Exception as e:
            print(f"ERROR assigning slot: {e}")
            # Reset button if assignment failed
            self.slot_buttons[slot_key].config(text=f"Slot {i}\nError", bg="#95a5a6")
            
            # Show error message
            self.balance_lbl.config(text="ERROR: Could not assign slot", fg="#e74c3c")
            self.after(2000, lambda: self.refresh())  # Reset after 2 seconds

    def _on_close(self):
        """Clean up when screen is closed"""
        # Ensure all timer displays show correct status
        self._update_timer_display()


# --------- Screen: Charging ----------
# ChargingScreen class in UI-HD_charge_detection.py
class ChargingScreen(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#34495e")
        self.controller = controller
        
        # ========== HARDWARE CONTROLLER - CRITICAL FIX ==========
        # Get hardware from controller, don't try to create new
        self.hw = None
        try:
            if hasattr(controller, 'hw'):
                self.hw = controller.hw
                print(f"CHARGING_SCREEN: Hardware controller {'available' if self.hw else 'not available'}")
                
                # Test if hardware is properly initialized
                if self.hw:
                    print(f"CHARGING_SCREEN: Hardware mode: {self.hw.mode}")
            else:
                print("CHARGING_SCREEN: ERROR - Controller has no hardware attribute")
        except Exception as e:
            print(f"CHARGING_SCREEN: ERROR getting hardware: {e}")
        
        # user info visible while charging
        self.user_info = UserInfoFrame(self, controller)
        self.user_info.pack(fill="x")

        body = tk.Frame(self, bg="#34495e")
        body.pack(expand=True, fill='both', pady=12)

        # Large centered header that shows the active charging slot
        self.slot_lbl = tk.Label(body, text="Charging Slot -", font=("Arial", 28, "bold"), fg="white", bg="#34495e")
        self.slot_lbl.pack(pady=(20, 12))

        # Time display
        self.time_var = tk.StringVar(value="0")
        tk.Label(body, text="Time Left", font=("Arial", 14), fg="white", bg="#34495e").pack(pady=(6, 2))
        tk.Label(body, textvariable=self.time_var, font=("Arial", 28, "bold"), fg="white", bg="#34495e").pack(pady=(0, 12))
        
        # Progress bar
        self.progress_bar = tk.Canvas(body, width=300, height=20, bg="#2c3e50", highlightthickness=0)
        self.progress_bar.pack(pady=10)
        self.progress_rect = self.progress_bar.create_rectangle(0, 0, 0, 20, fill="#27ae60", outline="")

        # Status display
        self.status_lbl = tk.Label(body, text="Ready to start charging", font=("Arial", 12), 
                                   fg="white", bg="#34495e")
        self.status_lbl.pack(pady=5)
        
        # Current reading display
        self.current_lbl = tk.Label(body, text="Current: 0.00A", font=("Arial", 10), 
                                    fg="#95a5a6", bg="#34495e")
        self.current_lbl.pack(pady=2)

        # Button frame
        btn_frame = tk.Frame(body, bg="#34495e")
        btn_frame.pack(pady=8)
        
        # Back button
        tk.Button(btn_frame, text="← Back", font=("Arial", 12, "bold"),
                  bg="#95a5a6", fg="white", width=10, 
                  command=lambda: controller.show_frame(MainScreen)).grid(row=0, column=0, padx=6)
        
        # Start Charging button
        self.start_btn = tk.Button(btn_frame, text="Start Charging", font=("Arial", 14, "bold"),
                  bg="#27ae60", fg="white", width=14, command=self.start_charging)
        self.start_btn.grid(row=0, column=1, padx=6)
        
        # Stop Session button
        self.stop_btn = tk.Button(btn_frame, text="Stop Session", font=("Arial", 12, "bold"),
                  bg="#c0392b", fg="white", width=14, command=self.stop_session)
        self.stop_btn.grid(row=0, column=2, padx=6)
        self.stop_btn.config(state="disabled")

        # Debug/status label
        self.debug_lbl = tk.Label(body, text="", font=("Arial", 9), 
                                   fg="#f39c12", bg="#34495e")
        self.debug_lbl.pack(pady=5)

        # ========== Hardware State ==========
        self.db_acc = 0
        self.is_charging = False
        self.is_pending = False  # Waiting for plug detection
        self.unplug_time = None
        self.remaining = 0
        self._tick_job = None
        
        # Hardware monitoring
        self.tm = None
        self._wait_job = None
        self._hw_monitor_job = None
        self._poll_timeout_job = None
        
        # Current detection state
        self._charge_consecutive = 0
        self._charge_samples = []
        self._plug_hits = []
        self._unplug_hits = []
        self._baseline_current = 0
        self._plug_threshold = 0.10  # 100mA threshold
        self._unplug_threshold = 0.07  # 70mA threshold
        self._confirm_samples = 3
        self._sample_interval = 0.5  # seconds
        
        # Session tracking
        self.charging_uid = None
        self.charging_slot = None
        self._session_valid = False
        self._session_id = 0
        self._current_session_id = None
        
        # Timer display state
        self._last_timer_update = 0
        self.TIMER_UPDATE_INTERVAL = 5
        
        # Update slot label immediately
        self._update_slot_label()
        
        # Hardware debug info
        if self.hw:
            self.debug_lbl.config(text=f"Hardware: {self.hw.mode} mode ready")
        else:
            self.debug_lbl.config(text="WARNING: No hardware controller")

    def _update_slot_label(self):
        """Update the slot label with current slot information."""
        slot = self.controller.active_slot
        if slot:
            slot_num = self._get_slot_number(slot)
            self.slot_lbl.config(text=f"Charging Slot {slot_num}")
        else:
            self.slot_lbl.config(text="No Slot Selected")

    def _get_slot_number(self, slot):
        """Extract slot number from slot string (e.g., 'slot1' -> 1)."""
        try:
            if slot and isinstance(slot, str):
                import re
                numbers = re.findall(r'\d+', slot)
                if numbers:
                    return int(numbers[0])
            return 0
        except Exception:
            return 0

    def _get_session_uid(self):
        """Get the current session UID."""
        return self.charging_uid or self.controller.active_uid

    def _update_progress_bar(self, remaining, total):
        """Update the progress bar."""
        if total <= 0:
            return
        percentage = min(100, max(0, (remaining / total) * 100))
        width = int((percentage / 100) * 300)
        self.progress_bar.coords(self.progress_rect, 0, 0, width, 20)
        
        # Change color based on remaining time
        if percentage > 50:
            color = "#27ae60"  # Green
        elif percentage > 20:
            color = "#f39c12"  # Orange
        else:
            color = "#e74c3c"  # Red
        self.progress_bar.itemconfig(self.progress_rect, fill=color)

    def refresh(self):
        """Refresh the charging screen display."""
        self._update_slot_label()
        self.user_info.refresh()
        
        # Update time display
        uid = self.controller.active_uid
        if uid:
            user = read_user(uid)
            if user:
                cb = user.get("charge_balance", 0) or 0
                self.time_var.set(self._format_time(cb))
                
                # Update progress bar
                self._update_progress_bar(cb, max(cb, 3600))  # Use max of balance or 1 hour for scale
                
                # Update button states
                slot = self.controller.active_slot
                if slot:
                    slot_data = read_slot(slot)
                    if slot_data:
                        status = slot_data.get("status", "inactive")
                        if status == "active":
                            self.start_btn.config(state="disabled", text="Charging...")
                            self.stop_btn.config(state="normal")
                            self.status_lbl.config(text="Charging in progress...", fg="white")
                        elif status == "pending":
                            self.start_btn.config(state="disabled", text="Waiting for plug...")
                            self.stop_btn.config(state="normal")
                            self.status_lbl.config(text="Please plug in your device", fg="#f39c12")
                        else:
                            self.start_btn.config(state="normal", text="Start Charging")
                            self.stop_btn.config(state="normal")
                            self.status_lbl.config(text="Ready to start charging", fg="white")
                    else:
                        self.start_btn.config(state="normal", text="Start Charging")
                        self.stop_btn.config(state="normal")
                        self.status_lbl.config(text="Ready to start charging", fg="white")
                else:
                    self.start_btn.config(state="disabled", text="Start Charging")
                    self.stop_btn.config(state="disabled")
                    self.status_lbl.config(text="Select a slot first", fg="yellow")
        else:
            self.start_btn.config(state="disabled", text="Start Charging")
            self.stop_btn.config(state="disabled")
            self.status_lbl.config(text="Scan RFID first", fg="yellow")

    def _format_time(self, seconds):
        """Format seconds to MM:SS or HH:MM."""
        if seconds >= 3600:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}:{minutes:02d}"
        else:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes}:{secs:02d}"

    def update_balance_display(self):
        """Update the displayed balance from database."""
        uid = self.controller.active_uid
        if uid:
            user = read_user(uid)
            if user:
                cb = user.get("charge_balance", 0) or 0
                self.time_var.set(self._format_time(cb))
                
                # Update timer display if available
                slot = self.charging_slot or self.controller.active_slot
                slot_num = self._get_slot_number(slot)
                if slot_num > 0 and self.controller.timer_available:
                    self.controller.send_timer_command(f"SLOT{slot_num}:{cb}")

    # ========== HARDWARE CONTROL METHODS ==========
    
    def _block_coins_for_hardware(self, duration_ms=3000, reason=""):
        """Block coins during hardware operations using TEMP_BLOCK_ON."""
        if self.controller.arduino_available:
            print(f"[COIN_BLOCK] Blocking coins for {reason}")
            success = self.controller.send_arduino_command("TEMP_BLOCK_ON")
            if success:
                print(f"[COIN_BLOCK] Successfully blocked coins for {reason}")
            else:
                print(f"[COIN_BLOCK] Failed to send block command")
            return success
        return False

    def _unblock_coins(self):
        """Unblock coins using TEMP_BLOCK_OFF."""
        if self.controller.arduino_available:
            success = self.controller.send_arduino_command("TEMP_BLOCK_OFF")
            if success:
                print("[COIN_BLOCK] Coins unblocked")
            else:
                print("[COIN_BLOCK] Failed to unblock coins")
            return success
        return False

    def _setup_hardware_for_slot(self, slot):
        """Setup hardware for the given slot."""
        if not self.hw:
            self.debug_lbl.config(text="ERROR: No hardware controller")
            print("ERROR: No hardware controller available")
            return False
            
        try:
            print(f"[HARDWARE_SETUP] Setting up hardware for {slot}")
            
            # Check if slot has hardware mapping
            if hasattr(self.hw, 'pinmap'):
                acs_channels = self.hw.pinmap.get('acs712_channels', {})
                if slot not in acs_channels:
                    self.debug_lbl.config(text=f"ERROR: No hardware mapping for {slot}")
                    print(f"ERROR: No ACS712 channel mapping for {slot}")
                    return False
            
            # Calibrate current sensor baseline
            self.debug_lbl.config(text=f"Calibrating current sensor for {slot}...")
            self.update_idletasks()
            
            # Block coins during calibration
            self._block_coins_for_hardware(2000, "calibration")
            
            # Take multiple baseline readings
            baseline_readings = []
            for i in range(10):
                try:
                    current = self.hw.read_current(slot)
                    if current is not None:
                        baseline_readings.append(current.get('amps', 0))
                    time.sleep(0.1)
                except Exception as e:
                    print(f"Calibration error: {e}")
            
            if baseline_readings:
                self._baseline_current = sum(baseline_readings) / len(baseline_readings)
                print(f"[CALIBRATION] Baseline current for {slot}: {self._baseline_current:.3f}A")
                self.debug_lbl.config(text=f"Baseline: {self._baseline_current:.3f}A")
            else:
                self._baseline_current = 0
                print(f"WARN: Could not calibrate {slot}, using 0 baseline")
            
            # Unblock coins after calibration
            self._unblock_coins()
            
            return True
            
        except Exception as e:
            print(f"ERROR setting up hardware for {slot}: {e}")
            import traceback
            traceback.print_exc()
            self.debug_lbl.config(text=f"Hardware error: {e}")
            return False

    def _unlock_slot_for_user(self, slot):
        """Unlock the slot for user to plug in device."""
        if not self.hw:
            self.debug_lbl.config(text="ERROR: No hardware controller")
            print("ERROR: No hardware controller for unlock")
            return False
            
        try:
            # Block coins before solenoid operation
            print(f"[COIN_BLOCK] Blocking coins for unlock_solenoid")
            if self.controller.arduino_available:
                self.controller.send_arduino_command("TEMP_BLOCK_ON")
            
            # Ensure hardware is ready
            if not hasattr(self.hw, 'setup'):
                print("WARNING: Hardware may not be set up")
            
            # Turn on relay power to the slot
            self.hw.relay_on(slot)
            print(f"[RELAY] Power ON for {slot}")
            
            # UNLOCK solenoid (engage lock - solenoid ON)
            # For most solenoids: lock=True = solenoid ON (unlocked)
            # For active-low relays: adjust as needed
            self.hw.lock_slot(slot, lock=True)  
            print(f"[SOLENOID] UNLOCKED (solenoid ON) for {slot}")
            
            # Update UI
            slot_num = self._get_slot_number(slot)
            self.status_lbl.config(text=f"Slot {slot_num} UNLOCKED - Plug in device (5s)", fg="#f39c12")
            self.debug_lbl.config(text="Solenoid unlocked for 5 seconds")
            
            return True
            
        except Exception as e:
            print(f"ERROR unlocking slot {slot}: {e}")
            import traceback
            traceback.print_exc()
            self.debug_lbl.config(text=f"Unlock error: {e}")
            # Try to unblock coins on error
            try:
                self._unblock_coins()
            except:
                pass
            return False

    def _lock_slot_after_timeout(self, slot):
        """Lock the slot after unlock timeout."""
        if not self.hw:
            print("ERROR: No hardware controller for lock")
            return
            
        try:
            # Block coins before locking
            print(f"[COIN_BLOCK] Blocking coins for lock_solenoid")
            if self.controller.arduino_available:
                self.controller.send_arduino_command("TEMP_BLOCK_ON")
            
            # LOCK solenoid (disengage lock - solenoid OFF)
            self.hw.lock_slot(slot, lock=False)
            print(f"[SOLENOID] LOCKED (solenoid OFF) for {slot}")
            
            # Update UI
            slot_num = self._get_slot_number(slot)
            self.status_lbl.config(text=f"Slot {slot_num} ready - Plug in device", fg="#f39c12")
            self.debug_lbl.config(text="Solenoid locked, waiting for device...")
            
            # Auto-unblock coins after 2.5 seconds
            def _release_block():
                if self.controller.arduino_available:
                    success = self.controller.send_arduino_command("TEMP_BLOCK_OFF")
                    if success:
                        print("[COIN_BLOCK] Temporary block released")
                    else:
                        print("[COIN_BLOCK] Failed to release block")
            
            self.after(2500, _release_block)
            
        except Exception as e:
            print(f"ERROR locking slot {slot}: {e}")
            import traceback
            traceback.print_exc()
            self.debug_lbl.config(text=f"Lock error: {e}")

    def _read_current_with_baseline(self, slot):
        """Read current and subtract baseline."""
        if not self.hw:
            return 0
            
        try:
            current_data = self.hw.read_current(slot)
            if not current_data:
                return 0
                
            # Get RMS current (smoothed)
            net_current = current_data.get('amps', 0)
            
            # Update display
            self.current_lbl.config(text=f"Current: {net_current:.2f}A")
            
            return net_current
            
        except Exception as e:
            print(f"ERROR reading current: {e}")
            return 0

    # ========== SESSION CONTROL METHODS ==========
    
    def start_charging(self):
        """Start the charging session with full hardware integration."""
        uid = self.controller.active_uid
        if not uid:
            self.status_lbl.config(text="ERROR: No user logged in", fg="red")
            print("ERROR: No user logged in")
            return
        
        user = read_user(uid)
        cb = user.get("charge_balance", 0) if user else 0
        if (cb or 0) <= 0:
            self.status_lbl.config(text="ERROR: No charge balance. Add coins first.", fg="red")
            print("ERROR: No charge balance")
            return
        
        slot = self.controller.active_slot
        if not slot:
            self.status_lbl.config(text="ERROR: No slot selected.", fg="red")
            print("ERROR: No slot selected")
            return
        
        slot_num = self._get_slot_number(slot)
        
        # Check if slot is already active
        slot_data = read_slot(slot)
        if slot_data and slot_data.get("status") in ["active", "pending"]:
            self.status_lbl.config(text="ERROR: Slot is already in use.", fg="red")
            print(f"ERROR: Slot {slot} already in use")
            return
        
        # Set session info
        self.charging_uid = uid
        self.charging_slot = slot
        self._session_valid = True
        self._session_id += 1
        self._current_session_id = self._session_id
        self.remaining = cb
        
        print(f"[SESSION_START] Starting charging for {uid} on {slot} with {cb}s")
        
        # Setup hardware
        if not self._setup_hardware_for_slot(slot):
            self.status_lbl.config(text="ERROR: Hardware setup failed", fg="red")
            print("ERROR: Hardware setup failed")
            return
        
        # Update database to pending state
        write_user(uid, {
            "charging_status": "pending",
            "occupied_slot": slot
        })
        
        write_slot(slot, {
            "status": "pending",
            "current_user": uid
        })
        
        # Update timer display
        if self.controller.timer_available and slot_num > 0:
            self.controller.send_timer_command(f"SLOT{slot_num}:{cb}")
        
        # Update UI
        self.is_pending = True
        self.start_btn.config(state="disabled", text="Waiting...")
        self.stop_btn.config(state="normal")
        self.time_var.set(self._format_time(cb))
        self._update_progress_bar(cb, max(cb, 3600))
        
        # Unlock slot for user
        if not self._unlock_slot_for_user(slot):
            self.status_lbl.config(text="ERROR: Failed to unlock slot", fg="red")
            print("ERROR: Failed to unlock slot")
            self._cleanup_pending_session()
            return
        
        # Schedule lock after 5 seconds
        self.after(5000, lambda: self._lock_slot_after_timeout(slot))
        
        # Start plug detection polling
        self._wait_job = self.after(6000, self._poll_for_charging_start)  # Start after unlock period
        
        # Start timeout for no detection
        self._poll_timeout_job = self.after(60000, self._poll_no_detect_timeout)  # 60 second timeout
        
        print(f"INFO: Charging session started successfully for {uid} on {slot}")

    def _cleanup_pending_session(self):
        """Clean up a pending session that never started charging."""
        slot = self.charging_slot
        uid = self._get_session_uid()
        
        print(f"[CLEANUP] Cleaning up pending session for {uid} on {slot}")
        
        if slot and self.hw:
            try:
                # Block coins during cleanup
                self._block_coins_for_hardware(2000, "cleanup")
                
                # Turn off relay power
                self.hw.relay_off(slot)
                print(f"[RELAY] Power OFF for {slot}")
                
                # Ensure solenoid is locked
                self.hw.lock_slot(slot, lock=False)
                print(f"[SOLENOID] LOCKED for cleanup")
                
                # Unblock coins
                self._unblock_coins()
            except Exception as e:
                print(f"ERROR cleaning hardware: {e}")
        
        # Update database
        if uid:
            write_user(uid, {
                "charging_status": "idle",
                "occupied_slot": "none"
            })
            
            if slot:
                write_slot(slot, {
                    "status": "inactive",
                    "current_user": "none"
                })
        
        # Update timer display
        slot_num = self._get_slot_number(slot)
        if slot_num > 0 and self.controller.timer_available:
            self.controller.send_timer_command(f"SLOT{slot_num}:-")
        
        # Reset state
        self._clear_session_state()

    def _clean_hardware(self, slot):
        """Clean up hardware resources."""
        if not slot or not self.hw:
            print(f"WARNING: No hardware to clean for {slot}")
            return
            
        try:
            print(f"[HARDWARE_CLEAN] Cleaning hardware for {slot}")
            
            # Block coins before hardware operations
            self._block_coins_for_hardware(2000, "hardware_clean")
            
            # First, lock solenoid (disengaged)
            self.hw.lock_slot(slot, lock=False)
            print(f"[SOLENOID_CLEAN] LOCKED (solenoid OFF) for {slot}")
            
            # Then turn off relay power
            self.hw.relay_off(slot)
            print(f"[RELAY_CLEAN] Power OFF for {slot}")
            
            # Unblock coins after operations
            self._unblock_coins()
            
        except Exception as e:
            print(f"ERROR cleaning hardware for {slot}: {e}")
            import traceback
            traceback.print_exc()

    def _cancel_all_jobs(self):
        """Cancel all scheduled jobs."""
        jobs = ['_tick_job', '_wait_job', '_hw_monitor_job', '_poll_timeout_job']
        for job_name in jobs:
            job = getattr(self, job_name, None)
            if job:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
                setattr(self, job_name, None)

    def _clear_session_state(self):
        """Clear all session-related state."""
        self.is_charging = False
        self.is_pending = False
        self._session_valid = False
        self.remaining = 0
        self.charging_uid = None
        self.charging_slot = None
        self._current_session_id = None
        self.unplug_time = None
        self._charge_samples = []
        self._plug_hits = []
        self._unplug_hits = []
        self._baseline_current = 0

    def stop_session(self):
        """Stop the charging session with hardware cleanup."""
        if not self.is_charging and not self.is_pending:
            self.status_lbl.config(text="No active session to stop", fg="yellow")
            return
        
        uid = self._get_session_uid()
        slot = self.charging_slot or self.controller.active_slot
        slot_num = self._get_slot_number(slot)
        
        print(f"[SESSION_STOP] Stopping session for {uid} on {slot}")
        
        # Cancel all scheduled jobs
        self._cancel_all_jobs()
        
        # Block coins during hardware shutdown
        print(f"[COIN_BLOCK_START] Blocking coins for hardware shutdown")
        if self.controller.arduino_available:
            self.controller.send_arduino_command("TEMP_BLOCK_ON")
        
        # Clean hardware (solenoid unlock first, then power off)
        self._clean_hardware(slot)
        
        # Update database
        if uid:
            remaining = self.remaining if hasattr(self, 'remaining') else 0
            write_user(uid, {
                "charge_balance": remaining,
                "charging_status": "idle",
                "occupied_slot": "none"
            })
            
            if slot:
                write_slot(slot, {
                    "status": "inactive",
                    "current_user": "none"
                })
        
        # Update timer display
        if slot_num > 0 and self.controller.timer_available:
            self.controller.send_timer_command(f"SLOT{slot_num}:-")
        
        # Reset state
        self._clear_session_state()
        
        # Update UI
        self.start_btn.config(state="normal", text="Start Charging")
        self.stop_btn.config(state="disabled")
        self.status_lbl.config(text="Charging session stopped", fg="white")
        self.debug_lbl.config(text="Session stopped by user")
        self.time_var.set("0:00")
        self._update_progress_bar(0, 100)
        self.current_lbl.config(text="Current: 0.00A")
        
        # Release coin block after delay
        def _release_final():
            if self.controller.arduino_available:
                success = self.controller.send_arduino_command("TEMP_BLOCK_OFF")
                if success:
                    print("[COIN_BLOCK_END] Temporary block released")
                else:
                    print("[COIN_BLOCK_END] Failed to release block")
        
        self.after(3000, _release_final)
        
        print(f"INFO: Charging session stopped for slot {slot_num}")
        
        # Return to main screen after delay
        self.after(2000, lambda: self.controller.show_frame(MainScreen))

    # ========== PLUG DETECTION METHODS ==========
    
    def _poll_for_charging_start(self):
        """Poll for device plug detection."""
        if not self._session_valid or not self.is_pending:
            return
            
        slot = self.charging_slot
        if not slot or not self.hw:
            return
            
        try:
            # Read current
            current = self._read_current_with_baseline(slot)
            
            # Add to samples buffer (keep last 5 samples)
            self._charge_samples.append(current)
            if len(self._charge_samples) > 5:
                self._charge_samples.pop(0)
            
            # Check if current exceeds threshold
            if current > self._plug_threshold:
                self._plug_hits.append(time.time())
                # Keep only recent hits (last 10 seconds)
                self._plug_hits = [t for t in self._plug_hits if time.time() - t < 10]
                
                # If we have enough consecutive hits, start charging
                if len(self._plug_hits) >= self._confirm_samples:
                    print(f"[PLUG_DETECTED] Current: {current:.2f}A, Starting charging...")
                    self._start_actual_charging()
                    return
            else:
                # Reset hits if current drops
                self._plug_hits = []
            
            # Update status
            avg_current = sum(self._charge_samples) / len(self._charge_samples) if self._charge_samples else 0
            self.status_lbl.config(text=f"Waiting for device... Current: {avg_current:.2f}A", fg="#f39c12")
            
            # Schedule next poll
            self._wait_job = self.after(int(self._sample_interval * 1000), self._poll_for_charging_start)
            
        except Exception as e:
            print(f"ERROR in plug detection: {e}")
            self.debug_lbl.config(text=f"Detection error: {e}")
            # Retry after error
            self._wait_job = self.after(1000, self._poll_for_charging_start)

    def _poll_no_detect_timeout(self):
        """Timeout if no device detected within 60 seconds."""
        if self.is_pending:
            print("[TIMEOUT] No device detected within 60 seconds")
            self.status_lbl.config(text="Timeout: No device detected", fg="#e74c3c")
            self.debug_lbl.config(text="Session cancelled - no device plugged in")
            
            # Clean up
            self._cleanup_pending_session()
            
            # Return to slot selection
            self.after(3000, lambda: self.controller.show_frame(SlotSelectScreen))

    def _start_actual_charging(self):
        """Start actual charging after plug detection."""
        slot = self.charging_slot
        slot_num = self._get_slot_number(slot)
        
        # Update database
        uid = self._get_session_uid()
        if uid:
            write_user(uid, {"charging_status": "charging"})
            write_slot(slot, {"status": "active"})
        
        # Update UI
        self.is_pending = False
        self.is_charging = True
        self.start_btn.config(state="disabled", text="Charging...")
        self.stop_btn.config(state="normal")
        self.status_lbl.config(text=f"Charging started on Slot {slot_num}!", fg="#27ae60")
        self.debug_lbl.config(text="Device detected, charging started")
        
        # Cancel timeout job
        if self._poll_timeout_job:
            try:
                self.after_cancel(self._poll_timeout_job)
            except Exception:
                pass
            self._poll_timeout_job = None
        
        # Start hardware monitoring
        self._start_hardware_monitoring()
        
        # Start countdown
        self._start_countdown()

    # ========== COUNTDOWN METHODS ==========
    
    def _start_countdown(self):
        """Start the countdown timer."""
        if not self._session_valid or not self.is_charging:
            return
        
        if self.remaining <= 0:
            self._end_session_timeout()
            return
        
        # Decrement time
        self.remaining -= 1
        self.time_var.set(self._format_time(self.remaining))
        self._update_progress_bar(self.remaining, self.remaining + self.db_acc)
        
        # Update database periodically
        if self.remaining % 30 == 0:  # Every 30 seconds
            uid = self._get_session_uid()
            if uid:
                write_user(uid, {"charge_balance": self.remaining})
        
        # Update timer display periodically
        current_time = time.time()
        if current_time - self._last_timer_update >= self.TIMER_UPDATE_INTERVAL:
            slot_num = self._get_slot_number(self.charging_slot)
            if slot_num > 0 and self.controller.timer_available:
                self.controller.send_timer_command(f"SLOT{slot_num}:{self.remaining}")
                self._last_timer_update = current_time
        
        # Schedule next tick
        self._tick_job = self.after(1000, self._start_countdown)

    def _start_hardware_monitoring(self):
        """Monitor hardware during charging."""
        if not self._session_valid or not self.is_charging:
            return
            
        slot = self.charging_slot
        if not slot or not self.hw:
            return
            
        try:
            # Read current
            current = self._read_current_with_baseline(slot)
            
            # Check for unplug (current drops below threshold)
            if current < self._unplug_threshold:
                self._unplug_hits.append(time.time())
                # Keep only recent hits (last 30 seconds)
                self._unplug_hits = [t for t in self._unplug_hits if time.time() - t < 30]
                
                # If we have enough consecutive low readings, handle unplug
                if len(self._unplug_hits) >= self._confirm_samples:
                    print(f"[UNPLUG_DETECTED] Current: {current:.2f}A")
                    self._handle_unplug_detected()
                    return
            else:
                # Reset unplug hits if current is good
                self._unplug_hits = []
            
            # Schedule next monitor
            self._hw_monitor_job = self.after(int(self._sample_interval * 1000), self._start_hardware_monitoring)
            
        except Exception as e:
            print(f"ERROR in hardware monitoring: {e}")
            # Retry after error
            self._hw_monitor_job = self.after(1000, self._start_hardware_monitoring)

    def _handle_unplug_detected(self):
        """Handle device unplug detection."""
        print("[UNPLUG] Device unplugged detected")
        
        # Record unplug time
        self.unplug_time = time.time()
        
        # Update UI
        self.status_lbl.config(text="Device unplugged! Plug back in within 30s...", fg="#e74c3c")
        self.debug_lbl.config(text="Unplug detected, grace period started")
        
        # Start grace period countdown
        self._start_grace_period()

    def _start_grace_period(self):
        """Start grace period after unplug."""
        if not self._session_valid:
            return
            
        elapsed = time.time() - self.unplug_time
        
        if elapsed >= UNPLUG_GRACE_SECONDS:
            # Grace period expired
            print(f"[GRACE_PERIOD] Expired after {elapsed:.0f}s")
            self.status_lbl.config(text="Grace period expired", fg="#e74c3c")
            self._end_session_unplug()
        else:
            # Update countdown
            remaining = UNPLUG_GRACE_SECONDS - int(elapsed)
            self.status_lbl.config(text=f"Plug back in: {remaining}s remaining", fg="#f39c12")
            
            # Check if device was re-plugged
            slot = self.charging_slot
            if slot and self.hw:
                current = self._read_current_with_baseline(slot)
                if current > self._plug_threshold:
                    print("[RE-PLUG] Device re-plugged during grace period")
                    self._handle_replug_detected()
                    return
            
            # Continue grace period
            self._hw_monitor_job = self.after(1000, self._start_grace_period)

    def _handle_replug_detected(self):
        """Handle device re-plug during grace period."""
        print("[RE-PLUG] Device re-plugged, resuming charging")
        
        # Reset unplug tracking
        self.unplug_time = None
        self._unplug_hits = []
        
        # Update UI
        self.status_lbl.config(text="Device re-plugged, charging resumed", fg="#27ae60")
        self.debug_lbl.config(text="Charging resumed after re-plug")
        
        # Resume hardware monitoring
        self._start_hardware_monitoring()

    def _end_session_timeout(self):
        """Handle session timeout (when time runs out)."""
        uid = self._get_session_uid()
        slot = self.charging_slot
        slot_num = self._get_slot_number(slot)
        
        print(f"[TIME_COMPLETE] Charging time completed for {uid}")
        
        # Cancel all jobs
        self._cancel_all_jobs()
        
        # Block coins during shutdown
        print(f"[COIN_BLOCK] Blocking coins for time_complete")
        self._block_coins_for_hardware(3000, "time_complete")
        
        # Clean hardware
        self._clean_hardware(slot)
        
        # Update database
        if uid:
            write_user(uid, {
                "charge_balance": 0,
                "charging_status": "idle",
                "occupied_slot": "none"
            })
            
            if slot:
                write_slot(slot, {
                    "status": "inactive",
                    "current_user": "none"
                })
        
        # Update timer display
        if slot_num > 0 and self.controller.timer_available:
            self.controller.send_timer_command(f"SLOT{slot_num}:-")
        
        # Update UI
        self.start_btn.config(state="normal", text="Start Charging")
        self.stop_btn.config(state="disabled")
        self.status_lbl.config(text="Charging time completed!", fg="green")
        self.debug_lbl.config(text="Session completed normally")
        self.time_var.set("0:00")
        self._update_progress_bar(0, 100)
        self.current_lbl.config(text="Current: 0.00A")
        
        # Unblock coins after delay
        def _unblock_after_timeout():
            self._unblock_coins()
        
        self.after(3500, _unblock_after_timeout)
        
        # Clear state
        self._clear_session_state()
        
        # Return to main screen after delay
        self.after(3000, lambda: self.controller.show_frame(MainScreen))

    def _end_session_unplug(self):
        """Handle session end due to unplug."""
        uid = self._get_session_uid()
        slot = self.charging_slot
        slot_num = self._get_slot_number(slot)
        
        print(f"[UNPLUG_END] Session ended due to unplug for {uid}")
        
        # Cancel all jobs
        self._cancel_all_jobs()
        
        # Block coins during shutdown
        print(f"[COIN_BLOCK] Blocking coins for unplug_end")
        self._block_coins_for_hardware(3000, "unplug_end")
        
        # Clean hardware
        self._clean_hardware(slot)
        
        # Update database (keep remaining time)
        if uid:
            remaining = self.remaining if hasattr(self, 'remaining') else 0
            write_user(uid, {
                "charge_balance": remaining,
                "charging_status": "idle",
                "occupied_slot": "none"
            })
            
            if slot:
                write_slot(slot, {
                    "status": "inactive",
                    "current_user": "none"
                })
        
        # Update timer display
        if slot_num > 0 and self.controller.timer_available:
            self.controller.send_timer_command(f"SLOT{slot_num}:-")
        
        # Update UI
        self.start_btn.config(state="normal", text="Start Charging")
        self.stop_btn.config(state="disabled")
        self.status_lbl.config(text="Session ended (device unplugged)", fg="#e74c3c")
        self.debug_lbl.config(text="Session ended due to unplug")
        self.time_var.set(self._format_time(remaining) if hasattr(self, 'remaining') else "0:00")
        self.current_lbl.config(text="Current: 0.00A")
        
        # Unblock coins after delay
        def _unblock_after_unplug():
            self._unblock_coins()
        
        self.after(3500, _unblock_after_unplug)
        
        # Clear state
        self._clear_session_state()
        
        # Return to main screen after delay
        self.after(3000, lambda: self.controller.show_frame(MainScreen))
        
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
        self.is_dispensing = False
        
        # Animation variables
        self.animation_job = None
        self.animation_start_time = 0
        self.animation_total_ml = 0
        self.animation_total_seconds = 0
        self.animation_steps = 0
        self.animation_step_size = 10  # FIXED 10mL steps
        self.animation_step_delay = 0
        self.animation_current_ml = 0
        self.animation_dispensed_so_far = 0  # track amount dispensed so far
        
        # Test Arduino connection
        self.test_arduino_connection()

    def handle_arduino_event(self, event, value):
        """Handle Arduino events in WaterScreen with smooth animation."""
        print(f"WaterScreen received: {event} = {value}")
        
        try:
         
            if event == 'coin':
                # DIRECT HANDLING - bypass KioskApp routing
                print(f"DIRECT COIN HANDLING IN WATERSCREEN: P{value}")
                uid = self.controller.active_uid
                if not uid:
                    print("No user logged in")
                    return
                    
                water_ml_map = {1: 50, 5: 250, 10: 500}
                added_ml = water_ml_map.get(value, 0)
                
                if added_ml > 0:
                    # Update user balance directly
                    user = read_user(uid)
                    if user.get("type") == "member":
                        current_balance = user.get("water_balance", 0) or 0
                        new_balance = current_balance + added_ml
                        write_user(uid, {"water_balance": new_balance})
                    else:
                        current_balance = user.get("temp_water_time", 0) or 0
                        new_balance = current_balance + added_ml
                        write_user(uid, {"temp_water_time": new_balance})
                    
                    # Update WaterScreen UI
                    self.time_var.set(str(new_balance))
                    if new_balance > 0:
                        self.status_lbl.config(text=f"Balance: {new_balance}mL - Place cup to start")
                    
                    # Force UserInfoFrame update
                    if hasattr(self.controller, 'refresh_all_user_info'):
                        self.controller.refresh_all_user_info()
                    
                    # Show popup
                    self.controller.show_coin_popup(uid, peso=value, added_ml=added_ml, total_ml=new_balance)
                    
                    print(f"Updated balance: {new_balance}mL")
                return
       

            elif event == 'cup_detected':
                self.cup_present = True
                self.status_lbl.config(text="Cup detected - Starting countdown...")
                self.debug_var.set("Cup detected - Countdown starting")
                print("CUP_DETECTED: Starting countdown")
                
            elif event == 'countdown':
                seconds = value
                self.status_lbl.config(text=f"Starting in: {seconds} seconds...")
                self.debug_var.set(f"Countdown: {seconds}")
                print(f"COUNTDOWN: {seconds} seconds remaining")
                
            elif event == 'countdown_end':
                self.status_lbl.config(text="DISPENSING WATER...")
                self.debug_var.set("Countdown complete - Dispensing started")
                print("COUNTDOWN_END: Starting to dispense")
                
            elif event == 'dispense_start':
                self.is_dispensing = True
                self.status_lbl.config(text="DISPENSING WATER...")
                self.debug_var.set("Dispensing started")
                print("DISPENSE_START: Water flow started")
                
            elif event == 'animation_start':
                # value should be a dict with total_ml and total_seconds
                if isinstance(value, dict):
                    total_ml = int(value.get("total_ml", 0))
                    total_seconds = int(value.get("total_seconds", 0))
                    
                    if total_ml > 0 and total_seconds > 0:
                        # OPTIONAL: ensure the WaterScreen is visible so user sees animation
                        # Uncomment next line if you want the UI to switch to water screen automatically
                        # self.controller.show_frame(WaterScreen)

                        self._start_smooth_animation(total_ml, total_seconds)
                        print(f"ANIMATION_START: {total_ml}mL in {total_seconds} seconds")
                    else:
                        print(f"Invalid animation parameters: {value}")
                else:
                    print(f"Unexpected animation data type: {type(value)}")
                return
                
            elif event == 'dispense_done':
                dispensed_ml = 0
                try:
                    dispensed_ml = int(value) if isinstance(value, (int, str)) else self.animation_dispensed_so_far
                except Exception:
                    dispensed_ml = self.animation_dispensed_so_far
                # Stop any running animation safely
                self._stop_animation()
                self._end_dispensing_complete(f"Dispensing completed: {dispensed_ml}mL")
                print(f"DISPENSE_DONE: {dispensed_ml}mL dispensed")
                
            elif event == 'cup_removed':
                self.cup_present = False
                # Stop animation if cup is removed
                self._stop_animation()
                    
                if self.is_dispensing:
                    self.status_lbl.config(text="Cup removed - Dispensing paused")
                    self.debug_var.set("Cup removed - Dispensing paused")
                else:
                    self.status_lbl.config(text="Cup removed")
                    self.debug_var.set("Cup removed")
                print("CUP_REMOVED: Cup taken away")
                
            elif event == 'system_ready':
                self.debug_var.set("Arduino system ready")
                self.status_lbl.config(text="System ready - Insert coins")
                print("SYSTEM_READY: Arduino connected")
                
        except Exception as e:
            print(f"ERROR in WaterScreen event handler: {e}")
            self.debug_var.set(f"Event error: {e}")

    def _stop_animation(self):
        """Cancel currently running animation job if any and mark dispensing stopped."""
        try:
            if self.animation_job:
                try:
                    self.after_cancel(self.animation_job)
                except Exception:
                    pass
                self.animation_job = None
            self.is_dispensing = False
        except Exception as e:
            print(f"Error stopping animation: {e}")

    def _start_smooth_animation(self, total_ml, total_seconds):
        """Start smooth countdown animation that ends exactly when estimated time completes."""
        # Cancel any existing animation first
        self._stop_animation()

        # FIXED: Animation should match estimated time, not actual flow
        # We want the countdown to reach zero exactly when total_seconds elapses
        
        # Calculate step size and timing to match the estimated duration
        if total_seconds <= 0:
            total_seconds = max(1, total_ml / 41.7)  # Fallback calculation
        
        # FIXED: Use fixed step size but calculate delay to match total_seconds exactly
        step_size_ml = 10  # Fixed 10mL steps as you want
        total_steps = (total_ml + step_size_ml - 1) // step_size_ml  # Ceiling division
        
        if total_steps <= 0:
            total_steps = 1
        
        # CRITICAL FIX: Calculate step delay to ensure animation completes in exactly total_seconds
        total_time_ms = int(total_seconds * 1000)
        step_delay_ms = max(50, total_time_ms // total_steps)  # Minimum 50ms per step
        
        # Recalculate to ensure perfect timing
        actual_total_time_ms = step_delay_ms * total_steps
        actual_total_seconds = actual_total_time_ms / 1000.0
        
        # If we're running too long, adjust step size to maintain timing
        if actual_total_seconds > total_seconds * 1.1:  # If more than 10% over
            # Increase step size to reduce number of steps
            step_size_ml = 20
            total_steps = (total_ml + step_size_ml - 1) // step_size_ml
            step_delay_ms = max(50, total_time_ms // total_steps)
            actual_total_time_ms = step_delay_ms * total_steps
            actual_total_seconds = actual_total_time_ms / 1000.0

        # Store animation parameters
        self.animation_total_ml = total_ml
        self.animation_total_seconds = total_seconds
        self.animation_steps = total_steps
        self.animation_step_size = step_size_ml
        self.animation_step_delay = step_delay_ms
        self.animation_current_ml = total_ml
        self.animation_start_time = time.time()
        self.animation_dispensed_so_far = 0
        self.animation_target_end_time = time.time() + total_seconds  # Exact end time

        self.is_dispensing = True
        # Update UI
        self.time_var.set(str(int(self.animation_current_ml)))
        self.status_lbl.config(text=f"Dispensing... {int(self.animation_current_ml)}mL remaining")
        
        # DEBUG: Show exact timing
        self.debug_var.set(f"Anim: {step_size_ml}mL/step, {step_delay_ms}ms, target: {total_seconds}s")
        
        print(f"TIMED ANIMATION: {total_ml}mL -> 0mL in {total_steps} steps")
        print(f"STEP TIMING: {step_size_ml}mL every {step_delay_ms}ms = {actual_total_seconds:.1f}s total")
        print(f"TARGET: Complete in exactly {total_seconds}s (ends at {self.animation_target_end_time:.1f})")

        # Start animation
        try:
            self.animation_job = self.after(self.animation_step_delay, self._animation_tick)
        except Exception as e:
            print(f"Error scheduling animation tick: {e}")
            self.animation_job = None
            self.is_dispensing = False

    def _animation_tick(self):
        """Update the countdown animation with exact timing."""
        try:
            # Clear job id (we will re-set it if continuing)
            self.animation_job = None

            if not self.is_dispensing or self.animation_current_ml <= 0:
                self._stop_animation()
                return

            # Calculate time remaining to target end time
            current_time = time.time()
            time_remaining = self.animation_target_end_time - current_time
            
            # If we've reached or passed the target end time, jump to completion
            if time_remaining <= 0:
                print(f"TIME'S UP: Jumping to completion (remaining: {self.animation_current_ml}mL)")
                self.animation_current_ml = 0
                self.animation_dispensed_so_far = self.animation_total_ml
                
                # Update UI to show completion
                self.time_var.set("0")
                self.status_lbl.config(text="Dispensing complete!")
                self.debug_var.set("Animation complete (time target reached)")
                
                # Final balance updates
                self._finalize_dispensing()
                return

            # Normal decrement
            prev_ml = self.animation_current_ml
            self.animation_current_ml = max(0, self.animation_current_ml - self.animation_step_size)
            dispensed_this_tick = prev_ml - self.animation_current_ml
            self.animation_dispensed_so_far += dispensed_this_tick

            # Update UI immediately
            self.time_var.set(str(int(self.animation_current_ml)))
            
            # Show time-based countdown
            time_remaining_int = max(0, int(time_remaining))
            if time_remaining_int > 0:
                self.status_lbl.config(text=f"Dispensing... {int(self.animation_current_ml)}mL remaining (~{time_remaining_int}s)")
            else:
                self.status_lbl.config(text=f"Dispensing... {int(self.animation_current_ml)}mL remaining")
            
            self.debug_var.set(f"Time remaining: {time_remaining_int}s")
            self.update_idletasks()

            # Schedule next tick or finish
            if self.animation_current_ml > 0:
                # Calculate dynamic delay to stay on schedule
                current_time = time.time()
                next_scheduled_time = current_time + (self.animation_step_delay / 1000.0)
                
                # If we're running behind schedule, adjust delay to catch up
                if next_scheduled_time > self.animation_target_end_time:
                    # Speed up to finish on time
                    adjusted_delay = max(10, int((self.animation_target_end_time - current_time) * 1000))
                    print(f"ADJUSTING: Speeding up to {adjusted_delay}ms to finish on time")
                    self.animation_step_delay = adjusted_delay
                
                try:
                    self.animation_job = self.after(self.animation_step_delay, self._animation_tick)
                except Exception as e:
                    print(f"Error scheduling next animation tick: {e}")
                    self.animation_job = None
                    self.is_dispensing = False
            else:
                # Completed normally (reached 0mL)
                self._stop_animation()
                self.status_lbl.config(text="Dispensing complete!")
                self.debug_var.set("Animation complete")
                self._finalize_dispensing()

        except Exception as e:
            print(f"ERROR in _animation_tick: {e}")
            self._stop_animation()
            self.debug_var.set(f"Anim error: {e}")

    def _finalize_dispensing(self):
        """Final cleanup when animation completes."""
        try:
            uid = self.controller.active_uid
            if uid:
                user = read_user(uid)
                if user and user.get("type") == "member":
                    current_balance = user.get("water_balance", 0) or 0
                    new_balance = max(0, int(current_balance - self.animation_dispensed_so_far))
                    write_user(uid, {"water_balance": new_balance})
                else:
                    # ensure guest is zeroed
                    write_user(uid, {"temp_water_time": 0})
                    self.temp_water_time = 0
        except Exception as e:
            print(f"Error during final balance update: {e}")

        # Update UI then return to main after short pause
        self.time_var.set("0")
        self.update_idletasks()
        self.after(2000, lambda: self.controller.show_frame(MainScreen))

    def _update_water_balance(self, new_balance):
        """Update water balance in database and UI"""
        uid = self.controller.active_uid
        if not uid:
            return
            
        user = read_user(uid)
        if user and user.get("type") == "member":
            write_user(uid, {"water_balance": new_balance})
        else:
            write_user(uid, {"temp_water_time": new_balance})
            self.temp_water_time = new_balance
        
        # Update UI immediately
        self.time_var.set(str(new_balance))
        self.update_idletasks()
        self.controller.refresh_all_user_info()

    def _end_dispensing_complete(self, message):
        """End dispensing session completely and reset guest balance to zero."""
        # Stop any running animation
        self._stop_animation()
        
        self.is_dispensing = False
        self.cup_present = False
        self.status_lbl.config(text=message)
        self.debug_var.set("Dispensing complete")
        
        # CRITICAL FIX: Always reset balance to zero after dispensing
        uid = self.controller.active_uid
        if uid:
            user = read_user(uid)
            if user and user.get("type") == "nonmember":
                # GUEST ACCOUNT: Always reset to zero after use
                write_user(uid, {"temp_water_time": 0})
                self.temp_water_time = 0
                print(f"INFO: Guest account water balance reset to zero for UID: {uid}")
            else:
                # MEMBER ACCOUNT: Update balance normally (subtract what was actually dispensed)
                try:
                    current_balance = user.get("water_balance", 0) or 0
                    dispensed_ml = self.animation_dispensed_so_far if hasattr(self, 'animation_dispensed_so_far') else 0
                    new_balance = max(0, int(current_balance - dispensed_ml))
                    write_user(uid, {"water_balance": new_balance})
                except Exception as e:
                    print(f"Warning: couldn't update member balance: {e}")
        
        # Update UI immediately
        self.time_var.set("0")
        self.update_idletasks()
        self.controller.refresh_all_user_info()
        
        # Cancel any jobs
        if self._water_job:
            try:
                self.after_cancel(self._water_job)
            except Exception:
                pass
            self._water_job = None
            
        # Auto-return to main after completion
        self.after(3000, lambda: self.controller.show_frame(MainScreen))

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
        """Update WaterScreen display with current balance and set WATER mode."""
        try:
            # Set Arduino to WATER mode when this screen is shown
            try:
                if hasattr(self.controller, 'arduino_available') and self.controller.arduino_available:
                    if self.controller.send_arduino_command('WATER'):
                        print('INFO: WaterScreen - Arduino set to WATER mode')
                        self.debug_var.set("Arduino: WATER mode - Ready")
                    else:
                        self.debug_var.set("Arduino: Failed to set WATER mode")
            except Exception as e:
                print(f"WARN: Could not set Arduino mode: {e}")
                self.debug_var.set(f"Arduino error: {e}")
            
            self.user_info.refresh()
            
            uid = self.controller.active_uid
            if not uid:
                self.time_var.set("0")
                self.status_lbl.config(text="Please scan RFID first")
                return
                
            user = read_user(uid)
            if user.get("type") == "member":
                wb = user.get("water_balance", 0) or 0
                self.time_var.set(str(wb))
                status_text = f"Balance: {wb}mL - Place cup to start" if wb > 0 else "No water balance - Insert coins"
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


    def stop_session(self):
        """Stop charging session and clean up all resources."""
        
        # Get session info BEFORE any cleanup
        uid = self._get_session_uid()
        slot = self.charging_slot or self.controller.active_slot
        slot_num = self._get_slot_number(slot)
        
        print(f"[SESSION_STOP] Stopping session - UID: {uid}, Slot: {slot}, Slot#: {slot_num}")
        
        # 1. BLOCK COINS BEFORE SOLENOID OPERATION
        print(f"[COIN_BLOCK] Blocking coins for stop_session solenoid operations")
        if self.controller.arduino_available:
            # Block for 2 seconds (2000ms) - covers solenoid deactivation
            self.controller.send_arduino_command("BLOCK_COINS:2000")
        
        # 2. Cancel all scheduled jobs first
        self._cancel_all_jobs()
        
        # 3. Update timer display (physical 7-segment) to show slot is available
        if slot_num > 0 and self.controller.timer_available:
            try:
                self.controller.send_timer_command(f"SLOT{slot_num}:-")
                print(f"[TIMER] Physical timer for slot {slot_num} set to available")
            except Exception as e:
                print(f"[TIMER] WARN: Could not update timer display: {e}")
        
        # 4. Update database
        if uid:
            try:
                write_user(uid, {
                    "charging_status": "idle",
                    "occupied_slot": "none"
                })
                
                if slot:
                    write_slot(slot, {
                        "status": "inactive", 
                        "current_user": "none"
                    })
                    
                    if FIREBASE_AVAILABLE and users_ref:
                        users_ref.child(uid).child("slot_status").update({slot: "inactive"})
                
                print(f"[DB] Updated user {uid} and slot {slot} to inactive")
            except Exception as e:
                print(f"[DB] ERROR updating database: {e}")
        
        # 5. Clean hardware (including solenoid deactivation)
        self._clean_hardware(slot)
        
        # 6. Reset Arduino mode
        if self.controller.arduino_available:
            try:
                self.controller.send_arduino_command('RESET')
                print("[ARDUINO] Main Arduino reset")
            except Exception as e:
                print(f"[ARDUINO] WARN: Could not reset Arduino: {e}")
        
        # 7. Clear all session state
        self._clear_session_state()
        
        # 8. Force UNBLOCK coins after hardware cleanup (with delay)
        def _unblock_coins_final():
            if self.controller.arduino_available:
                success = self.controller.send_arduino_command("UNBLOCK_COINS")
                if success:
                    print("[COIN_BLOCK] Coins unblocked after stop_session")
                else:
                    print("[COIN_BLOCK] Failed to unblock coins")
        
        # Schedule unblock after 2.5 seconds (ensuring solenoid operations are complete)
        self.after(2500, _unblock_coins_final)
        
        # 9. Show main screen
        print(f"[SESSION_STOP] Charging session stopped for slot {slot_num}")
        self.controller.show_frame(MainScreen)


# ----------------- Rund App -----------------
if __name__ == "__main__":
    app = KioskApp()
    app.protocol("WM_DELETE_WINDOW", lambda: (app.cleanup(), app.destroy()))
    app.mainloop()