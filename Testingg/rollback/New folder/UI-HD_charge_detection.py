# UI-HD_charge_detection.py
# Cleaned & refactored single-file UI for Smart Kiosk
# NOTE: Simulation buttons for Charging and Water screens have been removed.
# Source/Reference: original uploaded UI file. :contentReference[oaicite:2]{index=2}

import tkinter as tk
from tkinter import messagebox
import firebase_admin
from firebase_admin import credentials, db
import time
import json
import os
import glob
import sys

# Try to import optional helpers (safe fallback)
try:
    from firebase_helpers import append_audit_log, deduct_charge_balance_transactionally
    FIREBASE_HELPERS_AVAILABLE = True
except Exception:
    FIREBASE_HELPERS_AVAILABLE = False

try:
    from hardware_gpio import HardwareGPIO
    HARDWARE_GPIO_AVAILABLE = True
except Exception:
    HARDWARE_GPIO_AVAILABLE = False

# Attempt to import ArduinoListener (should exist or be replaced by improved version)
try:
    from ArduinoListener import ArduinoListener
    HAS_ARDUINO_LISTENER = True
except Exception:
    HAS_ARDUINO_LISTENER = False

# ---------------- Config/constants ----------------
DATABASE_URL = "https://kiosk-testing-22bf4-default-rtdb.firebaseio.com/"

# Coin maps
COIN_MAP = {1: 60, 5: 300, 10: 600}        # charging seconds
WATER_COIN_MAP = {1: 50, 5: 250, 10: 500}  # water mL

DEFAULT_WATER_BAL = 600
DEFAULT_CHARGE_BAL = 1200

# Tuning
PLUG_THRESHOLD = 0.10
UNPLUG_THRESHOLD = 0.07
UNPLUG_GRACE_SECONDS = 30

# ---------------- Firebase setup (optional) ----------------
SERVICE_KEY = None
def find_firebase_key():
    possible = [
        "firebase-key.json",
        "serviceAccountKey.json",
        "firebase-adminsdk.json",
        "kiosk-testing-22bf4-firebase-adminsdk-fbsvc-2c5b11e75d.json"
    ]
    for p in possible:
        if os.path.exists(p):
            return p
    for f in os.listdir('.'):
        if f.endswith('.json') and ('firebase' in f.lower() or 'admin' in f.lower()):
            return f
    return None

SERVICE_KEY = find_firebase_key()
FIREBASE_AVAILABLE = False
users_ref = None
slots_ref = None
firebase_app = None

if SERVICE_KEY and os.path.exists(SERVICE_KEY):
    try:
        cred = credentials.Certificate(SERVICE_KEY)
        firebase_app = firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})
        users_ref = db.reference("users")
        slots_ref = db.reference("slots")
        FIREBASE_AVAILABLE = True
        print(f"INFO: Firebase initialized with {SERVICE_KEY}")
    except Exception as e:
        print(f"WARN: Firebase init failed: {e}")
        FIREBASE_AVAILABLE = False

# Provide safe fallback functions when firebase_helpers not available
if not FIREBASE_HELPERS_AVAILABLE:
    def append_audit_log(actor, action, meta=None):
        print(f"AUDIT: {actor} - {action} - {meta}")

# ----------------- Simple DB wrappers (work offline if no Firebase) -----------------
def user_exists(uid):
    if not FIREBASE_AVAILABLE or users_ref is None:
        return False
    try:
        return users_ref.child(uid).get() is not None
    except Exception:
        return False

def read_user(uid):
    if not FIREBASE_AVAILABLE or users_ref is None:
        # offline mock
        return {"type": "nonmember", "name": "Guest", "student_id": "", "water_balance": None, "charge_balance": 0, "occupied_slot": "none", "charging_status": "idle", "slot_status": {}}
    try:
        u = users_ref.child(uid).get()
        if u is None:
            return {"type": "nonmember", "name": "Guest", "student_id": "", "water_balance": None, "charge_balance": 0, "occupied_slot": "none", "charging_status": "idle", "slot_status": {}}
        return u
    except Exception:
        return None

def write_user(uid, data):
    if not FIREBASE_AVAILABLE or users_ref is None:
        print(f"SIMWRITE user {uid}: {data}")
        return
    try:
        users_ref.child(uid).update(data)
    except Exception as e:
        print(f"Error writing user: {e}")

