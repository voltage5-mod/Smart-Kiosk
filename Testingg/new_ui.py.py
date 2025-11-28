# kiosk_ui_refactored.py
# Refactored UI for Solar-Powered Smart Vending and Charging Station

import tkinter as tk
from tkinter import messagebox
import firebase_admin
from firebase_admin import credentials, db
import time
import json
import os

# Configuration
DATABASE_URL = "https://kiosk-testing-22bf4-default-rtdb.firebaseio.com/"
SERVICE_KEY = "kiosk-testing-22bf4-firebase-adminsdk-fbsvc-2c5b11e75d.json"

# Constants
WATER_SECONDS_PER_LITER = 10
WATER_DB_WRITE_INTERVAL = 2
CHARGE_DB_WRITE_INTERVAL = 10
UNPLUG_GRACE_SECONDS = 60
NO_CUP_TIMEOUT = 10

# Detection thresholds
PLUG_THRESHOLD = 0.14
PLUG_CONFIRM_COUNT = 4
PLUG_CONFIRM_WINDOW = 2.0
UNPLUG_THRESHOLD = 0.3
UNPLUG_CONFIRM_COUNT = 4
UNPLUG_CONFIRM_WINDOW = 2.0

# Coin mappings
COIN_MAP = {1: 60, 5: 300, 10: 600}
WATER_COIN_MAP = {1: 50, 5: 250, 10: 500}

# Default balances
DEFAULT_WATER_BAL = 600
DEFAULT_CHARGE_BAL = 1200

# Firebase setup
cred = credentials.Certificate(SERVICE_KEY)
firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})
users_ref = db.reference("users")
slots_ref = db.reference("slots")

# Ensure slots exist
for i in range(1, 5):
    slot_key = f"slot{i}"
    if slots_ref.child(slot_key).get() is None:
        slots_ref.child(slot_key).set({"status": "inactive", "current_user": "none"})

# Core Helper Functions
def user_exists(uid):
    return users_ref.child(uid).get() is not None

def create_nonmember(uid):
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

def read_user(uid):
    return users_ref.child(uid).get()

def write_user(uid, data):
    users_ref.child(uid).update(data)

def read_slot(slot):
    return slots_ref.child(slot).get()

def write_slot(slot, data):
    slots_ref.child(slot).update(data)

def seconds_to_min_display(sec):
    return f"{sec//60}m {sec%60}s" if sec is not None else "N/A"

def water_seconds_to_liters(sec):
    try:
        liters = float(sec or 0) / 1000.0
        return f"{liters:.2f} L"
    except:
        return "N/A"

# Base Screen Class
class BaseScreen(tk.Frame):
    def __init__(self, parent, controller, bg_color):
        super().__init__(parent, bg=bg_color)
        self.controller = controller
    
    def refresh(self):
        pass

# User Info Component
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
            self.clear_display()
            return
            
        user = read_user(uid)
        if not user:
            self.clear_display(uid)
            return
            
        name = user.get("name", "Guest") if user.get("type") == "member" else "Guest"
        sid = user.get("student_id", "")
        
        if user.get("type") == "member":
            wbal = user.get("water_balance", None)
        else:
            wbal = user.get("temp_water_time", 0) or 0
            
        cbal = user.get("charge_balance", 0)
        
        self.name_lbl.config(text=f"Name: {name}")
        self.info_lbl.config(text=f"UID: {uid}    Student ID: {sid if sid else '-'}")
        self.bal_lbl.config(text=f"Water: {water_seconds_to_liters(wbal)}    Charge: {seconds_to_min_display(cbal)}")

    def clear_display(self, uid=None):
        self.name_lbl.config(text="Name: -")
        self.info_lbl.config(text=f"UID: {uid if uid else '-'}    Student ID: -")
        self.bal_lbl.config(text="Water: -    Charge: -")

# Screen: Scan UID
class ScanScreen(BaseScreen):
    def __init__(self, parent, controller):
        super().__init__(parent, controller, "#2c3e50")
        
        tk.Label(self, text="Welcome to MSEUFCi Kiosk", font=("Arial", 28, "bold"),
                 fg="white", bg="#2c3e50").pack(pady=(20, 5))
        
        tk.Label(self, text="Enter RFID UID and press Scan", font=("Arial", 14),
                 fg="white", bg="#2c3e50").pack(pady=(0, 12))
        
        self.uid_entry = tk.Entry(self, font=("Arial", 18), width=36)
        self.uid_entry.pack(pady=5)
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
            self.info.config(text="Please enter an RFID UID.")
            return

        if not user_exists(uid):
            create_nonmember(uid)
            self.controller.active_uid = uid
            self.controller.show_frame(RegisterChoiceScreen)
            return

        self.controller.active_uid = uid
        self.controller.show_frame(MainScreen)

    def refresh(self):
        self.clear()
        self.uid_entry.focus_set()

