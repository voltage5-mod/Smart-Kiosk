# ui/screens/water_screen.py
import tkinter as tk
from tkinter import ttk
import time
import re
import logging

_LOGGER = logging.getLogger("WaterScreen")

WATER_COIN_MAP = {1: 250, 5: 1250, 10: 2500}
NO_CUP_TIMEOUT = 10
WATER_DB_WRITE_INTERVAL = 3


class WaterScreen(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#2980b9")
        self.controller = controller
        self.user_info = None
        try:
            from ui.screens.user_info import UserInfoFrame
            self.user_info = UserInfoFrame(self, controller)
            self.user_info.pack(fill="x")
        except Exception:
            pass

        body = tk.Frame(self, bg="#2980b9")
        body.pack(expand=True, pady=12)
        tk.Label(body, text="Water Dispensing", font=("Arial", 22, "bold"), fg="white", bg="#2980b9").pack(pady=6)
        self.status_lbl = tk.Label(body, text="Place cup to start", font=("Arial", 16), fg="white", bg="#2980b9")
        self.status_lbl.pack(pady=6)
        self.time_var = tk.StringVar(value="0")
        tk.Label(body, text="Time (sec)", font=("Arial", 14), fg="white", bg="#2980b9").pack()
        tk.Label(body, textvariable=self.time_var, font=("Arial", 28, "bold"), fg="white", bg="#2980b9").pack(pady=6)

        hw_present = getattr(self.controller, 'hw', None) is not None
        self.hw_label = tk.Label(body,
                                 text="🔗 Hardware sensors active" if hw_present else "⚠ Simulation mode (no hardware)",
                                 fg="#27ae60" if hw_present else "#f39c12", bg="#2980b9", font=("Arial", 12))
        self.hw_label.pack(pady=4)

        btn_frame = tk.Frame(body, bg="#2980b9")
        btn_frame.pack(pady=8)
        tk.Button(btn_frame, text="Stop Session", font=("Arial", 12, "bold"),
                  bg="#c0392b", fg="white", width=20, command=self.stop_session).pack(pady=8)

        if not hw_present:
            coin_frame = tk.LabelFrame(body, text="Coinslot (simulation) - water (non-members only)", font=("Arial", 12, "bold"),
                                       fg="white", bg="#2980b9", bd=2, labelanchor="n")
            coin_frame.pack(pady=10)
            tk.Button(coin_frame, text="₱1", font=("Arial", 14, "bold"), bg="#f39c12", fg="white", width=8,
                      command=lambda: self.insert_coin_water(1)).grid(row=0, column=0, padx=6, pady=6)

            cup_sim_frame = tk.LabelFrame(body, text="Cup Simulation (for testing)", font=("Arial", 12, "bold"),
                                          fg="white", bg="#2980b9", bd=2, labelanchor="n")
            cup_sim_frame.pack(pady=10)
            tk.Button(cup_sim_frame, text="Simulate Place Cup", font=("Arial", 12, "bold"),
                      bg="#27ae60", fg="white", width=18, command=self.place_cup).grid(row=0, column=0, padx=6, pady=6)
            tk.Button(cup_sim_frame, text="Simulate Remove Cup", font=("Arial", 12, "bold"),
                      bg="#f39c12", fg="white", width=18, command=self.remove_cup).grid(row=0, column=1, padx=6, pady=6)

        self.cup_present = False
        self.last_cup_time = None
        self.temp_water_time = 0
        self._water_job = None
        self._water_nocup_job = None
        self._water_db_acc = 0
        self._water_remaining = 0
        self._last_coin_ts = {}
        self.total_coins = 0
        self.total_credit = 0

    def refresh(self):
        try:
            if self.user_info:
                self.user_info.refresh()
        except Exception:
            pass
        uid = self.controller.active_uid
        if not uid:
            self.time_var.set("0")
            self.status_lbl.config(text="Place cup to start")
            return
        user = self.controller.read_user(uid) or {}
        if user.get("type") == "member":
            wb = user.get("water_balance", 0) or 0
            self.time_var.set(str(wb))
            self.status_lbl.config(text="Place cup to start")
        else:
            temp = user.get("temp_water_time", None)
            if temp is None:
                temp = self.temp_water_time
            self.temp_water_time = temp
            self.time_var.set(str(temp))
            if (temp or 0) <= 0:
                self.status_lbl.config(text="Non-member: buy water with coins")
            else:
                self.status_lbl.config(text="Place cup to start (Purchased time)")

    def handle_arduino_event(self, event, value):
        try:
            ev = (event or '').strip()
            if ev == 'COIN_CHARGE':
                try:
                    peso = int(value)
                except Exception:
                    peso = 0
                add = 0
                try:
                    add = WATER_COIN_MAP.get(peso, 0)
                except Exception:
                    add = 0
                try:
                    uid = self.controller.active_uid
                    if not uid:
                        uid = None
                    if not uid:
                        return
                    user = self.controller.read_user(uid) or {}
                    if user.get('type') == 'member':
                        cur = user.get('water_balance', 0)
                        self.controller.write_user(uid, {'water_balance': cur + add})
                    else:
                        cur = user.get('temp_water_time', 0)
                        self.controller.write_user(uid, {'temp_water_time': cur + add})
                    try:
                        self.controller.append_audit_log(actor=uid, action='insert_coin_water', meta={'amount': peso, 'added_ml': add})
                    except Exception:
                        pass
                    if self.cup_present:
                        try:
                            self._water_remaining += add
                            self.time_var.set(str(self._water_remaining))
                        except Exception:
                            pass
                    try:
                        self.controller.record_coin_insert(uid, peso, add)
                    except Exception:
                        pass
                    try:
                        self.controller.show_coin_popup(uid, peso=peso, added_ml=add, total_ml=None)
                    except Exception:
                        pass
                    try:
                        self.controller.refresh_all_user_info()
                    except Exception:
                        pass
                except Exception:
                    pass
            # handle other events minimally (CUP_DETECTED, COIN_WATER etc.)
        except Exception:
            pass

    def insert_coin_water(self, amount, record=True):
        uid = self.controller.active_uid
        if not uid:
            print("WARN: No user; scan first.")
            return
        add = WATER_COIN_MAP.get(amount, 0)
        user = self.controller.read_user(uid) or {}
        if user.get("type") == "member":
            new = (user.get("water_balance", 0) or 0) + add
            try:
                self.controller.write_user(uid, {"water_balance": new})
            except Exception:
                pass
            try:
                self.controller.append_audit_log(actor=uid, action='insert_coin_water', meta={'amount': amount, 'added_ml': add, 'new_water_balance_ml': new})
            except Exception:
                pass
            print(f"INFO: ₱{amount} added to water balance ({add} mL).")
            if record:
                try:
                    self.total_coins += int(amount)
                    self.total_credit += int(add)
                except Exception:
                    pass
                try:
                    self.controller.show_coin_popup(uid, peso=None, added_ml=add, total_ml=self.total_credit)
                except Exception:
                    pass
                try:
                    self.controller.show_totals_popup(uid, self.total_coins, self.total_credit)
                except Exception:
                    pass
        else:
            prev = user.get("temp_water_time", 0) or 0
            newtemp = prev + add
            self.temp_water_time = newtemp
            try:
                self.controller.write_user(uid, {"temp_water_time": newtemp})
            except Exception:
                pass
            try:
                self.controller.append_audit_log(actor=uid, action='purchase_water', meta={'amount': amount, 'added_ml': add, 'new_temp_ml': newtemp})
            except Exception:
                pass
            print(f"INFO: ₱{amount} purchased => {add} mL water (temporary).")
            if record:
                try:
                    self.total_coins += int(amount)
                    self.total_credit += int(add)
                except Exception:
                    pass
                try:
                    self.controller.show_coin_popup(uid, peso=None, added_ml=add, total_ml=self.total_credit)
                except Exception:
                    pass
                try:
                    self.controller.show_totals_popup(uid, self.total_coins, self.total_credit)
                except Exception:
                    pass
        self.refresh()

    def reset_totals(self):
        try:
            self.total_coins = 0
            self.total_credit = 0
        except Exception:
            pass

    def _notify_arduino_stop(self):
        try:
            al = getattr(self.controller, 'arduino_listener', None)
            if al is None:
                return
            try:
                al.send_command('RESET')
            except Exception:
                pass
            try:
                al.send_command('MODE CHARGE')
            except Exception:
                pass
            try:
                print('INFO: Sent RESET and MODE CHARGE to Arduino to stop water sensing')
            except Exception:
                pass
        except Exception:
            pass

    def place_cup(self):
        uid = self.controller.active_uid
        if not uid:
            print("WARN: No user; please scan RFID first.")
            return
        user = self.controller.read_user(uid) or {}
        if user.get("type") == "member":
            wb = user.get("water_balance", 0) or 0
            if wb <= 0:
                print("WARN: No water balance left. Ask admin.")
                return
            self.cup_present = True
            self.last_cup_time = time.time()
            self.status_lbl.config(text="Dispensing...")
            self._water_remaining = wb
            self.time_var.set(str(self._water_remaining))
            self._water_db_acc = 0
            if self._water_job is None:
                self._water_job = self.after(1000, self._water_tick_member)
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
            self._water_remaining = self.temp_water_time
            self.time_var.set(str(self._water_remaining))
            if self._water_job is None:
                self._water_job = self.after(1000, self._water_tick_nonmember)
            if getattr(self, '_water_nocup_job', None) is not None:
                try:
                    self.after_cancel(self._water_nocup_job)
                except Exception:
                    pass
                self._water_nocup_job = None

    def _water_tick_member(self):
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
            self.controller.show_frame("MainScreen")
            return
        self._water_remaining -= 1
        self.time_var.set(str(self._water_remaining))
        self._water_db_acc += 1
        if self._water_db_acc >= WATER_DB_WRITE_INTERVAL:
            try:
                self.controller.write_user(uid, {"water_balance": self._water_remaining})
            except Exception:
                pass
            self._water_db_acc = 0
        self._water_job = self.after(1000, self._water_tick_member)

    def _water_tick_nonmember(self):
        if not self.cup_present:
            self.last_cup_time = time.time()
            self._water_job = None
            self._water_no_cup_check()
            return
        if self._water_remaining <= 0:
            self.status_lbl.config(text="Purchased time finished")
            self.cup_present = False
            self._water_job = None
            try:
                uid = self.controller.active_uid
                if uid:
                    self.controller.write_user(uid, {"temp_water_time": 0})
                    try:
                        self.controller.append_audit_log(actor=uid, action='temp_water_expired', meta={'uid': uid})
                    except Exception:
                        pass
                    try:
                        if hasattr(self.controller, 'coin_counters') and uid in self.controller.coin_counters:
                            del self.controller.coin_counters[uid]
                    except Exception:
                        pass
            except Exception:
                pass
            self.temp_water_time = 0
            self.controller.show_frame("MainScreen")
            return
        self._water_remaining -= 1
        self.time_var.set(str(self._water_remaining))
        self.temp_water_time = self._water_remaining
        self._water_job = self.after(1000, self._water_tick_nonmember)

    def remove_cup(self):
        self.cup_present = False
        self.status_lbl.config(text="Cup removed - waiting (10s) to auto-end")
        self.last_cup_time = time.time()
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
        try:
            self._water_nocup_job = self.after(1000, self._water_no_cup_check)
        except Exception:
            self._water_nocup_job = None

    def _water_no_cup_check(self):
        if self.cup_present:
            return
        elapsed = time.time() - (self.last_cup_time or time.time())
        if elapsed >= NO_CUP_TIMEOUT:
            if getattr(self, '_water_nocup_job', None) is not None:
                try:
                    self.after_cancel(self._water_nocup_job)
                except Exception:
                    pass
                self._water_nocup_job = None
            try:
                uid = self.controller.active_uid
                user = self.controller.read_user(uid) if uid else None
                if user and user.get('type') != 'member':
                    self.controller.write_user(uid, {"temp_water_time": 0})
                    try:
                        self.controller.append_audit_log(actor=uid, action='temp_water_reset_on_timeout', meta={'uid': uid})
                    except Exception:
                        pass
                    self.temp_water_time = 0
                    try:
                        if hasattr(self.controller, 'coin_counters') and uid in self.controller.coin_counters:
                            del self.controller.coin_counters[uid]
                    except Exception:
                        pass
            except Exception:
                pass
            print("INFO: No cup detected. Water session ended.")
            try:
                self._notify_arduino_stop()
            except Exception:
                pass
            try:
                self.reset_totals()
            except Exception:
                pass
            self.controller.show_frame("MainScreen")
            return
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
        user = self.controller.read_user(uid) if uid else None
        if user and user.get("type") == "member":
            try:
                val = int(self.time_var.get())
            except:
                val = user.get("water_balance", 0) or 0
            try:
                if getattr(self.controller, "users_ref", None):
                    self.controller.users_ref.child(uid).update({"water_balance": val})
            except Exception:
                pass
            try:
                self.controller.append_audit_log(actor=uid, action='stop_water_session', meta={'water_balance': val})
            except Exception:
                pass
        if user and user.get("type") != "member":
            try:
                self.controller.write_user(uid, {"temp_water_time": 0})
                try:
                    self.controller.append_audit_log(actor=uid, action='reset_temp_water', meta={'uid': uid})
                except Exception:
                    pass
                try:
                    if hasattr(self.controller, 'coin_counters') and uid in self.controller.coin_counters:
                        del self.controller.coin_counters[uid]
                except Exception:
                    pass
            except Exception:
                pass
        self.temp_water_time = 0
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
        try:
            self.reset_totals()
        except Exception:
            pass
        try:
            self._notify_arduino_stop()
        except Exception:
            pass
        print("INFO: Water session stopped.")
        self.controller.show_frame("MainScreen")
