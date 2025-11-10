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

# Charging detection tuning (updated - use averaged absolute-sample rule)
# New approach: use a small rolling window of recent amps readings and require
# both the window average and a minimum count of readings above a positive
# threshold to confirm charging. This avoids single-sample spikes from
# prematurely starting the timer.
CHARGE_SAMPLE_WINDOW = 5          # how many recent samples to keep
CHARGE_AVG_THRESHOLD = 0.20       # average amps threshold (avg of window) to consider charging
CHARGE_COUNT_THRESHOLD = 3        # minimum number of samples in the window that must be >= AVG_THRESHOLD
CHARGE_CONSECUTIVE_REQUIRED = 3   # fallback: require this many consecutive samples (kept for compatibility)
# New stricter detection policy (user request):
# - plug detected when >= PLUG_THRESHOLD for PLUG_CONFIRM_COUNT samples within PLUG_CONFIRM_WINDOW seconds
# - unplug detected when < UNPLUG_THRESHOLD for UNPLUG_CONFIRM_COUNT samples within UNPLUG_CONFIRM_WINDOW seconds
PLUG_THRESHOLD = 0.22
PLUG_CONFIRM_COUNT = 4
PLUG_CONFIRM_WINDOW = 2.0
UNPLUG_THRESHOLD = 0.14
UNPLUG_CONFIRM_COUNT = 4
UNPLUG_CONFIRM_WINDOW = 2.0

