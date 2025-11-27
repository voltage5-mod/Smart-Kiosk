import tkinter as tk
import time
import math
import statistics
import logging

_LOGGER = logging.getLogger("ChargingScreen")
try:
    from ui.screens.user_info import UserInfoFrame
except Exception:
    class UserInfoFrame(tk.Frame):
        def __init__(self, p, c): super().__init__(p)
        def refresh(self): pass

# thresholds & constants fallback
PLUG_THRESHOLD = 0.05
PLUG_CONFIRM_WINDOW = 3.0
PLUG_CONFIRM_COUNT = 3
UNPLUG_GRACE_SECONDS = 60
CHARGE_DB_WRITE_INTERVAL = 5

class ChargingScreen(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#34495e")
        self.controller = controller
        self.user_info = UserInfoFrame(self, controller)
        self.user_info.pack(fill="x")

        body = tk.Frame(self, bg="#34495e")
        body.pack(expand=True, fill='both', pady=12)

        self.slot_lbl = tk.Label(body, text="Charging Slot -", font=("Arial", 28, "bold"), fg="white", bg="#34495e")
        self.slot_lbl.pack(pady=(20, 12))

        self.time_var = tk.StringVar(value="0")
        tk.Label(body, text="Time Left (sec)", font=("Arial", 14), fg="white", bg="#34495e").pack(pady=(6, 2))
        tk.Label(body, textvariable=self.time_var, font=("Arial", 28, "bold"), fg="white", bg="#34495e").pack(pady=(0, 12))

        btn_frame = tk.Frame(body, bg="#34495e")
        btn_frame.pack(pady=8)
        tk.Button(btn_frame, text="Back", font=("Arial", 12, "bold"),
                  bg="#95a5a6", fg="white", width=10, command=lambda: controller.show_frame("MainScreen")).grid(row=0, column=0, padx=6)
        tk.Button(btn_frame, text="Start Charging", font=("Arial", 14, "bold"),
                  bg="#2980b9", fg="white", width=14, command=self.start_charging).grid(row=0, column=1, padx=6)
        tk.Button(btn_frame, text="Unlock Slot", font=("Arial", 14, "bold"),
                  bg="#f39c12", fg="white", width=14, command=self.unlock_slot).grid(row=0, column=2, padx=6)
        tk.Button(btn_frame, text="Stop Session", font=("Arial", 12, "bold"),
                  bg="#c0392b", fg="white", width=14, command=self.stop_session).grid(row=0, column=3, padx=6)

        # state
        self.db_acc = 0
        self.is_charging = False
        self.unplug_time = None
        self.remaining = 0
        self._tick_job = None
        self.tm = None
        self._wait_job = None
        self._hw_monitor_job = None
        self._poll_timeout_job = None
        self._plug_hits = []
        self._unplug_hits = []
        self.charging_uid = None
        self.charging_slot = None
        self._session_valid = False
        self._session_id = 0
        self._current_session_id = None

    def _get_session_uid(self):
        uid = getattr(self, 'charging_uid', None) or getattr(self.controller, 'active_uid', None)
        if uid:
            return uid
        slot = getattr(self.controller, 'active_slot', None)
        if slot:
            s = self.controller.get_slot(slot)
            if s:
                return s.get('current_user')
        return None

    def refresh(self):
        uid = self.controller.active_uid
        slot = self.controller.active_slot or "none"
        if uid and self.charging_uid and uid != self.charging_uid:
            # invalidate session
            self._session_valid = False
            self._cancel_all_timers()
            self.is_charging = False
            self.charging_uid = None
            self.charging_slot = None
            self.remaining = 0
            self.db_acc = 0
            self.unplug_time = None
            try:
                self.time_var.set("0")
            except Exception:
                pass

        display_text = f"Charging Slot {slot[4:] if slot and slot.startswith('slot') else slot}"
        self.slot_lbl.config(text=display_text)
        try:
            self.user_info.refresh()
        except Exception:
            pass

        if uid:
            user = self.controller.get_user(uid) or {}
            cb = user.get("charge_balance", 0) or 0
            self.time_var.set(str(cb))
            if user.get("charging_status") == "charging" and self.charging_uid == uid:
                if not self.is_charging:
                    self._session_valid = True
                    self.is_charging = True
                    self.remaining = cb
                    self.db_acc = 0
                    if self._tick_job is None:
                        self._charging_tick()
                else:
                    if self._tick_job is None:
                        self.remaining = cb
            else:
                if not self.is_charging and self._tick_job is None:
                    self.remaining = cb
        else:
            self.time_var.set("0")

    def insert_coin(self, amount):
        uid = self.controller.active_uid
        if not uid:
            print("WARN: No user; scan first.")
            return
        COIN_MAP = {1: 60, 5: 300, 10: 600}
        add = COIN_MAP.get(amount, 0)
        user = self.controller.get_user(uid) or {}
        newbal = (user.get("charge_balance", 0) or 0) + add
        self.controller.set_user(uid, {"charge_balance": newbal})
        try:
            self.controller.append_audit_log(actor=uid, action='insert_coin', meta={'amount': amount, 'added_seconds': add, 'new_balance': newbal})
        except Exception:
            pass
        print(f"INFO: ₱{amount} added => {add} seconds.")
        if self.is_charging:
            self.remaining += add
            self.time_var.set(str(self.remaining))
            try:
                sm = getattr(self.controller, 'session_manager', None)
                slot = self.controller.active_slot
                if sm and slot and slot in sm.sessions:
                    sm.sessions[slot]['remaining'] = sm.sessions[slot].get('remaining', 0) + add
            except Exception:
                pass
        else:
            self.refresh()
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
        user = self.controller.get_user(uid) or {}
        cb = user.get("charge_balance", 0) or 0
        if cb <= 0:
            print("WARN: No charge balance; please add coins.")
            return
        slot = self.controller.active_slot
        self.charging_uid = uid
        self._session_valid = True
        self._session_id += 1
        self._current_session_id = self._session_id
        self.charging_slot = slot

        try:
            hw = getattr(self.controller, 'hw', None)
            hw_supported = bool(slot and hw is not None and slot in (getattr(hw, 'pinmap', {}).get('acs712_channels', {})))
            if hw_supported:
                self.controller.set_user(uid, {"charging_status": "pending"})
            else:
                self.controller.set_user(uid, {"charging_status": "charging"})
        except Exception:
            pass

        try:
            self.controller.append_audit_log(actor=uid, action='start_charging', meta={'slot': slot})
        except Exception:
            pass

        if slot:
            try:
                if hasattr(self.controller, "users_ref") and self.controller.users_ref:
                    self.controller.users_ref.child(uid).child("slot_status").update({slot: "active"})
            except Exception:
                pass
            try:
                self.controller.set_slot(slot, {"status": "active", "current_user": uid})
            except Exception:
                pass

        self.db_acc = 0
        self.remaining = cb
        self.time_var.set(str(self.remaining))

        # Hardware path simplified: if no hw then start tick immediately
        if slot and getattr(self.controller, 'hw', None) is None:
            self.is_charging = True
            if self._tick_job is None:
                self._charging_tick()
            return

        # If hardware exists, rely on hardware monitor flow (not fully implemented here)
        self.is_charging = True
        if self._tick_job is None:
            self._charging_tick()

    def _charging_tick(self):
        self._tick_job = None
        if not getattr(self, '_session_valid', False):
            return
        if not self.is_charging:
            return
        uid = self._get_session_uid()
        if not uid:
            return
        t = self.remaining
        if t <= 0:
            if self._tick_job is not None:
                try:
                    self.after_cancel(self._tick_job)
                except Exception:
                    pass
                self._tick_job = None
            try:
                self.controller.set_user(uid, {"charging_status": "idle", "charge_balance": 0, "occupied_slot": "none"})
            except Exception:
                pass
            slot = self.charging_slot or self.controller.active_slot
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
                    self.controller.set_slot(slot, {"status": "inactive", "current_user": "none"})
                    if hasattr(self.controller, "users_ref") and self.controller.users_ref and uid:
                        self.controller.users_ref.child(uid).child("slot_status").update({slot: "inactive"})
                except Exception:
                    pass
            try:
                self.controller.append_audit_log(actor=uid, action='charging_finished', meta={'slot': slot})
            except Exception:
                pass
            print("INFO: Charging time finished; session ended.")
            self.is_charging = False
            try:
                self.charging_uid = None
            except Exception:
                pass
            try:
                if self.controller.active_slot == slot:
                    self.controller.active_slot = None
            except Exception:
                self.controller.active_slot = None
            self.controller.show_frame("MainScreen")
            return

        self.remaining = max(0, t - 1)
        self.time_var.set(str(self.remaining))
        self.db_acc += 1
        if self.db_acc >= CHARGE_DB_WRITE_INTERVAL:
            try:
                prev = (self.controller.get_user(uid) or {}).get('charge_balance', 0) or 0
                delta = max(0, prev - self.remaining)
                if delta > 0 and hasattr(self.controller, 'billing_service') and self.controller.billing_service and hasattr(self.controller.billing_service, 'deduct_seconds'):
                    try:
                        self.controller.billing_service.deduct_seconds(uid, delta)
                    except Exception:
                        self.controller.set_user(uid, {"charge_balance": self.remaining})
                else:
                    if hasattr(self.controller, "users_ref") and self.controller.users_ref:
                        self.controller.users_ref.child(uid).update({"charge_balance": self.remaining})
            except Exception:
                try:
                    if hasattr(self.controller, "users_ref") and self.controller.users_ref:
                        self.controller.users_ref.child(uid).update({"charge_balance": self.remaining})
                except Exception:
                    pass
            self.db_acc = 0

        try:
            self._tick_job = self.after(1000, self._charging_tick)
        except Exception:
            self._tick_job = None

    def _cancel_all_timers(self):
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

    def unlock_slot(self):
        uid = self._get_session_uid()
        slot = self.charging_slot or self.controller.active_slot
        if not uid or not slot:
            print("WARN: No slot assigned.")
            return
        try:
            if hasattr(self.controller, "users_ref") and self.controller.users_ref:
                self.controller.users_ref.child(uid).child("slot_status").update({slot: "inactive"})
        except Exception:
            pass
        print(f"INFO: {slot} unlocked. Please unplug your device when ready.")
        try:
            self.user_info.refresh()
        except Exception:
            pass

    def stop_session(self):
        uid = self._get_session_uid()
        slot = self.charging_slot or self.controller.active_slot
        if uid:
            try:
                if self._tick_job is not None:
                    self.after_cancel(self._tick_job)
                    self._tick_job = None
            except Exception:
                pass
            try:
                self.controller.set_user(uid, {"charging_status": "idle"})
                self.controller.set_user(uid, {"occupied_slot": "none"})
                if slot:
                    self.controller.set_slot(slot, {"status": "inactive", "current_user": "none"})
                    if hasattr(self.controller, "users_ref") and self.controller.users_ref:
                        self.controller.users_ref.child(uid).child("slot_status").update({slot: "inactive"})
                    self.controller.active_slot = None
            except Exception:
                pass
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
            try:
                if self._wait_job is not None:
                    self.after_cancel(self._wait_job)
                    self._wait_job = None
                if self._hw_monitor_job is not None:
                    self.after_cancel(self._hw_monitor_job)
                    self._hw_monitor_job = None
                self.tm = None
                self.unplug_time = None
            except Exception:
                pass
            try:
                self.controller.append_audit_log(actor=uid, action='stop_charging', meta={'slot': slot})
            except Exception:
                pass

        try:
            self.charging_uid = None
        except Exception:
            pass
        try:
            self.charging_slot = None
        except Exception:
            pass
        try:
            self._session_valid = False
        except Exception:
            pass
        print("INFO: Charging session stopped.")
        self.controller.show_frame("MainScreen")