# Screen: Registration Choice
class RegisterChoiceScreen(BaseScreen):
    def __init__(self, parent, controller):
        super().__init__(parent, controller, "#34495e")
        
        tk.Label(self, text="Card not registered", font=("Arial", 24, "bold"),
                 fg="white", bg="#34495e").pack(pady=20)
        
        tk.Label(self, text="Register now or continue as Guest",
                 font=("Arial", 14), fg="white", bg="#34495e").pack(pady=8)
        
        btn_frame = tk.Frame(self, bg="#34495e")
        btn_frame.pack(pady=10)
        
        tk.Button(btn_frame, text="Register", font=("Arial", 14, "bold"),
                  bg="#16a085", fg="white", width=16, command=self.request_registration).grid(row=0, column=0, padx=10)
        
        tk.Button(btn_frame, text="Use as Guest", font=("Arial", 14, "bold"),
                  bg="#f39c12", fg="white", width=12, command=self.use_guest).grid(row=0, column=1, padx=10)

    def use_guest(self):
        self.controller.show_frame(MainScreen)

    def request_registration(self):
        uid = self.controller.active_uid
        if not uid:
            return
            
        if not user_exists(uid):
            create_nonmember(uid)
            
        req_ref = db.reference("registration_requests")
        ts = int(time.time() * 1000)
        req_ref.child(uid).set({"timestamp": ts, "status": "pending"})
        
        self.controller.show_frame(MainScreen)

# Screen: Main Menu
class MainScreen(BaseScreen):
    def __init__(self, parent, controller):
        super().__init__(parent, controller, "#34495e")
        
        self.user_info = UserInfoFrame(self, controller)
        self.user_info.pack(fill="x")

        body = tk.Frame(self, bg="#34495e")
        body.pack(fill="both", expand=True, pady=8)
        
        tk.Label(body, text="Select Service", font=("Arial", 24, "bold"), 
                 fg="white", bg="#34495e").pack(pady=12)
        
        btn_frame = tk.Frame(body, bg="#34495e")
        btn_frame.pack(pady=10)
        
        tk.Button(btn_frame, text="Water Vendo", font=("Arial", 18, "bold"),
                  bg="#2980b9", fg="white", width=20, height=2, 
                  command=lambda: controller.show_frame(WaterScreen)).grid(row=0, column=0, padx=10, pady=8)
        
        tk.Button(btn_frame, text="Phone Charging", font=("Arial", 18, "bold"),
                  bg="#27ae60", fg="white", width=20, height=2, 
                  command=lambda: controller.show_frame(SlotSelectScreen)).grid(row=0, column=1, padx=10, pady=8)

        self.register_btn = tk.Button(self, text="Register as Member", font=("Arial", 10, "underline"),
                                     fg="white", bg="#34495e", bd=0, command=self.goto_register)
        self.register_btn.pack(side="bottom", pady=10)

        tk.Button(self, text="Logout", font=("Arial", 12, "bold"), bg="#c0392b", fg="white",
                  command=self.logout).pack(side="bottom", pady=6)

    def goto_register(self):
        uid = self.controller.active_uid
        if not uid:
            return
            
        req_ref = db.reference(f"registration_requests/{uid}")
        existing = req_ref.get()
        
        if existing and existing.get('status') == 'pending':
            return
            
        ts = int(time.time() * 1000)
        req_ref.set({'timestamp': ts, 'status': 'pending'})
        self.register_btn.config(text='Registration Requested', state='disabled')

    def logout(self):
        self.controller.active_uid = None
        self.controller.show_frame(ScanScreen)

    def refresh(self):
        self.user_info.refresh()
        
        uid = self.controller.active_uid
        if uid:
            user = read_user(uid)
            if user and user.get("type") == "nonmember":
                self.register_btn.config(state="normal")
            else:
                self.register_btn.config(state="disabled")
        else:
            self.register_btn.config(state="disabled")