def read_slot(slot):
    if not FIREBASE_AVAILABLE or slots_ref is None:
        return {"status": "inactive", "current_user": "none"}
    try:
        s = slots_ref.child(slot).get()
        if s is None:
            return {"status": "inactive", "current_user": "none"}
        return s
    except Exception:
        return {"status": "inactive", "current_user": "none"}

def write_slot(slot, data):
    if not FIREBASE_AVAILABLE or slots_ref is None:
        print(f"SIMWRITE slot {slot}: {data}")
        return
    try:
        slots_ref.child(slot).update(data)
    except Exception as e:
        print(f"Error writing slot: {e}")

# ----------------- UI Helper Converters -----------------
def seconds_to_min_display(sec):
    if sec is None:
        return "N/A"
    return f"{sec//60}m {sec%60}s"

def ml_to_liters_str(ml):
    try:
        liters = (ml or 0) / 1000.0
        return f"{liters:.2f} L"
    except Exception:
        return "N/A"

# ----------------- Session Manager -----------------
class SessionManager:
    def __init__(self, controller):
        self.controller = controller
        self.sessions = {}

    def start_session(self, slot, uid, initial_balance):
        self.sessions[slot] = {'uid': uid, 'remaining': initial_balance, 'start_time': time.time(), 'active': True}

    def stop_session(self, slot):
        if slot in self.sessions:
            self.sessions[slot]['active'] = False

    def get_remaining(self, slot):
        if slot in self.sessions and self.sessions[slot]['active']:
            return self.sessions[slot]['remaining']
        return 0

    def update_remaining(self, slot, new_remaining):
        if slot in self.sessions:
            self.sessions[slot]['remaining'] = new_remaining

