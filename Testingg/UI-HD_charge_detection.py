# kiosk_ui_full_v2.py
# Full UI prototype for: Solar-Powered Smart Vending and Charging Station (UI-only)
# - Manual UID textbox (no RFID hardware)
# - Register or Skip flow for new UID
# - Existing non-member goes to Main with Register-as-Member option
# - Coin slot simulation inside service screens (charging & water)
# - Slot selection (1-5), charging session simulation, water cup simulation

import tkinter as tk
from tkinter import messagebox
import firebase_admin
from firebase_admin import credentials, db
import time
import json
import os
import re
from firebase_helpers import append_audit_log, deduct_charge_balance_transactionally
# hardware integration
from hardware_gpio import HardwareGPIO
import threading

# load pinmap for hardware_gpio
BASE = os.path.dirname(__file__)
PINMAP_PATH = os.path.join(BASE, 'pinmap.json')
try:
    with open(PINMAP_PATH, 'r', encoding='utf-8') as _f:
        _pinmap = json.load(_f)
except Exception:
    _pinmap = None

# ---------------- Configuration ----------------
DATABASE_URL = "https://kiosk-testing-22bf4-default-rtdb.firebaseio.com/"  # <-- CHANGE to your DB URL
SERVICE_KEY = "firebase-key.json"                  # placed in same folder

# Timing / conversion constants (tweak as needed)
WATER_SECONDS_PER_LITER = 10       # example: 10 seconds -> 1 liter (for display only)
WATER_DB_WRITE_INTERVAL = 2        # write water balance every 2 seconds
CHARGE_DB_WRITE_INTERVAL = 10      # write charge balance every 10 seconds
UNPLUG_GRACE_SECONDS = 60          # 1 minute grace after unplug before termination
NO_CUP_TIMEOUT = 10                # 10s no-cup => terminate water session

# Charging detection tuning (simplified and more reliable)
PLUG_THRESHOLD = 0.15  # Increased slightly for better detection
UNPLUG_THRESHOLD = 0.10  # Lower than plug threshold for hysteresis
CONFIRM_SAMPLES = 3  # Require 3 consecutive samples for state change
SAMPLE_INTERVAL = 0.5  # Sample every 500ms

# Coin to seconds mapping (charging)
COIN_MAP = {1: 60, 5: 300, 10: 600}  # 1 peso = 60s, 5 -> 300s, 10 -> 600s

# Coin to ml mapping (water). Keep this separate from COIN_MAP which is for charging.
# Per user request: 1P = 50 mL, 5P = 250 mL, 10P = 500 mL
WATER_COIN_MAP = {1: 50, 5: 250, 10: 500}

# Default starting balances for newly registered members (seconds)
DEFAULT_WATER_BAL = 600   # 10 min
DEFAULT_CHARGE_BAL = 1200 # 20 min

# ------------------------------------------------