# Screen: Slot Selection
class SlotSelectScreen(BaseScreen):
    def __init__(self, parent, controller):
        super().__init__(parent, controller, "#34495e")
        
        self.user_info = UserInfoFrame(self, controller)
        self.user_info.pack(fill="x")

        tk.Label(self, text="Select Charging Slot", font=("Arial", 22, "bold"),
                 fg="white", bg="#34495e").pack(pady=6)

        self.slot_buttons = {}
        grid = tk.Frame(self, bg="#34495e")
        grid.pack(pady=8)
        
        for i in range(1, 5):
            btn = tk.Button(grid, text=f"Slot {i}\nChecking...", font=("Arial", 14, "bold"),
                            bg="#95a5a6", fg="black", width=14, height=2,
                            command=lambda s=i: self.select_slot(s))
            btn.grid(row=(i-1)//3, column=(i-1)%3, padx=10, pady=8)
            self.slot_buttons[f"slot{i}"] = btn

        tk.Button(self, text="Back", font=("Arial", 14, "bold"), bg="#c0392b", fg="white",
                  command=lambda: controller.show_frame(MainScreen)).pack(pady=6, anchor='nw', padx=8)

    def refresh(self):
        self.user_info.refresh()
        
        for i in range(1, 5):
            key = f"slot{i}"
            slot = read_slot(key)
            text = f"Slot {i}\nFree"
            color = "#2ecc71"
            
            if slot:
                status = slot.get("status", "inactive")
                cur = slot.get("current_user", "none")
                uid = self.controller.active_uid
                
                if cur != "none":
                    if cur == uid:
                        text = f"Slot {i}\nIn Use"
                        color = "#95a5a6"
                    else:
                        text = f"Slot {i}\nOccupied"
                        color = "#e74c3c"
                elif status == "active":
                    text = f"Slot {i}\nIn Use"
                    color = "#e74c3c"
                    
            self.slot_buttons[key].config(text=text, bg=color)

    def select_slot(self, i):
        uid = self.controller.active_uid
        if not uid:
            return
            
        user = read_user(uid)
        cb = user.get("charge_balance", 0) if user else 0
        if (cb or 0) <= 0:
            return
            
        slot_key = f"slot{i}"
        slot = read_slot(slot_key)
        
        if slot:
            cur = slot.get("current_user", "none")
            status = slot.get("status", "inactive")
            if cur != "none" and cur != uid:
                return
            if status == "active" and cur != uid:
                return

        write_user(uid, {"occupied_slot": slot_key})
        users_ref.child(uid).child("slot_status").update({slot_key: "inactive"})
        write_slot(slot_key, {"status": "inactive", "current_user": uid})
        
        self.controller.active_slot = slot_key
        self.controller.show_frame(ChargingScreen)

# Screen: Charging
class ChargingScreen(BaseScreen):
    def __init__(self, parent, controller):
        super().__init__(parent, controller, "#34495e")
        
        self.user_info = UserInfoFrame(self, controller)
        self.user_info.pack(fill="x")

        body = tk.Frame(self, bg="#34495e")
        body.pack(expand=True, fill='both', pady=12)

        self.slot_lbl = tk.Label(body, text="Charging Slot -", font=("Arial", 28, "bold"), 
                                fg="white", bg="#34495e")
        self.slot_lbl.pack(pady=(20, 12))

        self.time_var = tk.StringVar(value="0")
        tk.Label(body, text="Time Left (sec)", font=("Arial", 14), fg="white", bg="#34495e").pack(pady=(6, 2))
        tk.Label(body, textvariable=self.time_var, font=("Arial", 28, "bold"), 
                fg="white", bg="#34495e").pack(pady=(0, 12))

        btn_frame = tk.Frame(body, bg="#34495e")
        btn_frame.pack(pady=8)
        
        tk.Button(btn_frame, text="Back", font=("Arial", 12, "bold"),
                  bg="#95a5a6", fg="white", width=10, 
                  command=lambda: controller.show_frame(MainScreen)).grid(row=0, column=0, padx=6)
        
        tk.Button(btn_frame, text="Start Charging", font=("Arial", 14, "bold"),
                  bg="#2980b9", fg="white", width=14, command=self.start_charging).grid(row=0, column=1, padx=6)
        
        tk.Button(btn_frame, text="Stop Session", font=("Arial", 12, "bold"),
                  bg="#c0392b", fg="white", width=14, command=self.stop_session).grid(row=0, column=2, padx=6)

        # Session state
        self.is_charging = False
        self.remaining = 0
        self._tick_job = None

    def refresh(self):
        self.user_info.refresh()
        
        slot = self.controller.active_slot or "none"
        display_text = f"Charging Slot {slot[4:] if slot.startswith('slot') else slot}"
        self.slot_lbl.config(text=display_text)
        
        uid = self.controller.active_uid
        if uid:
            user = read_user(uid)
            cb = user.get("charge_balance", 0) or 0
            self.time_var.set(str(cb))
            if not self.is_charging:
                self.remaining = cb
        else:
            self.time_var.set("0")

    def start_charging(self):
        uid = self.controller.active_uid
        if not uid:
            return
            
        user = read_user(uid)
        cb = user.get("charge_balance", 0) or 0
        if cb <= 0:
            return
            
        slot = self.controller.active_slot
        write_user(uid, {"charging_status": "charging"})
        
        if slot:
            users_ref.child(uid).child("slot_status").update({slot: "active"})
            write_slot(slot, {"status": "active", "current_user": uid})

        self.is_charging = True
        self.remaining = cb
        self.time_var.set(str(self.remaining))
        
        if self._tick_job is None:
            self._charging_tick()

    def _charging_tick(self):
        if not self.is_charging:
            return
            
        uid = self.controller.active_uid
        if not uid:
            self.stop_session()
            return

        if self.remaining <= 0:
            self.stop_session()
            return

        self.remaining -= 1
        self.time_var.set(str(self.remaining))
        
        write_user(uid, {"charge_balance": self.remaining})
        
        self._tick_job = self.after(1000, self._charging_tick)

    def stop_session(self):
        uid = self.controller.active_uid
        slot = self.controller.active_slot
        
        if self._tick_job:
            self.after_cancel(self._tick_job)
            self._tick_job = None
            
        if uid:
            write_user(uid, {
                "charging_status": "idle", 
                "charge_balance": 0, 
                "occupied_slot": "none"
            })
            
        if slot:
            write_slot(slot, {"status": "inactive", "current_user": "none"})
            users_ref.child(uid).child("slot_status").update({slot: "inactive"})
            
        self.controller.active_slot = None
        self.is_charging = False
        self.controller.show_frame(MainScreen)

# Screen: Water Dispensing
class WaterScreen(BaseScreen):
    def __init__(self, parent, controller):
        super().__init__(parent, controller, "#2980b9")
        
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
        tk.Label(body, text="Time (sec)", font=("Arial", 14), fg="white", bg="#2980b9").pack()
        tk.Label(body, textvariable=self.time_var, font=("Arial", 28, "bold"), 
                fg="white", bg="#2980b9").pack(pady=6)

        btn_frame = tk.Frame(body, bg="#2980b9")
        btn_frame.pack(pady=8)
        
        tk.Button(btn_frame, text="Stop Session", font=("Arial", 12, "bold"),
                  bg="#c0392b", fg="white", width=20, command=self.stop_session).pack(pady=8)

        # Simulation buttons for testing
        sim_frame = tk.Frame(body, bg="#2980b9")
        sim_frame.pack(pady=10)
        
        tk.Button(sim_frame, text="Place Cup", font=("Arial", 12, "bold"),
                  bg="#27ae60", fg="white", width=12, command=self.place_cup).grid(row=0, column=0, padx=5)
        
        tk.Button(sim_frame, text="Remove Cup", font=("Arial", 12, "bold"),
                  bg="#f39c12", fg="white", width=12, command=self.remove_cup).grid(row=0, column=1, padx=5)
        
        tk.Button(sim_frame, text="Add ₱1", font=("Arial", 12, "bold"),
                  bg="#f39c12", fg="white", width=8, command=lambda: self.insert_coin(1)).grid(row=0, column=2, padx=5)

        # Session state
        self.cup_present = False
        self.temp_water_time = 0
        self._water_job = None
        self._water_nocup_job = None

    def refresh(self):
        self.user_info.refresh()
        
        uid = self.controller.active_uid
        if not uid:
            self.time_var.set("0")
            self.status_lbl.config(text="Place cup to start")
            return
            
        user = read_user(uid)
        if user.get("type") == "member":
            wb = user.get("water_balance", 0) or 0
            self.time_var.set(str(wb))
        else:
            temp = user.get("temp_water_time", 0) or 0
            self.temp_water_time = temp
            self.time_var.set(str(temp))
            if temp <= 0:
                self.status_lbl.config(text="Non-member: buy water with coins")
            else:
                self.status_lbl.config(text="Place cup to start (Purchased time)")

    def insert_coin(self, amount):
        uid = self.controller.active_uid
        if not uid:
            return
            
        user = read_user(uid)
        add_ml = WATER_COIN_MAP.get(amount, 0)
        
        if user.get("type") == "member":
            new_bal = (user.get("water_balance", 0) or 0) + add_ml
            write_user(uid, {"water_balance": new_bal})
        else:
            prev = user.get("temp_water_time", 0) or 0
            new_temp = prev + add_ml
            self.temp_water_time = new_temp
            write_user(uid, {"temp_water_time": new_temp})
            
        self.refresh()

    def place_cup(self):
        uid = self.controller.active_uid
        if not uid:
            return
            
        user = read_user(uid)
        if user.get("type") == "member":
            wb = user.get("water_balance", 0) or 0
            if wb <= 0:
                return
            self._start_water_session(wb, "member")
        else:
            if self.temp_water_time <= 0:
                return
            self._start_water_session(self.temp_water_time, "nonmember")

    def _start_water_session(self, balance, user_type):
        self.cup_present = True
        self.status_lbl.config(text="Dispensing...")
        self._water_remaining = balance
        
        if user_type == "member":
            self._water_job = self.after(1000, self._water_tick_member)
        else:
            self._water_job = self.after(1000, self._water_tick_nonmember)

    def _water_tick_member(self):
        if not self.cup_present:
            self._water_job = None
            return
            
        uid = self.controller.active_uid
        if not uid or self._water_remaining <= 0:
            self.stop_session()
            return

        self._water_remaining -= 1
        self.time_var.set(str(self._water_remaining))
        write_user(uid, {"water_balance": self._water_remaining})
        
        self._water_job = self.after(1000, self._water_tick_member)

    def _water_tick_nonmember(self):
        if not self.cup_present:
            self._water_job = None
            return
            
        if self._water_remaining <= 0:
            self.stop_session()
            return

        self._water_remaining -= 1
        self.time_var.set(str(self._water_remaining))
        self.temp_water_time = self._water_remaining
        
        self._water_job = self.after(1000, self._water_tick_nonmember)

    def remove_cup(self):
        self.cup_present = False
        self.status_lbl.config(text="Cup removed")
        
        if self._water_job:
            self.after_cancel(self._water_job)
            self._water_job = None

    def stop_session(self):
        uid = self.controller.active_uid
        
        if self._water_job:
            self.after_cancel(self._water_job)
            self._water_job = None
            
        if self._water_nocup_job:
            self.after_cancel(self._water_nocup_job)
            self._water_nocup_job = None
            
        if uid:
            user = read_user(uid)
            if user and user.get("type") != "member":
                write_user(uid, {"temp_water_time": 0})
                self.temp_water_time = 0
                
        self.cup_present = False
        self.controller.show_frame(MainScreen)

# Main Application
class KioskApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Smart Kiosk")
        
        # Fullscreen setup
        self.attributes("-fullscreen", True)
        self.config(cursor="none")
        self.geometry("800x480")
        self.minsize(640, 360)
        
        # Session variables
        self.active_uid = None
        self.active_slot = None
        
        # Container setup
        container = tk.Frame(self)
        container.pack(fill="both", expand=True)
        
        # Screen registry
        self.frames = {}
        screens = [ScanScreen, RegisterChoiceScreen, MainScreen, SlotSelectScreen, ChargingScreen, WaterScreen]
        
        for screen in screens:
            frame = screen(container, self)
            self.frames[screen] = frame
            frame.grid(row=0, column=0, sticky="nsew")
        
        # Start with scan screen
        self.show_frame(ScanScreen)

    def show_frame(self, cls):
        frame = self.frames[cls]
        if hasattr(frame, "refresh"):
            frame.refresh()
        frame.tkraise()

    def refresh_all_user_info(self):
        for frame in self.frames.values():
            if hasattr(frame, 'user_info'):
                try:
                    frame.user_info.refresh()
                except:
                    pass

# Application entry point
if __name__ == "__main__":
    app = KioskApp()
    app.mainloop()