# ----------------- Main Application -----------------
class KioskApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Smart Kiosk - UI-HD (cleaned)")
        self.attributes("-fullscreen", False)
        self.geometry("1024x600")
        self.minsize(800, 480)
        self.config(cursor="arrow")
        self.bind("<Escape>", lambda e: self.attributes("-fullscreen", False))

        # state
        self.active_uid = None
        self.active_slot = None
        self.coin_counters = {}  # per-uid coin insert summary
        self.frames = {}

        # hardware interface
        try:
            if HARDWARE_GPIO_AVAILABLE:
                self.hw = HardwareGPIO(pinmap=None, mode='auto')
                self.hw.setup()
            else:
                self.hw = None
        except Exception:
            self.hw = None

        # Arduino listener
        self.arduino_listener = None
        self.arduino_available = False
        if HAS_ARDUINO_LISTENER:
            try:
                self.arduino_listener = ArduinoListener(event_callback=self._arduino_event_callback)
                started = self.arduino_listener.start()
                self.arduino_available = started
                print(f"Arduino listener started: {started}")
            except Exception as e:
                print(f"Arduino listener init error: {e}")
                self.arduino_listener = None
                self.arduino_available = False

        # session manager
        self.session_manager = SessionManager(self)

        # build UI frames
        container = tk.Frame(self)
        container.pack(fill="both", expand=True)
        for F in (ScanScreen, RegisterChoiceScreen, RegisterScreen, MainScreen, SlotSelectScreen, ChargingScreen, WaterScreen):
            frame = F(container, self)
            self.frames[F] = frame
            frame.grid(row=0, column=0, sticky="nsew")

        # start with ScanScreen
        self.show_frame(ScanScreen)

    # Arduino command wrapper
    def send_arduino_command(self, cmd):
        if not self.arduino_available or not self.arduino_listener:
            print(f"Cannot send to Arduino (not available): {cmd}")
            return False
        try:
            return self.arduino_listener.send_command(cmd)
        except Exception as e:
            print(f"Error sending command: {e}")
            return False

    def _arduino_event_callback(self, event, value):
        # Central dispatcher — keeps behaviour similar to original UI
        print(f"[ARDUINO EVENT] {event} = {value}")
        try:
            if event == 'coin' and value is not None:
                uid = self.active_uid
                if uid:
                    # water or charge depends on current frame
                    frame_name = getattr(self, 'current_frame', None)
                    if frame_name == 'WaterScreen':
                        added_ml = WATER_COIN_MAP.get(value, 0)
                        user = read_user(uid)
                        if user and user.get('type') == 'member':
                            newbal = (user.get('water_balance', 0) or 0) + added_ml
                            write_user(uid, {"water_balance": newbal})
                        else:
                            newbal = (user.get('temp_water_time', 0) or 0) + added_ml
                            write_user(uid, {"temp_water_time": newbal})
                        # update UI
                        ws = self.frames.get(WaterScreen)
                        if ws:
                            ws.time_var.set(str(newbal))
                            if newbal > 0:
                                ws.status_lbl.config(text=f"Balance: {newbal}mL - Place cup to start")
                            else:
                                ws.status_lbl.config(text="Insert coins to buy water")
                            ws.update_idletasks()
                        # record and popup
                        self.record_coin_insert(uid, value, added_ml)
                        self.show_coin_popup(uid, peso=value, added_ml=added_ml, total_ml=newbal)
                    else:
                        # treat as charging coin by default in other screens
                        added_sec = COIN_MAP.get(value, 0)
                        if added_sec > 0:
                            user = read_user(uid)
                            newbal = (user.get('charge_balance', 0) or 0) + added_sec
                            write_user(uid, {"charge_balance": newbal})
                            # update ChargingScreen UI if present
                            cs = self.frames.get(ChargingScreen)
                            if cs:
                                cs.time_var.set(str(newbal))
                                if hasattr(cs, 'remaining'):
                                    cs.remaining = newbal
                                cs.update_idletasks()
                            self.record_coin_insert(uid, value, added_sec)
                            mins = newbal // 60; secs = newbal % 60
                            try:
                                messagebox.showinfo("Coin Inserted", f"Coin inserted: ₱{value}\nCharging time: {mins}m {secs}s")
                            except Exception:
                                print(f"POPUP: ₱{value} -> {mins}m {secs}s")
                return

            # route water events to WaterScreen
            if event in ['cup_detected', 'countdown', 'countdown_end', 'dispense_start', 'dispense_done', 'dispense_progress', 'credit_left', 'cup_removed']:
                ws = self.frames.get(WaterScreen)
                if ws and hasattr(ws, 'handle_arduino_event'):
                    ws.handle_arduino_event(event, value)
                return

            # route charge events to ChargingScreen
            if event in ['current_sensor', 'plug_status', 'charging_event']:
                cs = self.frames.get(ChargingScreen)
                if cs and hasattr(cs, 'handle_arduino_event'):
                    cs.handle_arduino_event(event, value)
                return

            # system events
            if event in ['system_ready', 'calibration_done']:
                print(f"SYSTEM EVENT: {event} = {value}")
                return

        except Exception as e:
            print(f"Error in central event dispatcher: {e}")

    # coin record helpers
    def record_coin_insert(self, uid, amount, seconds_or_ml):
        rec = self.coin_counters.get(uid, {'coins': 0, 'value': 0, 'amount': 0})
        rec['coins'] += 1
        rec['value'] += seconds_or_ml
        rec['amount'] += amount
        self.coin_counters[uid] = rec
        # show a small popup non-blocking
        try:
            self.after(0, lambda: self._show_coin_summary(uid))
        except Exception:
            self._show_coin_summary(uid)

    def _show_coin_summary(self, uid):
        rec = self.coin_counters.get(uid, {'coins': 0, 'value': 0, 'amount': 0})
        user = read_user(uid)
        active = getattr(self, 'current_frame', None)
        try:
            if active == 'WaterScreen':
                bal = user.get('water_balance', 0) or user.get('temp_water_time', 0) or rec.get('value', 0)
                liters = (bal or 0) / 1000.0
                msg = f"Coins inserted: ₱{rec.get('amount',0)}\nTotal water volume: {bal} mL (~{liters:.2f} L)"
            elif active in ('ChargingScreen', 'SlotSelectScreen'):
                bal = user.get('charge_balance', 0) or 0
                msg = f"Coins inserted: ₱{rec.get('amount',0)}\nCharging balance: {seconds_to_min_display(bal)}"
            else:
                msg = f"Coins inserted: ₱{rec.get('amount',0)}"
            try:
                messagebox.showinfo("Coin Inserted", msg)
            except Exception:
                print("COIN POPUP:", msg)
        except Exception:
            pass

    def show_coin_popup(self, uid, peso: int = None, added_ml: int = None, total_ml: int = None):
        def _do():
            if peso not in (1,5,10) and peso is not None:
                return
            parts=[]
            if peso is not None: parts.append(f"Inserted: P{peso}")
            if added_ml is not None and added_ml>0: parts.append(f"Added: {added_ml} mL")
            if total_ml is not None: parts.append(f"Total: {total_ml} mL")
            msg="\n".join(parts) if parts else "Coin event"
            try:
                messagebox.showinfo("Coin Inserted", msg)
            except Exception:
                print("POPUP:", msg)
        try:
            self.after(100, _do)
        except Exception:
            _do()

    # UI frame switching
    def show_frame(self, cls):
        try:
            self.current_frame = cls.__name__
        except Exception:
            self.current_frame = None
        frame = self.frames[cls]
        if hasattr(frame, "refresh"):
            try:
                frame.refresh()
            except Exception:
                pass
        # switch Arduino mode when changing frames (best-effort)
        try:
            if self.arduino_available:
                if cls.__name__ == 'WaterScreen':
                    self.send_arduino_command("MODE WATER")
                elif cls.__name__ in ('SlotSelectScreen', 'ChargingScreen'):
                    self.send_arduino_command("MODE CHARGE")
        except Exception:
            pass
        frame.tkraise()

    # graceful cleanup
    def cleanup(self):
        try:
            if self.arduino_listener and hasattr(self.arduino_listener, 'stop'):
                self.arduino_listener.stop()
        except:
            pass