class SessionManager:
    """Manage charging sessions per-slot so each slot has its own timer/monitor.
    Sessions are keyed by slot name (e.g. 'slot1'). This runs per-slot after() jobs
    on the Tk root (controller) so multiple sessions can run concurrently.
    """
    def __init__(self, controller):
        self.controller = controller
        self.sessions = {}  # slot -> session dict

    def start_session(self, uid, slot):
        # avoid duplicate
        if slot in self.sessions:
            print(f"WARN: session already running for {slot}")
            return
        hw = getattr(self.controller, 'hw', None)
        # read initial remaining from DB
        user = read_user(uid) or {}
        remaining = user.get('charge_balance', 0) or 0
        sess = {
            'uid': uid,
            'slot': slot,
            'remaining': remaining,
            'is_charging': False,
            'tick_job': None,
            'poll_job': None,
            'monitor_job': None,
            'plug_hits': [],
            'unplug_time': None,
        }
        self.sessions[slot] = sess
        # mark pending in DB
        try:
            write_user(uid, {'charging_status': 'pending'})
            users_ref.child(uid).child('slot_status').update({slot: 'active'})
            write_slot(slot, {'status': 'active', 'current_user': uid})
        except Exception:
            pass
        # power on
        try:
            if hw is not None:
                hw.relay_on(slot)
        except Exception:
            pass
        # begin poll for device draw (if hw present) otherwise start tick immediately
        if hw is None:
            self._begin_charging(sess)
        else:
            # schedule polling every 500ms
            self._schedule_poll_start(slot, delay=500)

    def _schedule_poll_start(self, slot, delay=500):
        sess = self.sessions.get(slot)
        if not sess:
            return
        try:
            # use controller.after
            sess['poll_job'] = self.controller.after(delay, lambda: self._poll_for_start(slot))
        except Exception:
            sess['poll_job'] = None

    def _poll_for_start(self, slot):
        sess = self.sessions.get(slot)
        if not sess:
            return
        hw = getattr(self.controller, 'hw', None)
        if not hw:
            self._begin_charging(sess)
            return
        try:
            cur = hw.read_current(slot)
            amps = float(cur.get('amps', 0) or 0)
        except Exception:
            amps = 0.0
        now = time.time()
        if amps >= PLUG_THRESHOLD:
            sess['plug_hits'].append(now)
            # prune
            sess['plug_hits'] = [t for t in sess['plug_hits'] if (now - t) <= PLUG_CONFIRM_WINDOW]
        else:
            sess['plug_hits'] = [t for t in sess['plug_hits'] if (now - t) <= PLUG_CONFIRM_WINDOW]
        if len(sess['plug_hits']) >= PLUG_CONFIRM_COUNT:
            # device detected -> begin charging
            try:
                write_user(sess['uid'], {'charging_status': 'charging'})
            except Exception:
                pass
            try:
                append_audit_log(actor=sess['uid'], action='charging_detected', meta={'slot': slot})
            except Exception:
                pass
            self._begin_charging(sess)
            return
        # reschedule
        self._schedule_poll_start(slot, delay=500)

    def _begin_charging(self, sess):
        slot = sess['slot']
        uid = sess['uid']
        sess['is_charging'] = True
        sess['plug_hits'] = []
        sess['unplug_time'] = None
        # schedule tick loop
        self._schedule_tick(slot)
        # schedule unplug monitor
        try:
            sess['monitor_job'] = self.controller.after(500, lambda: self._monitor_unplug(slot))
        except Exception:
            sess['monitor_job'] = None

    def _schedule_tick(self, slot, delay=1000):
        sess = self.sessions.get(slot)
        if not sess:
            return
        try:
            sess['tick_job'] = self.controller.after(delay, lambda: self._tick(slot))
        except Exception:
            sess['tick_job'] = None

    def _tick(self, slot):
        sess = self.sessions.get(slot)
        if not sess:
            return
        if not sess.get('is_charging'):
            # do not decrement while paused
            self._schedule_tick(slot)
            return
        sess['remaining'] = max(0, sess['remaining'] - 1)
        # persist to DB periodically
        try:
            users_ref.child(sess['uid']).update({'charge_balance': sess['remaining']})
        except Exception:
            pass
        # update any visible UI
        try:
            self.controller.refresh_all_user_info()
        except Exception:
            pass
        if sess['remaining'] <= 0:
            self.end_session(slot, reason='time_up')
            return
        # schedule next tick
        self._schedule_tick(slot, delay=1000)

    def _monitor_unplug(self, slot):
        sess = self.sessions.get(slot)
        if not sess:
            return
        hw = getattr(self.controller, 'hw', None)
        if not hw:
            # no hardware to monitor
            return
        try:
            cur = hw.read_current(slot)
            amps = float(cur.get('amps', 0) or 0)
        except Exception:
            amps = 0.0
        now = time.time()
        if amps >= PLUG_THRESHOLD:
            # device drawing current -> clear idle timer and ensure charging continues
            sess['unplug_time'] = None
            if not sess['is_charging']:
                sess['is_charging'] = True
                # restart tick loop
                self._schedule_tick(slot)
                try:
                    write_user(sess['uid'], {'charging_status': 'charging'})
                except Exception:
                    pass
        else:
            if not sess['unplug_time']:
                sess['unplug_time'] = now
                sess['is_charging'] = False
            else:
                if (now - sess['unplug_time']) >= UNPLUG_GRACE_SECONDS:
                    self.end_session(slot, reason='unplug_timeout')
                    return
        # reschedule monitor
        try:
            sess['monitor_job'] = self.controller.after(500, lambda: self._monitor_unplug(slot))
        except Exception:
            sess['monitor_job'] = None

    def end_session(self, slot, reason='manual'):
        sess = self.sessions.get(slot)
        if not sess:
            return
        uid = sess['uid']
        # cancel jobs
        try:
            if sess.get('tick_job') is not None:
                self.controller.after_cancel(sess['tick_job'])
        except Exception:
            pass
        try:
            if sess.get('poll_job') is not None:
                self.controller.after_cancel(sess['poll_job'])
        except Exception:
            pass
        try:
            if sess.get('monitor_job') is not None:
                self.controller.after_cancel(sess['monitor_job'])
        except Exception:
            pass
        # turn off hardware and unlock
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
        # update DB
        try:
            write_user(uid, {'charging_status': 'idle', 'occupied_slot': 'none', 'charge_balance': 0})
            write_slot(slot, {'status': 'inactive', 'current_user': 'none'})
            users_ref.child(uid).child('slot_status').update({slot: 'inactive'})
        except Exception:
            pass
        try:
            append_audit_log(actor=uid, action='charging_finished', meta={'slot': slot, 'reason': reason})
        except Exception:
            pass
        # cleanup
        try:
            del self.sessions[slot]
        except Exception:
            pass
        # refresh UI
        try:
            self.controller.refresh_all_user_info()
        except Exception:
            pass