# Coin to seconds mapping (charging)
COIN_MAP = {1: 60, 5: 300, 10: 600}  # 1 peso = 60s, 5 -> 300s, 10 -> 600s

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
    liters = sec / WATER_SECONDS_PER_LITER
    return f"{liters:.1f} L"

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
        # track recent coin inserts for display
        try:
            rec = self.coin_counters.get(uid, {'coins': 0, 'seconds': 0})
            rec['coins'] += 1
            rec['seconds'] += seconds
            self.coin_counters[uid] = rec
        except Exception:
            pass
        self.show_frame(ScanScreen)

    def show_frame(self, cls):
        frame = self.frames[cls]
        if hasattr(frame, "refresh"):
            frame.refresh()
        frame.tkraise()

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
        except Exception as e:
            print('Error submitting registration request:', e)
            print('ERROR: Failed to submit registration request. Please try again later.')

    def logout(self):
        # Only clear the active UID; keep any assigned/active slot so charging sessions
        # continue running even if the user logs out at the kiosk UI.
        self.controller.active_uid = None
        # do not clear controller.active_slot here; background charging should continue
        self.controller.show_frame(ScanScreen)

    def refresh(self):
        self.user_info.refresh()
        # show register button only if user exists and is non-member
        uid = self.controller.active_uid
        if uid:
            user = read_user(uid)
            # check registration request status to update button text/state
            try:
                req = db.reference(f"registration_requests/{uid}").get()
            except Exception:
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
            if slot is None:
                text = f"Slot {i}\nFree"
                color = "#2ecc71"
            else:
                status = slot.get("status", "inactive")
                cur = slot.get("current_user", "none")
                uid = self.controller.active_uid
                # If slot is assigned to someone
                if cur != "none":
                    if cur == uid:
                        # The logged-in user owns this slot -> show as in use (no special highlight)
                        text = f"Slot {i}\nIn Use"
                        # use neutral color (same as disabled/neutral) instead of yellow
                        color = "#95a5a6"
                    else:
                        # Other users see it as occupied (red)
                        text = f"Slot {i}\nOccupied"
                        color = "#e74c3c"
                else:
                    # no current_user assigned; reflect active status as red In Use
                    if status == "active":
                        text = f"Slot {i}\nIn Use"
                        color = "#e74c3c"
                    else:
                        text = f"Slot {i}\nFree"
                        color = "#2ecc71"
            self.slot_buttons[key].config(text=text, bg=color)
        # show coin status for current user if any
        try:
            uid = self.controller.active_uid
            if uid:
                rec = self.controller.coin_counters.get(uid)
                if rec:
                    self.coin_status_lbl.config(text=f"Coins inserted: {rec.get('coins',0)} (≈ {rec.get('seconds',0)}s)")
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
        # helper so coin slot appears before selecting slot
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
        print(f"INFO: ₱{amount} added => {add} seconds to charging balance.")
        # record coin insert for UI/summary
        try:
            self.controller.record_coin_insert(uid, amount, add)
        except Exception:
            pass
        try:
            self.controller.refresh_all_user_info()
        except Exception:
            pass
        try:
            rec = self.controller.coin_counters.get(uid)
            if rec:
                self.coin_status_lbl.config(text=f"Coins inserted: {rec.get('coins',0)} (≈ {rec.get('seconds',0)}s)")
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
        # UI update job id for periodically refreshing the displayed remaining time
        self._ui_update_job = None

    def _start_ui_update_loop(self):
        """Periodically update the displayed remaining time from SessionManager (if present).
        This keeps the UI responsive while the authoritative session ticks run in SessionManager.
        """
        try:
            # cancel any previous job
            if self._ui_update_job is not None:
                try:
                    self.after_cancel(self._ui_update_job)
                except Exception:
                    pass
                self._ui_update_job = None
            # immediate update then schedule next
            self._ui_update_once()
        except Exception:
            self._ui_update_job = None

    def _stop_ui_update_loop(self):
        try:
            if self._ui_update_job is not None:
                try:
                    self.after_cancel(self._ui_update_job)
                except Exception:
                    pass
                self._ui_update_job = None
        except Exception:
            pass

    def _ui_update_once(self):
        """Single UI update tick: read remaining seconds from SessionManager if available
        or from user DB if not. Schedule next update in ~1s.
        """
        try:
            slot = self.charging_slot or getattr(self.controller, 'active_slot', None)
            sm = getattr(self.controller, 'session_manager', None)
            if sm and slot and slot in sm.sessions:
                try:
                    rem = sm.sessions[slot].get('remaining', 0)
                    self.time_var.set(str(rem))
                    if getattr(self, 'tm', None) is not None:
                        try:
                            self.tm.show_time(rem)
                        except Exception:
                            pass
                except Exception:
                    pass
            else:
                # fallback: read DB for active uid
                uid = getattr(self.controller, 'active_uid', None)
                if uid:
                    try:
                        user = read_user(uid) or {}
                        self.time_var.set(str(user.get('charge_balance', 0) or 0))
                    except Exception:
                        pass
                else:
                    try:
                        self.time_var.set('0')
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            # schedule next UI update
            self._ui_update_job = self.after(1000, self._ui_update_once)
        except Exception:
            self._ui_update_job = None

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
        
        print(f"[REFRESH] ChargingScreen.refresh() called. active_uid={uid}, charging_uid={self.charging_uid}, is_charging={self.is_charging}, remaining={self.remaining}")
        
        # CRITICAL: If the active user has changed (different from the session owner),
        # IMMEDIATELY INVALIDATE THE SESSION to stop all background timers
        if uid and self.charging_uid and uid != self.charging_uid:
            print(f"[CHARGING] User changed: was {self.charging_uid}, now {uid}. Invalidating session and resetting ChargingScreen state.")
            # Mark session as invalid so any running callbacks will exit immediately
            self._session_valid = False
            # Cancel all running timers
            try:
                if self._tick_job is not None:
                    print(f"[CHARGING] Cancelling _tick_job: {self._tick_job}")
                    self.after_cancel(self._tick_job)
                    self._tick_job = None
            except Exception as e:
                print(f"[CHARGING] Error cancelling _tick_job: {e}")
                pass
            try:
                if self._wait_job is not None:
                    print(f"[CHARGING] Cancelling _wait_job: {self._wait_job}")
                    self.after_cancel(self._wait_job)
                    self._wait_job = None
            except Exception as e:
                print(f"[CHARGING] Error cancelling _wait_job: {e}")
                pass
            try:
                if self._hw_monitor_job is not None:
                    print(f"[CHARGING] Cancelling _hw_monitor_job: {self._hw_monitor_job}")
                    self.after_cancel(self._hw_monitor_job)
                    self._hw_monitor_job = None
            except Exception as e:
                print(f"[CHARGING] Error cancelling _hw_monitor_job: {e}")
                pass
            try:
                if self._poll_timeout_job is not None:
                    print(f"[CHARGING] Cancelling _poll_timeout_job: {self._poll_timeout_job}")
                    self.after_cancel(self._poll_timeout_job)
                    self._poll_timeout_job = None
            except Exception as e:
                print(f"[CHARGING] Error cancelling _poll_timeout_job: {e}")
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
                print(f"[CHARGING] Setting time_var to '0'")
                self.time_var.set("0")
            except Exception as e:
                print(f"[CHARGING] Error setting time_var: {e}")
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
        if uid:
            user = read_user(uid)
            cb = user.get("charge_balance", 0) or 0
            self.time_var.set(str(cb))
            # If a SessionManager session exists for the active slot, bind to it and
            # start the UI update loop so the view reflects the authoritative session state.
            try:
                sm = getattr(self.controller, 'session_manager', None)
                if sm and slot and slot in sm.sessions:
                    self.charging_slot = slot
                    self.charging_uid = sm.sessions[slot].get('uid')
                    self._session_valid = True
                    if self._ui_update_job is None:
                        self._start_ui_update_loop()
            except Exception:
                pass
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
            users_ref.child(uid).child("slot_status").update({slot: "active"})
            write_slot(slot, {"status": "active", "current_user": uid})

        # Delegate session lifecycle to SessionManager when available.
        # This centralizes per-slot timers so multiple sessions can run concurrently.
        sm = getattr(self.controller, 'session_manager', None)
        if sm:
            try:
                # cancel any local timers that might conflict
                try:
                    if self._tick_job is not None:
                        self.after_cancel(self._tick_job)
                except Exception:
                    pass
                try:
                    if self._wait_job is not None:
                        self.after_cancel(self._wait_job)
                except Exception:
                    pass
                try:
                    if self._hw_monitor_job is not None:
                        self.after_cancel(self._hw_monitor_job)
                except Exception:
                    pass
                try:
                    if self._poll_timeout_job is not None:
                        self.after_cancel(self._poll_timeout_job)
                except Exception:
                    pass
            except Exception:
                pass
            # start authoritative session manager which will handle ticks/monitoring
            try:
                sm.start_session(uid, slot)
            except Exception:
                pass
            # update view binding so UI shows the session for this slot
            self.charging_uid = uid
            self.charging_slot = slot
            self._session_valid = True
            # begin UI update loop that reads remaining from SessionManager
            try:
                if self._ui_update_job is None:
                    self._start_ui_update_loop()
            except Exception:
                pass
            return

        # Fallback: prepare local state but if hardware available for slot1, enable power and wait for current
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

    def _charging_tick(self):
        # clear current job marker since we're running now
        self._tick_job = None
        # CRITICAL: Capture the session ID at the START of this callback
        # Old queued callbacks will have stale session IDs and will skip display updates
        my_session_id = getattr(self, '_current_session_id', None)
        # SAFETY: If session has been invalidated (user changed), exit immediately
        if not getattr(self, '_session_valid', False):
            print(f"[TICK] Exiting: _session_valid=False (my_session_id={my_session_id}, current={self._current_session_id})")
            return
        if not self.is_charging:
            print(f"[TICK] Exiting: not charging")
            return
        uid = self._get_session_uid()
        if not uid:
            print(f"[TICK] Exiting: no uid")
            return
        print(f"[TICK] Running: uid={uid}, remaining={self.remaining}, my_session_id={my_session_id}, current={self._current_session_id}")
        # operate on local remaining for responsiveness; write back to DB periodically
        t = self.remaining
        if t <= 0:
            # ensure DB shows finished state and cancel any scheduled tick
            if self._tick_job is not None:
                try:
                    self.after_cancel(self._tick_job)
                except Exception:
                    pass
                self._tick_job = None
            # ensure DB shows finished state and clear remaining balance/assignment
            try:
                write_user(uid, {"charging_status": "idle", "charge_balance": 0, "occupied_slot": "none"})
            except Exception:
                pass
            # operate on the slot bound at start_charging; fall back to controller.active_slot
            slot = self.charging_slot or self.controller.active_slot
            # turn off hardware power and unlock slot where applicable
            try:
                hw = getattr(self.controller, 'hw', None)
                if hw is not None and slot:
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
            if slot:
                try:
                    write_slot(slot, {"status": "inactive", "current_user": "none"})
                    users_ref.child(uid).child("slot_status").update({slot: "inactive"})
                except Exception:
                    pass
            try:
                append_audit_log(actor=uid, action='charging_finished', meta={'slot': slot})
            except Exception:
                pass
            print("INFO: Charging time finished; session ended.")
            self.is_charging = False
            try:
                self.charging_uid = None
            except Exception:
                pass
            # clear active slot and return to main screen
            try:
                if self.controller.active_slot == slot:
                    self.controller.active_slot = None
            except Exception:
                # fallback: clear anyway
                self.controller.active_slot = None
            self.controller.show_frame(MainScreen)
            return
        # CRITICAL: Only update display if this callback belongs to the CURRENT session
        # Old queued callbacks will have stale session IDs and will exit here silently
        if my_session_id != self._current_session_id:
            return
        # decrement local remaining and update display
        self.remaining = max(0, t - 1)
        self.time_var.set(str(self.remaining))
        # update TM1637 if present
        try:
            if getattr(self, 'tm', None) is not None:
                try:
                    self.tm.show_time(self.remaining)
                except Exception:
                    pass
        except Exception:
            pass
        self.db_acc += 1
        # periodic DB write
        if self.db_acc >= CHARGE_DB_WRITE_INTERVAL:
            try:
                # deduct the accumulated seconds via transaction to avoid races
                # calculate how many seconds were consumed since last write
                # here we simply set the DB to remaining using transaction-guarded approach
                # compute delta = previous_db_balance - remaining
                # using transaction helper which reduces balance by delta
                prev = read_user(uid).get('charge_balance', 0) or 0
                delta = max(0, prev - self.remaining)
                if delta > 0:
                    deduct_charge_balance_transactionally(users_ref, uid, delta)
                else:
                    # edge: DB ahead; set to remaining
                    users_ref.child(uid).update({'charge_balance': self.remaining})
            except Exception:
                # fallback
                users_ref.child(uid).update({"charge_balance": self.remaining})
            self.db_acc = 0
        # schedule next tick and remember job id so we don't double-schedule
        try:
            self._tick_job = self.after(1000, self._charging_tick)
        except Exception:
            self._tick_job = None

    def _poll_for_charging_start(self):
        """Poll current sensor on the active slot until device draws current, then start countdown."""
        self._wait_job = None
        # Capture session ID at start; exit silently if stale
        my_session_id = getattr(self, '_current_session_id', None)
        # SAFETY: If session has been invalidated (user changed), exit immediately
        if not getattr(self, '_session_valid', False):
            return
        # Only proceed if this callback belongs to current session
        if my_session_id != self._current_session_id:
            return
        # ensure we use the slot captured at start_charging
        slot = self.charging_slot or self.controller.active_slot
        hw = getattr(self.controller, 'hw', None)
        uid = self._get_session_uid()
        if not slot or not hw:
            return
        if not uid:
            try:
                s = read_slot(slot)
                uid = s.get('current_user') if s else None
            except Exception:
                uid = None
        if not uid:
            return
        try:
            cur = hw.read_current(slot)
            amps = cur.get('amps', 0)
        except Exception:
            amps = 0

        # Debug print similar to test_slot1: show IDLE read line and compact poll info
        try:
            cur_raw = cur.get('raw') if isinstance(cur, dict) else None
            volts = cur.get('volts') if isinstance(cur, dict) else None
            print(f"IDLE read: raw={cur_raw} volts={(volts or 0):.3f} V amps={amps:.2f} A")
            print(f"[CHG POLL] t={time.time():.1f} slot={slot} amps={amps:.3f} plug_hits={len(self._plug_hits)} unplug_hits={len(self._unplug_hits)}")
        except Exception:
            pass

        now = time.time()
        try:
            sample = float(amps or 0.0)
        except Exception:
            sample = 0.0

        # record plug-hits when sample >= PLUG_THRESHOLD
        try:
            if sample >= PLUG_THRESHOLD:
                self._plug_hits.append(now)
                # keep only recent hits within PLUG_CONFIRM_WINDOW
                self._plug_hits = [t for t in self._plug_hits if (now - t) <= PLUG_CONFIRM_WINDOW]
            else:
                # optionally prune old entries even when below threshold
                self._plug_hits = [t for t in self._plug_hits if (now - t) <= PLUG_CONFIRM_WINDOW]

            # if enough plug hits within window, declare charging
            if len(self._plug_hits) >= PLUG_CONFIRM_COUNT:
                try:
                    write_user(uid, {"charging_status": "charging"})
                except Exception:
                    pass
                try:
                    append_audit_log(actor=uid, action='charging_detected', meta={'slot': slot, 'amps': amps})
                except Exception:
                    pass
                # Debug: notify terminal that charging was detected
                try:
                    print(f"[CHG EVENT] charging_detected slot={slot} amps={amps:.3f} plug_hits={len(self._plug_hits)}")
                except Exception:
                    pass

                # start charging state
                self.is_charging = True
                try:
                    user = read_user(uid)
                    self.remaining = user.get('charge_balance', self.remaining) or self.remaining
                except Exception:
                    pass
                try:
                    self.slot_lbl.config(text=f"{slot} - CHARGING")
                except Exception:
                    pass
                # start tick loop and a non-blocking monitor that implements the 1-minute idle behavior
                if self._tick_job is None:
                    self._charging_tick()
                if self._hw_monitor_job is None:
                    # sample unplug monitor at 500ms to allow detection and 1-minute idle handling
                    self._hw_monitor_job = self.after(500, self._hardware_unplug_monitor)
                # cancel poll timeout since device detected
                try:
                    if self._poll_timeout_job is not None:
                        try:
                            self.after_cancel(self._poll_timeout_job)
                        except Exception:
                            pass
                        self._poll_timeout_job = None
                except Exception:
                    pass
                return

            # not enough evidence yet; schedule next sample at 500ms
            try:
                self._wait_job = self.after(500, self._poll_for_charging_start)
            except Exception:
                self._wait_job = None
            return
        except Exception:
            # on error, reset plug_hits and continue polling
            try:
                self._plug_hits = []
            except Exception:
                pass
            try:
                self._wait_job = self.after(500, self._poll_for_charging_start)
            except Exception:
                self._wait_job = None
            return

    def _hardware_unplug_monitor(self):
        """Monitor the ACS712 reading; if current falls below threshold for UNPLUG_GRACE_SECONDS, stop the session."""
        self._hw_monitor_job = None
        # Capture session ID at start; exit silently if stale
        my_session_id = getattr(self, '_current_session_id', None)
        # SAFETY: If session has been invalidated (user changed), exit immediately
        if not getattr(self, '_session_valid', False):
            return
        # Only proceed if this callback belongs to current session
        if my_session_id != self._current_session_id:
            return
        # ensure we monitor the bound slot for this charging session
        slot = self.charging_slot or self.controller.active_slot
        hw = getattr(self.controller, 'hw', None)
        uid = self._get_session_uid()
        if not slot or not hw:
            return
        if not uid:
            try:
                s = read_slot(slot)
                uid = s.get('current_user') if s else None
            except Exception:
                uid = None
        if not uid:
            return
        try:
            cur = hw.read_current(slot)
            amps = cur.get('amps', 0)
        except Exception:
            amps = 0

        # Debug print: show live monitor reading
        try:
            print(f"[CHG MON] t={time.time():.1f} slot={slot} amps={amps:.3f} unplug_hits={len(self._unplug_hits)}")
        except Exception:
            pass

        now = time.time()
        try:
            # If we're seeing current above the plug threshold, ensure charging continues
            if amps >= PLUG_THRESHOLD:
                # device drawing current -> clear idle timer and resume countdown if paused
                self.unplug_time = None
                # ensure tick loop is running
                if not self.is_charging:
                    self.is_charging = True
                    try:
                        # restart tick loop if not scheduled
                        if self._tick_job is None:
                            self._charging_tick()
                    except Exception:
                        pass
                    try:
                        write_user(uid, {"charging_status": "charging"})
                    except Exception:
                        pass
                    try:
                        append_audit_log(actor=uid, action='charging_resumed', meta={'slot': slot, 'amps': amps})
                    except Exception:
                        pass
            else:
                # below threshold: start or continue idle timer
                if not self.unplug_time:
                    # first low reading: start the idle countdown (1 minute)
                    self.unplug_time = now
                    # pause tick loop so remaining doesn't decrease while unplugged
                    self.is_charging = False
                    if self._tick_job is not None:
                        try:
                            self.after_cancel(self._tick_job)
                        except Exception:
                            pass
                        self._tick_job = None
                    try:
                        print(f"[CHG MON] no current detected, starting idle timer for {UNPLUG_GRACE_SECONDS}s")
                    except Exception:
                        pass
                else:
                    # check if idle timer expired
                    if (now - self.unplug_time) >= UNPLUG_GRACE_SECONDS:
                        try:
                            print(f"[CHG EVENT] idle timeout expired, stopping session slot={slot} amps={amps:.3f}")
                        except Exception:
                            pass
                        self.stop_session()
                        return
        except Exception:
            # on error, reset and continue
            try:
                self.unplug_time = None
            except Exception:
                pass

        # reschedule monitor at 500ms to match detection window
        try:
            self._hw_monitor_job = self.after(500, self._hardware_unplug_monitor)
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
        users_ref.child(uid).child("slot_status").update({slot: "inactive"})
        print(f"INFO: {slot} unlocked. Please unplug your device when ready.")
        # update header info but do not stop charging; unlocking does not equal unplug
        try:
            self.user_info.refresh()
        except Exception:
            pass

    # simulate_unplug removed: hardware path monitors current and handles unplug

    def _check_unplug_grace(self):
        uid = self._get_session_uid()
        if not uid:
            return
        user = read_user(uid)
        if user.get("charging_status") == "charging":
            self.is_charging = True
            self.unplug_time = None
            # restart tick loop if not already running
            if self._tick_job is None:
                self._charging_tick()
            return
        if self.unplug_time and (time.time() - self.unplug_time) >= UNPLUG_GRACE_SECONDS:
            slot = self.charging_slot or self.controller.active_slot
            write_user(uid, {"occupied_slot": "none"})
            if slot:
                users_ref.child(uid).child("slot_status").update({slot: "inactive"})
                write_slot(slot, {"status": "inactive", "current_user": "none"})
            print("INFO: No device detected. Charging session terminated.")
            # clear active slot so SlotSelectScreen will show coin top again (only if it refers to this session)
            try:
                if self.controller.active_slot == slot:
                    self.controller.active_slot = None
            except Exception:
                try:
                    self.controller.active_slot = None
                except Exception:
                    pass
            self.controller.show_frame(MainScreen)
            self.unplug_time = None
            self.is_charging = False
            return
        self.after(1000, self._check_unplug_grace)

    def stop_session(self):
        uid = self._get_session_uid()
        slot = self.charging_slot or self.controller.active_slot
        # If SessionManager is authoritative, ask it to end the session for this slot
        try:
            sm = getattr(self.controller, 'session_manager', None)
            if sm and slot and slot in sm.sessions:
                try:
                    sm.end_session(slot, reason='manual')
                except Exception:
                    pass
        except Exception:
            pass
        # stop UI update loop
        try:
            self._stop_ui_update_loop()
        except Exception:
            pass
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
        tk.Label(body, text="Water Dispensing", font=("Arial", 22, "bold"), fg="white", bg="#2980b9").pack(pady=6)
        self.status_lbl = tk.Label(body, text="Place cup to start", font=("Arial", 16), fg="white", bg="#2980b9")
        self.status_lbl.pack(pady=6)
        self.time_var = tk.StringVar(value="0")
        tk.Label(body, text="Time (sec)", font=("Arial", 14), fg="white", bg="#2980b9").pack()
        tk.Label(body, textvariable=self.time_var, font=("Arial", 28, "bold"), fg="white", bg="#2980b9").pack(pady=6)

        # cup detection controls
        hw = getattr(self.controller, 'hw', None)
        hw_present = hw is not None
        btn_frame = tk.Frame(body, bg="#2980b9")
        btn_frame.pack(pady=8)
        if not hw_present:
            # simulation buttons for desktop/testing when no hardware present
            tk.Button(btn_frame, text="Simulate Place Cup (Start)", font=("Arial", 14, "bold"),
                      bg="#27ae60", fg="white", width=18, command=self.place_cup).grid(row=0, column=0, padx=6)
            tk.Button(btn_frame, text="Simulate Remove Cup", font=("Arial", 14, "bold"),
                      bg="#f39c12", fg="white", width=18, command=self.remove_cup).grid(row=0, column=1, padx=6)
        else:
            # when hardware is present, hide simulation and show helpful hint
            tk.Label(btn_frame, text="Hardware sensors active — use physical inputs", fg="white", bg="#2980b9", font=("Arial", 12)).pack()
        tk.Button(btn_frame, text="Stop Session", font=("Arial", 12, "bold"),
                  bg="#c0392b", fg="white", width=18, command=self.stop_session).grid(row=1, column=0, columnspan=2, pady=8)

        # coin area for non-members (water purchase only) - only ₱1 allowed
        coin_frame = tk.LabelFrame(body, text="Coinslot (simulate) - water (non-members only)", font=("Arial", 12, "bold"),
                                   fg="white", bg="#2980b9", bd=2, labelanchor="n")
        coin_frame.pack(pady=10)
        tk.Button(coin_frame, text="₱1", font=("Arial", 14, "bold"), bg="#f39c12", fg="white", width=8,
                  command=lambda: self.insert_coin_water(1)).grid(row=0, column=0, padx=6, pady=6)

        # state
        self.cup_present = False
        self.last_cup_time = None
        self.water_db_acc = 0
        self.temp_water_time = 0  # for non-member purchased water time
        self._water_job = None
        self._water_nocup_job = None
        self._water_db_acc = 0
        self._water_remaining = 0
        self._water_db_acc = 0
        self._water_remaining = 0

    def refresh(self):
        # refresh user info header too
        try:
            self.user_info.refresh()
        except Exception:
            pass
        uid = self.controller.active_uid
        if not uid:
            self.time_var.set("0")
            self.status_lbl.config(text="Place cup to start")
            return
        user = read_user(uid)
        if user.get("type") == "member":
            wb = user.get("water_balance", 0) or 0
            self.time_var.set(str(wb))
            self.status_lbl.config(text="Place cup to start")
        else:
            # non-member: show temporary purchased water time persisted in DB if available
            # try persisted temp time first, fall back to local temp
            temp = user.get("temp_water_time", None)
            if temp is None:
                temp = self.temp_water_time
            # ensure local cache matches persisted value so place_cup uses correct amount
            self.temp_water_time = temp
            self.time_var.set(str(temp))
            if (temp or 0) <= 0:
                self.status_lbl.config(text="Non-member: buy water with coins")
            else:
                self.status_lbl.config(text="Place cup to start (Purchased time)")

    def insert_coin_water(self, amount):
        uid = self.controller.active_uid
        if not uid:
            print("WARN: No user; scan first.")
            return
        user = read_user(uid)
        add = COIN_MAP.get(amount, 0)
        if user.get("type") == "member":
            # add to member water balance stored in DB
            new = (user.get("water_balance", 0) or 0) + add
            write_user(uid, {"water_balance": new})
            try:
                append_audit_log(actor=uid, action='insert_coin_water', meta={'amount': amount, 'added_seconds': add, 'new_water_balance': new})
            except Exception:
                pass
            print(f"INFO: ₱{amount} added to water balance ({add} sec).")
        else:
            # non-member: add to temp purchase time (persist to DB so it remains across screens)
            # read any existing persisted temp value
            prev = user.get("temp_water_time", 0) or 0
            newtemp = prev + add
            self.temp_water_time = newtemp
            write_user(uid, {"temp_water_time": newtemp})
            try:
                append_audit_log(actor=uid, action='purchase_water', meta={'amount': amount, 'added_seconds': add, 'new_temp': newtemp})
            except Exception:
                pass
            print(f"INFO: ₱{amount} purchased => {add} seconds water (temporary).")
        self.refresh()

    def place_cup(self):
        uid = self.controller.active_uid
        if not uid:
            print("WARN: No user; please scan RFID first.")
            return
        user = read_user(uid)
        if user.get("type") == "member":
            wb = user.get("water_balance", 0) or 0
            if wb <= 0:
                print("WARN: No water balance left. Ask admin.")
                return
            # start local tick using a cached remaining value to avoid blocking DB calls
            self.cup_present = True
            self.last_cup_time = time.time()
            self.status_lbl.config(text="Dispensing...")
            self._water_remaining = wb
            self.time_var.set(str(self._water_remaining))
            self._water_db_acc = 0
            if self._water_job is None:
                self._water_job = self.after(1000, self._water_tick_member)
            # cancel any scheduled no-cup timeout when cup is placed
            if getattr(self, '_water_nocup_job', None) is not None:
                try:
                    self.after_cancel(self._water_nocup_job)
                except Exception:
                    pass
                self._water_nocup_job = None
        else:
            if self.temp_water_time <= 0:
                print("WARN: No purchased time; please buy water with coins first.")
                return
            self.cup_present = True
            self.last_cup_time = time.time()
            self.status_lbl.config(text="Dispensing (Purchased time)...")
            # start local tick for non-member purchased time
            self._water_remaining = self.temp_water_time
            self.time_var.set(str(self._water_remaining))
            if self._water_job is None:
                self._water_job = self.after(1000, self._water_tick_nonmember)
            # cancel any scheduled no-cup timeout when cup is placed
            if getattr(self, '_water_nocup_job', None) is not None:
                try:
                    self.after_cancel(self._water_nocup_job)
                except Exception:
                    pass
                self._water_nocup_job = None

    def _water_tick_member(self):
        # local responsive tick for member water (avoids blocking DB reads)
        if not self.cup_present:
            self.last_cup_time = time.time()
            self._water_job = None
            self._water_no_cup_check()
            return
        uid = self.controller.active_uid
        if not uid:
            self._water_job = None
            return
        if self._water_remaining <= 0:
            self.status_lbl.config(text="No water balance left")
            self.cup_present = False
            self._water_job = None
            self.controller.show_frame(MainScreen)
            return
        # decrement and display
        self._water_remaining -= 1
        self.time_var.set(str(self._water_remaining))
        self._water_db_acc += 1
        if self._water_db_acc >= WATER_DB_WRITE_INTERVAL:
            # write remaining to DB
            users_ref.child(uid).update({"water_balance": self._water_remaining})
            self._water_db_acc = 0
        # schedule next tick
        self._water_job = self.after(1000, self._water_tick_member)

    def _water_tick_nonmember(self):
        # local tick for non-member purchased time
        if not self.cup_present:
            self.last_cup_time = time.time()
            self._water_job = None
            self._water_no_cup_check()
            return
        if self._water_remaining <= 0:
            self.status_lbl.config(text="Purchased time finished")
            self.cup_present = False
            self._water_job = None
            # clear persisted temp time so non-members do not retain credits
            try:
                uid = self.controller.active_uid
                if uid:
                    write_user(uid, {"temp_water_time": 0})
                    try:
                        append_audit_log(actor=uid, action='temp_water_expired', meta={'uid': uid})
                    except Exception:
                        pass
            except Exception:
                pass
            self.temp_water_time = 0
            self.controller.show_frame(MainScreen)
            return
        self._water_remaining -= 1
        self.time_var.set(str(self._water_remaining))
        # also reflect remaining back to temp_water_time
        self.temp_water_time = self._water_remaining
        self._water_job = self.after(1000, self._water_tick_nonmember)

    def remove_cup(self):
        self.cup_present = False
        self.status_lbl.config(text="Cup removed - waiting (10s) to auto-end")
        self.last_cup_time = time.time()
        # cancel active water tick to pause dispensing
        if getattr(self, '_water_job', None) is not None:
            try:
                self.after_cancel(self._water_job)
            except Exception:
                pass
            self._water_job = None
        # schedule the no-cup checker (stored in self._water_nocup_job so it can be canceled)
        # cancel previous no-cup job if any
        if getattr(self, '_water_nocup_job', None) is not None:
            try:
                self.after_cancel(self._water_nocup_job)
            except Exception:
                pass
            self._water_nocup_job = None
        # schedule one-second checks
        try:
            self._water_nocup_job = self.after(1000, self._water_no_cup_check)
        except Exception:
            self._water_nocup_job = None

    def _water_no_cup_check(self):
        if self.cup_present:
            return
        elapsed = time.time() - (self.last_cup_time or time.time())
        if elapsed >= NO_CUP_TIMEOUT:
            # clear job handle
            if getattr(self, '_water_nocup_job', None) is not None:
                try:
                    self.after_cancel(self._water_nocup_job)
                except Exception:
                    pass
                self._water_nocup_job = None
            # if a non-member had purchased temp time, clear it so it doesn't persist
            try:
                uid = self.controller.active_uid
                user = read_user(uid) if uid else None
                if user and user.get('type') != 'member':
                    write_user(uid, {"temp_water_time": 0})
                    try:
                        append_audit_log(actor=uid, action='temp_water_reset_on_timeout', meta={'uid': uid})
                    except Exception:
                        pass
                    self.temp_water_time = 0
            except Exception:
                pass
            print("INFO: No cup detected. Water session ended.")
            self.controller.show_frame(MainScreen)
            return
        # re-schedule the check and store handle
        try:
            if getattr(self, '_water_nocup_job', None) is not None:
                try:
                    self.after_cancel(self._water_nocup_job)
                except Exception:
                    pass
            self._water_nocup_job = self.after(1000, self._water_no_cup_check)
        except Exception:
            self._water_nocup_job = None

    def stop_session(self):
        uid = self.controller.active_uid
        user = read_user(uid)
        if user and user.get("type") == "member":
            try:
                val = int(self.time_var.get())
            except:
                val = user.get("water_balance", 0) or 0
            users_ref.child(uid).update({"water_balance": val})
            try:
                append_audit_log(actor=uid, action='stop_water_session', meta={'water_balance': val})
            except Exception:
                pass
        # reset temp purchase for nonmember
        if user and user.get("type") != "member":
            # persist reset for non-member
            write_user(uid, {"temp_water_time": 0})
            try:
                append_audit_log(actor=uid, action='reset_temp_water', meta={'uid': uid})
            except Exception:
                pass
        self.temp_water_time = 0
        # cancel any pending water tick or no-cup timeout
        if getattr(self, '_water_job', None) is not None:
            try:
                self.after_cancel(self._water_job)
            except Exception:
                pass
            self._water_job = None
        if getattr(self, '_water_nocup_job', None) is not None:
            try:
                self.after_cancel(self._water_nocup_job)
            except Exception:
                pass
            self._water_nocup_job = None
        print("INFO: Water session stopped.")
        self.controller.show_frame(MainScreen)

# ----------------- Run App -----------------
if __name__ == "__main__":
    app = KioskApp()
    app.mainloop()