# ----------------- Screens -----------------
class ScanScreen(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#2c3e50")
        self.controller = controller
        tk.Label(self, text="Welcome to Smart Kiosk", font=("Arial", 26, "bold"), fg="white", bg="#2c3e50").pack(pady=(24,6))
        tk.Label(self, text="Enter RFID UID:", font=("Arial", 14), fg="white", bg="#2c3e50").pack()
        self.uid_entry = tk.Entry(self, font=("Arial", 16), width=36)
        self.uid_entry.pack(pady=10)
        self.uid_entry.bind("<Return>", lambda e: self.scan())
        btn = tk.Frame(self, bg="#2c3e50")
        btn.pack()
        tk.Button(btn, text="Scan", font=("Arial", 14), bg="#27ae60", fg="white", width=12, command=self.scan).grid(row=0,column=0,padx=6)
        tk.Button(btn, text="Clear", font=("Arial", 14), bg="#c0392b", fg="white", width=12, command=self.clear).grid(row=0,column=1,padx=6)
        self.info = tk.Label(self, text="", fg="white", bg="#2c3e50")
        self.info.pack(pady=8)

    def scan(self):
        uid = self.uid_entry.get().strip()
        if not uid:
            self.info.config(text="Please enter an RFID UID.")
            return
        if not user_exists(uid):
            # create minimal non-member
            write_user(uid, {"type":"nonmember", "name":"Guest", "water_balance":None, "charge_balance":0, "occupied_slot":"none"})
            self.controller.active_uid = uid
            self.controller.show_frame(RegisterChoiceScreen)
            return
        self.controller.active_uid = uid
        self.controller.show_frame(MainScreen)

    def clear(self):
        self.uid_entry.delete(0, tk.END)
        self.info.config(text="")

    def refresh(self):
        self.info.config(text="")

class RegisterChoiceScreen(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#34495e")
        self.controller = controller
        tk.Label(self, text="Card not registered", font=("Arial", 22, "bold"), fg="white", bg="#34495e").pack(pady=18)
        tk.Label(self, text="Register as Member or continue as Guest", font=("Arial", 12), fg="white", bg="#34495e").pack(pady=6)
        btnf = tk.Frame(self, bg="#34495e")
        btnf.pack(pady=12)
        tk.Button(btnf, text="Register", font=("Arial", 14), bg="#16a085", fg="white", width=14, command=self.request_registration).grid(row=0,column=0,padx=6)
        tk.Button(btnf, text="Use as Guest", font=("Arial", 14), bg="#f39c12", fg="white", width=14, command=self.use_guest).grid(row=0,column=1,padx=6)

    def use_guest(self):
        self.controller.show_frame(MainScreen)

    def request_registration(self):
        uid = self.controller.active_uid
        if not uid: return
        # write registration request to DB if available
        if FIREBASE_AVAILABLE:
            try:
                req_ref = db.reference("registration_requests")
                ts = int(time.time()*1000)
                req_ref.child(uid).set({"timestamp": ts, "status": "pending"})
                append_audit_log(actor=uid, action='registration_request', meta={'uid': uid, 'ts': ts})
            except Exception:
                pass
        self.controller.show_frame(MainScreen)

    def refresh(self):
        pass

class RegisterScreen(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#16a085")
        self.controller = controller
        tk.Label(self, text="Register New Member", font=("Arial", 20, "bold"), fg="white", bg="#16a085").pack(pady=10)
        tk.Label(self, text="Name", fg="white", bg="#16a085").pack()
        self.name_entry = tk.Entry(self, font=("Arial", 14), width=36); self.name_entry.pack(pady=6)
        tk.Label(self, text="Student ID", fg="white", bg="#16a085").pack()
        self.id_entry = tk.Entry(self, font=("Arial", 14), width=36); self.id_entry.pack(pady=6)
        btnf = tk.Frame(self, bg="#16a085"); btnf.pack(pady=8)
        tk.Button(btnf, text="Register", font=("Arial",14), bg="#2980b9", fg="white", width=12, command=self.register).grid(row=0,column=0,padx=6)
        tk.Button(btnf, text="Cancel", font=("Arial",14), bg="#c0392b", fg="white", width=12, command=lambda: controller.show_frame(MainScreen)).grid(row=0,column=1,padx=6)
        self.msg = tk.Label(self, text="", fg="white", bg="#16a085"); self.msg.pack(pady=6)

    def register(self):
        uid = self.controller.active_uid
        name = self.name_entry.get().strip()
        sid = self.id_entry.get().strip()
        if not uid or not name or not sid:
            self.msg.config(text="Provide name and student ID.")
            return
        write_user(uid, {"type":"member","name":name,"student_id":sid,"water_balance":DEFAULT_WATER_BAL,"charge_balance":DEFAULT_CHARGE_BAL,"occupied_slot":"none","charging_status":"idle"})
        append_audit_log(actor=uid, action='register_member', meta={'uid':uid,'name':name,'student_id':sid})
        self.controller.show_frame(MainScreen)

    def refresh(self):
        self.name_entry.delete(0, tk.END)
        self.id_entry.delete(0, tk.END)
        self.msg.config(text="")

class UserInfoFrame(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#222f3e", height=80)
        self.controller = controller
        self.pack_propagate(False)
        self.name_lbl = tk.Label(self, text="Name: -", font=("Arial", 14, "bold"), fg="white", bg="#222f3e"); self.name_lbl.pack(anchor="w", padx=10, pady=(6,0))
        self.info_lbl = tk.Label(self, text="UID: -    Student ID: -", font=("Arial", 12), fg="white", bg="#222f3e"); self.info_lbl.pack(anchor="w", padx=10)
        self.bal_lbl = tk.Label(self, text="Water: -    Charge: -", font=("Arial", 12), fg="white", bg="#222f3e"); self.bal_lbl.pack(anchor="w", padx=10, pady=(0,6))

    def refresh(self):
        uid = self.controller.active_uid
        if not uid:
            self.name_lbl.config(text="Name: -")
            self.info_lbl.config(text="UID: -    Student ID: -")
            self.bal_lbl.config(text="Water: -    Charge: -"); return
        user = read_user(uid)
        if not user:
            self.name_lbl.config(text="Name: -"); self.info_lbl.config(text=f"UID: {uid}    Student ID: -"); self.bal_lbl.config(text="Water: -    Charge: -"); return
        name = user.get("name", "Guest") if user.get("type")=="member" else "Guest"
        sid = user.get("student_id", "")
        if user.get("type")=="member":
            wbal = user.get("water_balance", None)
        else:
            wbal = user.get("temp_water_time", 0) or 0
        cbal = user.get("charge_balance", 0) or 0
        self.name_lbl.config(text=f"Name: {name}")
        self.info_lbl.config(text=f"UID: {uid}    Student ID: {sid if sid else '-'}")
        self.bal_lbl.config(text=f"Water: {ml_to_liters_str(wbal)}    Charge: {seconds_to_min_display(cbal)}")

class MainScreen(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#34495e")
        self.controller = controller
        self.user_info = UserInfoFrame(self, controller)
        self.user_info.pack(fill="x")
        body = tk.Frame(self, bg="#34495e"); body.pack(expand=True, fill='both', pady=16)
        tk.Label(body, text="Select Service", font=("Arial", 24, "bold"), fg="white", bg="#34495e").pack(pady=12)
        btns = tk.Frame(body, bg="#34495e"); btns.pack()
        tk.Button(btns, text="Water Vendo", font=("Arial", 18), bg="#2980b9", fg="white", width=20, height=2, command=lambda: controller.show_frame(WaterScreen)).grid(row=0, column=0, padx=10, pady=8)
        tk.Button(btns, text="Phone Charging", font=("Arial", 18), bg="#27ae60", fg="white", width=20, height=2, command=lambda: controller.show_frame(SlotSelectScreen)).grid(row=0, column=1, padx=10, pady=8)
        self.register_small = tk.Button(self, text="Register as Member", font=("Arial", 10, "underline"), fg="white", bg="#34495e", bd=0, command=self.goto_register)
        self.register_small.pack(side="bottom", pady=8)
        tk.Button(self, text="Logout", font=("Arial", 12, "bold"), bg="#c0392b", fg="white", command=self.logout).pack(side="bottom", pady=6)

    def goto_register(self):
        uid = self.controller.active_uid
        if not uid: print("No UID"); return
        user = read_user(uid)
        if user and user.get("type")=="member":
            print("Already member")
            return
        # submit registration request
        if FIREBASE_AVAILABLE:
            try:
                req_ref = db.reference(f"registration_requests/{uid}")
                req = req_ref.get()
                if req and req.get('status')=='pending':
                    print("Registration pending")
                    return
                ts = int(time.time()*1000)
                req_ref.set({'timestamp':ts,'status':'pending'})
                append_audit_log(actor=uid, action='registration_request', meta={'ts':ts,'uid':uid})
                self.register_small.config(text='Registration Requested', state='disabled')
            except Exception as e:
                print("Registration error:", e)
        else:
            self.register_small.config(text='Registration Requested', state='disabled')

    def logout(self):
        self.controller.active_uid = None
        self.controller.show_frame(ScanScreen)

    def refresh(self):
        self.user_info.refresh()
        uid = self.controller.active_uid
        if uid:
            user = read_user(uid)
            if user and user.get("type")=="nonmember":
                self.register_small.config(state="normal")
            else:
                self.register_small.config(state="disabled")

class SlotSelectScreen(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#34495e")
        self.controller = controller
        self.user_info = UserInfoFrame(self, controller); self.user_info.pack(fill="x")
        tk.Label(self, text="Select Charging Slot", font=("Arial", 22, "bold"), fg="white", bg="#34495e").pack(pady=8)
        self.coin_frame_top = tk.LabelFrame(self, text="Add charge before slot", font=("Arial", 12, "bold"), fg="white", bg="#34495e", bd=2, labelanchor="n")
        self.coin_frame_top.pack(pady=6)
        self.coin_status_lbl = tk.Label(self.coin_frame_top, text="", fg="white", bg="#34495e"); self.coin_status_lbl.grid(row=0, column=0, columnspan=3, pady=(4,0))
        # NOTE: Simulation buttons removed here (user requested)
        # Slot buttons
        self.slot_buttons = {}
        slots_frame = tk.Frame(self, bg="#34495e"); slots_frame.pack(pady=8)
        for i in range(1,5):
            b = tk.Button(slots_frame, text=f"Slot {i}\nFree", font=("Arial", 14, "bold"), width=16, height=4, bg="#2ecc71", fg="white", command=lambda idx=i: self.select_slot(idx))
            b.grid(row=(i-1)//2, column=(i-1)%2, padx=10, pady=8)
            self.slot_buttons[f"slot{i}"] = b

    def refresh(self):
        # show slot statuses
        for i in range(1,5):
            key = f"slot{i}"
            slot = read_slot(key)
            status = slot.get("status","inactive")
            cur = slot.get("current_user", "none")
            if cur != "none":
                text = f"Slot {i}\nOccupied"
                color = "#e74c3c"
            else:
                if status == "active":
                    text = f"Slot {i}\nIn Use"
                    color = "#e74c3c"
                else:
                    text = f"Slot {i}\nFree"
                    color = "#2ecc71"
            try:
                self.slot_buttons[key].config(text=text, bg=color)
            except Exception:
                pass
        # coin status summary
        uid = self.controller.active_uid
        if uid:
            rec = self.controller.coin_counters.get(uid)
            if rec:
                self.coin_status_lbl.config(text=f"Coins inserted: {rec.get('coins',0)} (≈ {rec.get('value',0)})")
            else:
                self.coin_status_lbl.config(text="")
        else:
            self.coin_status_lbl.config(text="")

    def select_slot(self, i):
        uid = self.controller.active_uid
        if not uid:
            print("Scan first")
            return
        user = read_user(uid)
        cb = user.get("charge_balance", 0) or 0
        if cb <= 0:
            print("No charge balance")
            return
        slot_key = f"slot{i}"
        write_user(uid, {"occupied_slot": slot_key})
        if FIREBASE_AVAILABLE and users_ref:
            users_ref.child(uid).child("slot_status").update({slot_key: "inactive"})
        write_slot(slot_key, {"status":"inactive","current_user":uid})
        self.controller.active_slot = slot_key
        # hide top coin area (we removed simulation buttons)
        try:
            self.coin_frame_top.pack_forget()
        except Exception:
            pass
        self.controller.show_frame(ChargingScreen)

class ChargingScreen(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#34495e")
        self.controller = controller
        self.user_info = UserInfoFrame(self, controller); self.user_info.pack(fill="x")
        body = tk.Frame(self, bg="#34495e"); body.pack(expand=True, fill='both', pady=12)
        self.slot_lbl = tk.Label(body, text="Charging Slot -", font=("Arial", 28, "bold"), fg="white", bg="#34495e"); self.slot_lbl.pack(pady=(16,8))
        tk.Label(body, text="Time Left (sec)", font=("Arial", 14), fg="white", bg="#34495e").pack(pady=(6,2))
        self.time_var = tk.StringVar(value="0")
        tk.Label(body, textvariable=self.time_var, font=("Arial", 40, "bold"), fg="white", bg="#34495e").pack(pady=6)
        btnf = tk.Frame(body, bg="#34495e"); btnf.pack(pady=10)
        tk.Button(btnf, text="Start Charging", font=("Arial", 14, "bold"), bg="#16a085", fg="white", width=16, command=self.start_charging).grid(row=0,column=0,padx=6)
        tk.Button(btnf, text="End Charging", font=("Arial", 14, "bold"), bg="#c0392b", fg="white", width=16, command=self._end_charging_session).grid(row=0,column=1,padx=6)
        self.remaining = 0
        self.is_charging = False
        self._tick_job = None

    def refresh(self):
        uid = self.controller.active_uid
        if not uid:
            self.time_var.set("0"); return
        user = read_user(uid)
        slot = user.get("occupied_slot","none")
        self.slot_lbl.config(text=f"{slot} - Charging" if slot and slot!="none" else "Charging Slot -")
        self.remaining = user.get("charge_balance",0) or 0
        self.time_var.set(str(self.remaining))

    def start_charging(self):
        uid = self.controller.active_uid
        if not uid:
            print("Scan first"); return
        user = read_user(uid)
        cb = user.get("charge_balance",0) or 0
        if cb <= 0:
            print("No charge balance"); return
        slot = self.controller.active_slot
        # mark DB and start local tick
        write_user(uid, {"charging_status":"charging"})
        write_slot(slot, {"status":"active","current_user":uid})
        append_audit_log(actor=uid, action='start_charging', meta={'slot':slot})
        self.is_charging = True
        self._start_tick()

    def _start_tick(self):
        if self._tick_job:
            try: self.after_cancel(self._tick_job)
            except: pass
        self._tick()
    def _tick(self):
        if not self.is_charging:
            return
        # decrement local remaining and push to DB periodically
        try:
            self.remaining -= 1
            if self.remaining < 0:
                self.remaining = 0
            self.time_var.set(str(self.remaining))
            uid = self.controller.active_uid
            if uid:
                write_user(uid, {"charge_balance": self.remaining})
            if self.remaining <= 0:
                self.stop_session()
                return
        except Exception as e:
            print("Tick error:", e)
        self._tick_job = self.after(1000, self._tick)

    def _end_charging_session(self):
        uid = self.controller.active_uid
        if not uid: return
        self.stop_session()
        self.controller.show_frame(MainScreen)

    def stop_session(self):
        if not self.is_charging:
            return
        uid = self.controller.active_uid
        slot = self.controller.active_slot
        write_user(uid, {"charging_status":"idle","occupied_slot":"none"})
        if slot:
            write_slot(slot, {"status":"inactive","current_user":"none"})
        append_audit_log(actor=uid, action='stop_charging', meta={'slot':slot})
        self.is_charging = False
        try:
            if self._tick_job:
                self.after_cancel(self._tick_job)
                self._tick_job = None
        except Exception:
            pass
        self.remaining = 0
        self.time_var.set("0")

    def handle_arduino_event(self, event, value):
        # Basic hook for charging-related Arduino events (if used)
        if event == 'charging_event':
            print("Charging event:", value)

class WaterScreen(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#34495e")
        self.controller = controller
        self.user_info = UserInfoFrame(self, controller); self.user_info.pack(fill="x")
        body = tk.Frame(self, bg="#34495e"); body.pack(expand=True, fill='both', pady=12)
        tk.Label(body, text="Water Vendo", font=("Arial", 22, "bold"), fg="white", bg="#34495e").pack(pady=6)
        self.status_lbl = tk.Label(body, text="Insert coins to buy water", font=("Arial", 16), fg="white", bg="#34495e"); self.status_lbl.pack(pady=6)
        tk.Label(body, text="Balance (mL)", font=("Arial", 12), fg="white", bg="#34495e").pack()
        self.time_var = tk.StringVar(value="0")
        tk.Label(body, textvariable=self.time_var, font=("Arial", 28, "bold"), fg="white", bg="#34495e").pack(pady=6)
        btnf = tk.Frame(body, bg="#34495e"); btnf.pack(pady=10)
        tk.Button(btnf, text="Start Dispense", font=("Arial", 14, "bold"), bg="#16a085", fg="white", width=16, command=self.force_dispense).grid(row=0,column=0,padx=6)
        tk.Button(btnf, text="Cancel", font=("Arial", 14, "bold"), bg="#c0392b", fg="white", width=16, command=lambda: controller.show_frame(MainScreen)).grid(row=0,column=1,padx=6)
        # NOTE: Simulation buttons removed here as per request
        self.is_dispensing = False
        self.cup_present = False

    def refresh(self):
        uid = self.controller.active_uid
        if not uid:
            self.time_var.set("0"); self.status_lbl.config(text="Insert coins to buy water"); return
        user = read_user(uid)
        if user and user.get("type")=="member":
            bal = user.get("water_balance", 0) or 0
        else:
            bal = user.get("temp_water_time", 0) or 0
        self.time_var.set(str(bal))
        if bal > 0:
            self.status_lbl.config(text=f"Balance: {bal}mL - Place cup to start")
        else:
            self.status_lbl.config(text="Insert coins to buy water")

    def force_dispense(self):
        # Provide a manual trigger to ask Arduino to start countdown/dispense
        if not self.controller.arduino_available:
            print("Arduino not available to force dispense.")
            return
        # tell Arduino to start countdown (if implemented)
        self.controller.send_arduino_command("FORCE_COUNTDOWN")

    def handle_arduino_event(self, event, value):
        try:
            if event == 'cup_detected':
                self.cup_present = True
                self.status_lbl.config(text="Cup detected - Starting countdown.")
            elif event == 'countdown':
                self.status_lbl.config(text=f"Countdown: {value} seconds.")
            elif event == 'countdown_end':
                self.status_lbl.config(text="Dispensing...")
            elif event == 'dispense_start':
                self.is_dispensing = True
                self.status_lbl.config(text="Dispensing water...")
            elif event == 'dispense_done':
                dispensed_ml = value
                # update user balance: reduce the amount dispensed (arduino sets credit zero usually)
                uid = self.controller.active_uid
                if uid:
                    user = read_user(uid)
                    if user and user.get("type")=="member":
                        newbal = (user.get("water_balance", 0) or 0) - int(round(dispensed_ml))
                        if newbal < 0: newbal = 0
                        write_user(uid, {"water_balance": newbal})
                    else:
                        newbal = (user.get("temp_water_time", 0) or 0) - int(round(dispensed_ml))
                        if newbal < 0: newbal = 0
                        write_user(uid, {"temp_water_time": newbal})
                    # update display
                    self.time_var.set(str(newbal))
                    self.status_lbl.config(text=f"Dispense complete: {dispensed_ml:.0f} mL")
                self.is_dispensing = False
            elif event == 'credit_left':
                remaining_ml = value
                self.time_var.set(str(int(remaining_ml)))
                self.status_lbl.config(text=f"Balance: {int(remaining_ml)}mL - Place cup to continue")
            elif event == 'dispense_progress':
                if isinstance(value, dict):
                    dispensed = value.get('dispensed', 0)
                    remaining = value.get('remaining', 0)
                    self.time_var.set(str(int(remaining)))
                    self.status_lbl.config(text=f"Dispensing. {int(remaining)}mL left")
        except Exception as e:
            print("WaterScreen event error:", e)

# ----------------- Entrypoint -----------------
if __name__ == "__main__":
    app = KioskApp()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        try: app.cleanup()
        except: pass