# Initialize Firebase Admin
cred = credentials.Certificate(SERVICE_KEY)
firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})
users_ref = db.reference("users")
slots_ref = db.reference("slots")

# Ensure slots node exists (slot1..slot4)
for i in range(1, 5):
    slot_key = f"slot{i}"
    if slots_ref.child(slot_key).get() is None:
        slots_ref.child(slot_key).set({"status": "inactive", "current_user": "none"})

# ----------------- Helper Functions -----------------
def user_exists(uid):
    return users_ref.child(uid).get() is not None

def create_nonmember(uid):
    """Create a minimal user node as non-member (Guest)."""
    users_ref.child(uid).set({
        "type": "nonmember",
        "name": "Guest",
        "student_id": "",
        "water_balance": None,    # members only
        "charge_balance": 0,
        "occupied_slot": "none",
        "charging_status": "idle",
        "slot_status": {}
    })

def read_user(uid):
    return users_ref.child(uid).get()

def write_user(uid, data: dict):
    users_ref.child(uid).update(data)

def read_slot(slot):
    return slots_ref.child(slot).get()

def write_slot(slot, data: dict):
    slots_ref.child(slot).update(data)

def seconds_to_min_display(sec):
    if sec is None:
        return "N/A"
    return f"{sec//60}m {sec%60}s"

