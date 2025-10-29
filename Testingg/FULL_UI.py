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

# Coin to seconds mapping (charging)
COIN_MAP = {1: 60, 5: 300, 10: 600}  # 1 peso = 60s, 5 -> 300s, 10 -> 600s

# Default starting balances for newly registered members (seconds)
DEFAULT_WATER_BAL = 600   # 10 min
DEFAULT_CHARGE_BAL = 1200 # 20 min

# ------------------------------------------------

# Initialize Firebase Admin
cred = credentials.Certificate(SERVICE_KEY)
firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})
users_ref = db.reference("users")
slots_ref = db.reference("slots")

# Ensure slots node exists (slot1..slot5)
for i in range(1, 6):
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

        # start with 800x480 (7" typical) but allow resizing; keep a sensible minimum
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
            messagebox.showwarning("Input required", "Please enter an RFID UID.")
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
        messagebox.showinfo("Guest", "Proceeding as Guest (non-member). Use coins inside services.")
        self.controller.show_frame(MainScreen)

    def request_subscription(self):
        """Called when a not-registered user requests a subscription. This writes a subscription request
        entry in the DB so admins are notified in the dashboard.
        """
        uid = self.controller.active_uid
        if not uid:
            messagebox.showwarning("No UID", "No UID in session. Scan first.")
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
        messagebox.showinfo("Requested", "Subscription request sent. Admin has been notified.")
        # go to main screen (user can still use guest flows)
        self.controller.show_frame(MainScreen)

    def request_registration(self):
        """Called when a not-registered user taps Register on the kiosk.
        Instead of immediately creating a full member, create a registration_requests
        entry so admins are notified and can perform registration from the dashboard.
        """
        uid = self.controller.active_uid
        if not uid:
            messagebox.showwarning("No UID", "No UID in session. Scan first.")
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
        messagebox.showinfo("Requested", "Registration request sent. Admin has been notified.")
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
            messagebox.showwarning("Input required", "Please provide name and student ID.")
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
        messagebox.showinfo("Registered", "Registration successful. Welcome!")
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
            messagebox.showwarning("No user", "Scan first.")
            return
        user = read_user(uid)
        if user and user.get("type") == "member":
            messagebox.showinfo("Already a member", "This user is already a member.")
            return
        # Instead of opening a local registration UI, submit a registration request
        # to the admin dashboard via the Realtime Database so admins can approve.
        try:
            req_ref = db.reference(f"registration_requests/{uid}")
            existing = req_ref.get()
            if existing and existing.get('status') == 'pending':
                messagebox.showinfo("Request pending", "Registration request already submitted. Please wait for admin approval.")
                return
            ts = int(time.time() * 1000)
            req_ref.set({
                'timestamp': ts,
                'status': 'pending'
            })
            # add an audit entry so admins get a clear log
            append_audit_log(actor=uid, action='registration_request', meta={'ts': ts, 'uid': uid})
            messagebox.showinfo("Request submitted", "Registration request submitted. An admin will review it shortly.")
            # disable the button locally until admins process the request
            self.register_small.config(text='Registration Requested', state='disabled')
        except Exception as e:
            print('Error submitting registration request:', e)
            messagebox.showerror('Error', 'Failed to submit registration request. Please try again later.')

    def logout(self):
        self.controller.active_uid = None
        self.controller.active_slot = None
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
            messagebox.showwarning("No user", "Scan first.")
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
        messagebox.showinfo("Stopped", "Charging session ended.")

    def _unlock_my_slot(self):
        """Called when the logged-in user taps the Unlock Slot shortcut on MainScreen.
        This only unlocks the slot assigned to the currently logged-in user and does not stop charging.
        """
        uid = self.controller.active_uid
        if not uid:
            messagebox.showwarning("No user", "Scan first.")
            return
        user = read_user(uid)
        if not user:
            messagebox.showwarning("No user", "User record missing.")
            return
        slot = user.get("occupied_slot", "none") or "none"
        if slot == "none":
            messagebox.showinfo("No slot", "You don't have an assigned slot.")
            try:
                self.unlock_my_slot.pack_forget()
            except Exception:
                pass
            return
        # Update DB to unlock the slot but keep the user's charging_status untouched
        write_slot(slot, {"status": "inactive", "current_user": "none"})
        users_ref.child(uid).child("slot_status").update({slot: "inactive"})
        write_user(uid, {"occupied_slot": "none"})
        messagebox.showinfo("Unlocked", f"{slot} unlocked. You may unplug your device.")
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
        self.coin_frame_top = tk.LabelFrame(self, text="Coinslot (simulate) - add charge before slot", font=("Arial", 12, "bold"),
                                            fg="white", bg="#34495e", bd=2, labelanchor="n")
        self.coin_frame_top.pack(pady=6)
        tk.Button(self.coin_frame_top, text="₱1", font=("Arial", 12, "bold"), bg="#f39c12", fg="white", width=8,
                  command=lambda: self.insert_coin(1)).grid(row=0, column=0, padx=6, pady=6)
        tk.Button(self.coin_frame_top, text="₱5", font=("Arial", 12, "bold"), bg="#e67e22", fg="white", width=8,
                  command=lambda: self.insert_coin(5)).grid(row=0, column=1, padx=6, pady=6)
        tk.Button(self.coin_frame_top, text="₱10", font=("Arial", 12, "bold"), bg="#d35400", fg="white", width=8,
                  command=lambda: self.insert_coin(10)).grid(row=0, column=2, padx=6, pady=6)

        self.slot_buttons = {}
        grid = tk.Frame(self, bg="#34495e")
        grid.pack(pady=8)
        for i in range(1, 6):
            btn = tk.Button(grid, text=f"Slot {i}\n(Checking...)", font=("Arial", 16, "bold"),
                            bg="#95a5a6", fg="black", width=16, height=3,
                            command=lambda s=i: self.select_slot(s))
            btn.grid(row=(i-1)//3, column=(i-1)%3, padx=10, pady=8)
            self.slot_buttons[f"slot{i}"] = btn
        tk.Button(self, text="Back", font=("Arial", 14, "bold"), bg="#c0392b", fg="white",
                  command=lambda: controller.show_frame(MainScreen)).pack(pady=6)

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
        for i in range(1, 6):
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

    def select_slot(self, i):
        uid = self.controller.active_uid
        if not uid:
            messagebox.showwarning("No user", "Scan first before selecting a slot.")
            return
        # require a positive charge balance before allowing slot assignment
        user = read_user(uid)
        cb = user.get("charge_balance", 0) if user else 0
        if (cb or 0) <= 0:
            messagebox.showwarning("No balance", "Please insert coin(s) to add charging balance before selecting a slot.")
            return
        slot_key = f"slot{i}"
        slot = read_slot(slot_key)
        # if the slot is already active or assigned to someone else, prevent selection
        if slot is not None:
            cur = slot.get("current_user", "none")
            status = slot.get("status", "inactive")
            if cur != "none" and cur != uid:
                messagebox.showwarning("Slot Taken", f"{slot_key} is already assigned to another user.")
                return
            if status == "active" and cur != uid:
                messagebox.showwarning("In Use", f"{slot_key} is currently in use. Please choose another slot.")
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
        messagebox.showinfo("Slot Assigned", f"You selected {slot_key}. Please plug your device and press Start Charging.")
        self.controller.show_frame(ChargingScreen)

    def insert_coin(self, amount):
        # helper so coin slot appears before selecting slot
        uid = self.controller.active_uid
        if not uid:
            messagebox.showwarning("No user", "Scan first.")
            return
        add = COIN_MAP.get(amount, 0)
        user = read_user(uid)
        newbal = (user.get("charge_balance", 0) or 0) + add
        write_user(uid, {"charge_balance": newbal})
        try:
            append_audit_log(actor=uid, action='insert_coin', meta={'amount': amount, 'added_seconds': add, 'new_balance': newbal})
        except Exception:
            pass
        messagebox.showinfo("Coin Added", f"₱{amount} added => {add} seconds to charging balance.")


# --------- Screen: Charging ----------
class ChargingScreen(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#34495e")
        self.controller = controller
        # user info visible while charging
        self.user_info = UserInfoFrame(self, controller)
        self.user_info.pack(fill="x")

        body = tk.Frame(self, bg="#34495e")
        body.pack(expand=True, pady=12)
        tk.Label(body, text="Charging", font=("Arial", 22, "bold"), fg="white", bg="#34495e").pack(pady=6)
        self.slot_lbl = tk.Label(body, text="Slot: -", font=("Arial", 18, "bold"), fg="white", bg="#34495e")
        self.slot_lbl.pack(pady=4)

        self.time_var = tk.StringVar(value="0")
        tk.Label(body, text="Time Left (sec)", font=("Arial", 14), fg="white", bg="#34495e").pack()
        tk.Label(body, textvariable=self.time_var, font=("Arial", 28, "bold"), fg="white", bg="#34495e").pack(pady=6)

        btn_frame = tk.Frame(body, bg="#34495e")
        btn_frame.pack(pady=8)
        # Back button: allow user to return to Main screen while charging continues
        tk.Button(btn_frame, text="Back", font=("Arial", 12, "bold"),
                  bg="#95a5a6", fg="white", width=10, command=lambda: controller.show_frame(MainScreen)).grid(row=0, column=0, padx=6)
        # Start/Unlock/Unplug/Stop controls
        tk.Button(btn_frame, text="Start Charging", font=("Arial", 14, "bold"),
                  bg="#2980b9", fg="white", width=14, command=self.start_charging).grid(row=0, column=1, padx=6)
        tk.Button(btn_frame, text="Unlock Slot", font=("Arial", 14, "bold"),
                  bg="#f39c12", fg="white", width=14, command=self.unlock_slot).grid(row=0, column=2, padx=6)
    # Hardware integration: Simulate Unplug removed when hardware is present
        tk.Button(btn_frame, text="Stop Session", font=("Arial", 12, "bold"),
                  bg="#c0392b", fg="white", width=14, command=self.stop_session).grid(row=1, column=2, padx=6, pady=8)

        # coin area (for non-members or topping up)
        coin_frame = tk.LabelFrame(body, text="Coinslot (simulate) - adds charging time", font=("Arial", 12, "bold"),
                                   fg="white", bg="#34495e", bd=2, labelanchor="n")
        coin_frame.pack(pady=10)
        tk.Button(coin_frame, text="₱1", font=("Arial", 14, "bold"), bg="#f39c12", fg="white", width=8,
                  command=lambda: self.insert_coin(1)).grid(row=0, column=0, padx=6, pady=6)
        tk.Button(coin_frame, text="₱5", font=("Arial", 14, "bold"), bg="#e67e22", fg="white", width=8,
                  command=lambda: self.insert_coin(5)).grid(row=0, column=1, padx=6, pady=6)
        tk.Button(coin_frame, text="₱10", font=("Arial", 14, "bold"), bg="#d35400", fg="white", width=8,
                  command=lambda: self.insert_coin(10)).grid(row=0, column=2, padx=6, pady=6)

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

    def refresh(self):
        uid = self.controller.active_uid
        slot = self.controller.active_slot or "none"
        # Display slot status differently for the slot owner vs other users
        display_text = f"Slot: {slot}"
        display_bg = "#34495e"
        if slot and slot != "none":
            slot_info = read_slot(slot)
            # If the current user is the owner of the slot, show it as In use (yellow)
            if slot_info and slot_info.get("current_user") == uid:
                display_text = f"{slot} In use"
                # remove yellow highlight; use transparent/default background
                display_bg = self.cget('bg')
            else:
                # other users see it as Occupied (red)
                display_text = f"{slot} Occupied"
                display_bg = "#e74c3c"  # red
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
            # keep local remaining in sync when not actively charging
            # If DB reports charging_status == 'charging', ensure local tick loop is running so time continues
            if user.get("charging_status") == "charging":
                # if we are not currently running a local tick, start one so time continues while user navigates
                if not self.is_charging:
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
            messagebox.showwarning("No user", "Scan first.")
            return
        add = COIN_MAP.get(amount, 0)
        user = read_user(uid)
        newbal = (user.get("charge_balance", 0) or 0) + add
        write_user(uid, {"charge_balance": newbal})
        try:
            append_audit_log(actor=uid, action='insert_coin', meta={'amount': amount, 'added_seconds': add, 'new_balance': newbal})
        except Exception:
            pass
        messagebox.showinfo("Coin Added", f"₱{amount} added => {add} seconds.")
        # if currently charging, also update the responsive remaining timer
        if self.is_charging:
            self.remaining += add
            self.time_var.set(str(self.remaining))
        else:
            self.refresh()

    def start_charging(self):
        uid = self.controller.active_uid
        if not uid:
            messagebox.showwarning("No user", "Scan first.")
            return
        user = read_user(uid)
        cb = user.get("charge_balance", 0) or 0
        if cb <= 0:
            messagebox.showwarning("No balance", "Please add coins to charging balance.")
            return
        slot = self.controller.active_slot
        # For hardware-driven slots (slot1), do not mark DB as 'charging' yet because
        # the session must only start when current is detected. Mark 'pending' so
        # other systems know the user requested charging. For non-hardware or fallback
        # start immediately mark as 'charging'.
        hw = getattr(self.controller, 'hw', None)
        try:
            if slot == 'slot1' and hw is not None:
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

        # prepare local state but if hardware available for slot1, enable power and wait for current
        self.db_acc = 0
        self.remaining = cb
        self.time_var.set(str(self.remaining))

        # hw already assigned above; continue with hardware path
        if slot == 'slot1' and hw is not None:
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
            try:
                hw.relay_on('slot1')
            except Exception:
                pass
            # init TM1637 display for countdown (if present)
            try:
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
                hw.lock_slot('slot1', lock=True)
            except Exception:
                pass
            try:
                self.slot_lbl.config(text=f"{slot} - UNLOCKED: please plug in (5s)")
            except Exception:
                pass

            def _end_unlock_and_start_poll():
                try:
                    hw.lock_slot('slot1', lock=False)
                except Exception:
                    pass
                try:
                    self.slot_lbl.config(text=f"{slot} - Waiting for device...")
                except Exception:
                    pass
                # start polling loop to detect charging start
                if self._wait_job is None:
                    try:
                        self._wait_job = self.after(1000, self._poll_for_charging_start)
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
        if not self.is_charging:
            return
        uid = self.controller.active_uid
        if not uid:
            return
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
            # ensure DB shows finished state
            write_user(uid, {"charging_status": "idle"})
            try:
                # set balance to 0 via transaction for safety
                deduct_charge_balance_transactionally(users_ref, uid, t)
            except Exception:
                # fallback to direct write
                write_user(uid, {"charge_balance": 0})
            slot = self.controller.active_slot
            if slot:
                users_ref.child(uid).child("slot_status").update({slot: "active"})
                write_slot(slot, {"status": "active", "current_user": uid})
            messagebox.showinfo("Time Up", "Charging time finished. Please unlock slot to remove your device.")
            self.is_charging = False
            # clear active slot to return UI to initial state
            self.controller.active_slot = None
            self.controller.show_frame(MainScreen)
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
        """Poll current sensor on slot1 until device draws current, then start countdown."""
        self._wait_job = None
        uid = self.controller.active_uid
        slot = self.controller.active_slot
        hw = getattr(self.controller, 'hw', None)
        if not uid or not slot or not hw:
            return
        try:
            cur = hw.read_current('slot1')
            amps = cur.get('amps', 0)
        except Exception:
            amps = 0
        # start threshold (adjustable)
        if amps and amps >= 0.2:
            # detected charging; start countdown
            write_user(uid, {"charging_status": "charging"})
            try:
                append_audit_log(actor=uid, action='charging_detected', meta={'slot': slot, 'amps': amps})
            except Exception:
                pass
            self.is_charging = True
            # ensure remaining synced from DB
            try:
                user = read_user(uid)
                self.remaining = user.get('charge_balance', self.remaining) or self.remaining
            except Exception:
                pass
            # start tick loop and hardware unplug monitor
            if self._tick_job is None:
                self._charging_tick()
            if self._hw_monitor_job is None:
                self._hw_monitor_job = self.after(1000, self._hardware_unplug_monitor)
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
        # keep polling
        try:
            self._wait_job = self.after(1000, self._poll_for_charging_start)
        except Exception:
            self._wait_job = None

    def _hardware_unplug_monitor(self):
        """Monitor the ACS712 reading; if current falls below threshold for UNPLUG_GRACE_SECONDS, stop the session."""
        self._hw_monitor_job = None
        uid = self.controller.active_uid
        slot = self.controller.active_slot
        hw = getattr(self.controller, 'hw', None)
        if not uid or not slot or not hw:
            return
        try:
            cur = hw.read_current('slot1')
            amps = cur.get('amps', 0)
        except Exception:
            amps = 0
        if amps < 0.15:
            if not self.unplug_time:
                self.unplug_time = time.time()
            else:
                if (time.time() - self.unplug_time) >= UNPLUG_GRACE_SECONDS:
                    # treat as unplug event
                    self.stop_session()
                    return
        else:
            self.unplug_time = None
        # reschedule
        try:
            self._hw_monitor_job = self.after(1000, self._hardware_unplug_monitor)
        except Exception:
            self._hw_monitor_job = None

    def _poll_no_detect_timeout(self):
        """Called when no device is detected within the allowed window after unlock."""
        self._poll_timeout_job = None
        uid = self.controller.active_uid
        slot = self.controller.active_slot
        try:
            # ensure DB cleanup and notify user
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
            messagebox.showinfo("No device", "No device detected within the allowed time. Session ended.")
        except Exception:
            pass
        # ensure hardware relays off
        try:
            hw = getattr(self.controller, 'hw', None)
            if hw is not None:
                try:
                    hw.relay_off('slot1')
                except Exception:
                    pass
                try:
                    hw.lock_slot('slot1', lock=False)
                except Exception:
                    pass
        except Exception:
            pass
        # clear UI state
        self.controller.active_slot = None
        self.controller.show_frame(MainScreen)

    def unlock_slot(self):
        uid = self.controller.active_uid
        slot = self.controller.active_slot
        if not uid or not slot:
            messagebox.showwarning("No selection", "No slot assigned.")
            return
        users_ref.child(uid).child("slot_status").update({slot: "inactive"})
        messagebox.showinfo("Unlocked", f"{slot} unlocked. Please unplug your device when ready.")
        # update header info but do not stop charging; unlocking does not equal unplug
        try:
            self.user_info.refresh()
        except Exception:
            pass

    # simulate_unplug removed: hardware path monitors current and handles unplug

    def _check_unplug_grace(self):
        uid = self.controller.active_uid
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
            slot = self.controller.active_slot
            write_user(uid, {"occupied_slot": "none"})
            if slot:
                users_ref.child(uid).child("slot_status").update({slot: "inactive"})
                write_slot(slot, {"status": "inactive", "current_user": "none"})
            messagebox.showinfo("Session Ended", "No device detected. Charging session terminated.")
            # clear active slot so SlotSelectScreen will show coin top again
            self.controller.active_slot = None
            self.controller.show_frame(MainScreen)
            self.unplug_time = None
            self.is_charging = False
            return
        self.after(1000, self._check_unplug_grace)

    def stop_session(self):
        uid = self.controller.active_uid
        slot = self.controller.active_slot
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
                        hw.relay_off('slot1')
                    except Exception:
                        pass
                    try:
                        hw.lock_slot('slot1', lock=False)
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
        messagebox.showinfo("Stopped", "Charging session stopped.")
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

        # cup detection simulation and controls
        btn_frame = tk.Frame(body, bg="#2980b9")
        btn_frame.pack(pady=8)
        tk.Button(btn_frame, text="Simulate Place Cup (Start)", font=("Arial", 14, "bold"),
                  bg="#27ae60", fg="white", width=18, command=self.place_cup).grid(row=0, column=0, padx=6)
        tk.Button(btn_frame, text="Simulate Remove Cup", font=("Arial", 14, "bold"),
                  bg="#f39c12", fg="white", width=18, command=self.remove_cup).grid(row=0, column=1, padx=6)
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
            messagebox.showwarning("No user", "Scan first.")
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
            messagebox.showinfo("Coin Added", f"₱{amount} added to water balance ({add} sec).")
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
            messagebox.showinfo("Coin Added", f"₱{amount} purchased => {add} seconds water (temporary).")
        self.refresh()

    def place_cup(self):
        uid = self.controller.active_uid
        if not uid:
            messagebox.showwarning("No user", "Please scan RFID first.")
            return
        user = read_user(uid)
        if user.get("type") == "member":
            wb = user.get("water_balance", 0) or 0
            if wb <= 0:
                messagebox.showwarning("No balance", "No water balance left. Ask admin.")
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
                messagebox.showwarning("No purchase", "Please buy water with coins first.")
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
            messagebox.showinfo("Session ended", "No cup detected. Water session ended.")
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
        messagebox.showinfo("Stopped", "Water session stopped.")
        self.controller.show_frame(MainScreen)

# ----------------- Run App -----------------
if __name__ == "__main__":
    app = KioskApp()
    app.mainloop()