def water_seconds_to_liters(sec):
    if sec is None:
        return "N/A"
    # Historically this function converted 'seconds' -> liters. The app now stores
    # water balances as milliliters (mL). Interpret the input as mL and convert
    # to liters for display.
    try:
        ml = float(sec or 0)
        liters = ml / 1000.0
        return f"{liters:.2f} L"
    except Exception:
        return "N/A"

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
        
        # Initialize ArduinoListener for water service hardware integration
        try:
            # prefer the new ArduinoListener.py implementation (capitalized filename)
            try:
                from ArduinoListener import ArduinoListener
            except Exception:
                # fallback to older module name if present
                from arduino_listener import ArduinoListener

            # create central listener and dispatch events to screens
            self.arduino_listener = ArduinoListener(event_callback=self._arduino_event_callback)
            try:
                self.arduino_listener.start()
                print("INFO: ArduinoListener started (centralized event callback)")
            except Exception:
                print("WARN: ArduinoListener failed to start")
        except ImportError:
            print("WARN: ArduinoListener module not found; water service will use simulation buttons only")
            self.arduino_listener = None
        except Exception as e:
            print(f"WARN: Failed to initialize ArduinoListener: {e}")
            self.arduino_listener = None
        
        # NOW that ArduinoListener is created, register WaterScreen callbacks
        try:
            # WaterScreen will receive events via the central dispatcher; no per-screen registration required
            pass
        except Exception as e:
            print(f"WARN: Failed to register WaterScreen callbacks: {e}")
        
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
        """Display a simple coin popup using only Arduino-provided values.
        This is the canonical popup used by hardware-driven flows. It will also
        persist added_ml (delta) into the user's water balance.
        """
        def _do():
            parts = []
            if peso is not None:
                parts.append(f"Inserted: \u20B1{peso}")
            if added_ml is not None:
                parts.append(f"Added: {added_ml} mL")
            if total_ml is not None:
                parts.append(f"Total: {total_ml} mL")
            msg = "\n".join(parts) if parts else "Coin event"
            # persist to DB if added_ml provided
            try:
                if uid and added_ml is not None and added_ml > 0:
                    user = read_user(uid) or {}
                    if user.get('type') == 'member':
                        cur = user.get('water_balance') or 0
                        write_user(uid, {'water_balance': cur + added_ml})
                    else:
                        cur = user.get('temp_water_time') or 0
                        write_user(uid, {'temp_water_time': cur + added_ml})
                    try:
                        # refresh UI to show updated balances
                        self.refresh_all_user_info()
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                messagebox.showinfo("Coin Inserted", msg)
            except Exception:
                print("POPUP:", msg)

        try:
            self.after(0, _do)
        except Exception:
            _do()

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
        """Central dispatcher for ArduinoListener events.
        For now we forward events to the WaterScreen instance if present.
        The external ArduinoListener calls this with (event, value).
        """
        try:
            ws = self.frames.get(WaterScreen)
            if ws and hasattr(ws, 'handle_arduino_event'):
                try:
                    ws.handle_arduino_event(event, value)
                except Exception:
                    pass
        except Exception:
            pass

    def show_frame(self, cls):
        # record current frame name for context (used by coin popups)
        try:
            self.current_frame = cls.__name__
        except Exception:
            self.current_frame = None
        frame = self.frames[cls]
        if hasattr(frame, "refresh"):
            frame.refresh()
        # If switching to hardware-backed screens, notify Arduino to switch mode
        try:
            al = getattr(self, 'arduino_listener', None)
            if al is not None:
                # Water screen -> MODE WATER, Charging flow -> MODE CHARGE
                if cls.__name__ == 'WaterScreen':
                    try:
                        al.send_command('MODE WATER')
                        print('INFO: Sent MODE WATER to Arduino')
                    except Exception:
                        pass
                elif cls.__name__ in ('SlotSelectScreen', 'ChargingScreen'):
                    try:
                        al.send_command('MODE CHARGE')
                        print('INFO: Sent MODE CHARGE to Arduino')
                    except Exception:
                        pass
        except Exception:
            pass
        frame.tkraise()
    
    def cleanup(self):
        """Gracefully shutdown resources on app exit."""
        try:
            if hasattr(self, 'arduino_listener') and self.arduino_listener is not None:
                self.arduino_listener.stop()
                print("INFO: ArduinoListener stopped.")
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
        self.uid_entry.delete(0, tk.END)
        self.info.config(text="")
        # ensure the entry has focus so a scanner's automatic Enter is received
        try:
            self.uid_entry.focus_set()
        except Exception:
            pass
        # ensure the entry has focus so a scanner's automatic Enter is received
        try:
            self.uid_entry.focus_set()
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
        self.controller