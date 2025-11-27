# ui/screens/slot_select_screen.py
import tkinter as tk
from tkinter import ttk
import time
import logging

_LOGGER = logging.getLogger("SlotSelectScreen")

COIN_MAP = {1: 60, 5: 300, 10: 600}


class SlotSelectScreen(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#34495e")
        self.controller = controller
        self.user_info = None
        try:
            from ui.screens.user_info import UserInfoFrame
            self.user_info = UserInfoFrame(self, controller)
            self.user_info.pack(fill="x")
        except Exception:
            pass

        tk.Label(self, text="Select Charging Slot", font=("Arial", 22, "bold"),
                 fg="white", bg="#34495e").pack(pady=6)

        self.coin_frame_top = tk.LabelFrame(self, text="Coinslot - add charge before slot", font=("Arial", 12, "bold"),
                                            fg="white", bg="#34495e", bd=2, labelanchor="n")
        self.coin_frame_top.pack(pady=6)

        self.coin_status_lbl = tk.Label(self.coin_frame_top, text="", fg="white", bg="#34495e")
        self.coin_status_lbl.grid(row=0, column=0, columnspan=3, pady=(4, 0))

        tk.Button(self.coin_frame_top, text="₱1", font=("Arial", 12, "bold"), bg="#f39c12", fg="white", width=8,
                  command=lambda: self.insert_coin(1)).grid(row=1, column=0, padx=6, pady=6)
        tk.Button(self.coin_frame_top, text="₱5", font=("Arial", 12, "bold"), bg="#e67e22", fg="white", width=8,
                  command=lambda: self.insert_coin(5)).grid(row=1, column=1, padx=6, pady=6)
        tk.Button(self.coin_frame_top, text="₱10", font=("Arial", 12, "bold"), bg="#d35400", fg="white", width=8,
                  command=lambda: self.insert_coin(10)).grid(row=1, column=2, padx=6, pady=6)

        self.slot_buttons = {}
        grid = tk.Frame(self, bg="#34495e")
        grid.pack(pady=8)
        for i in range(1, 5):
            btn = tk.Button(grid, text=f"Slot {i}\n(Checking...)", font=("Arial", 14, "bold"),
                            bg="#95a5a6", fg="black", width=14, height=2,
                            command=lambda s=i: self.select_slot(s))
            btn.grid(row=(i-1)//3, column=(i-1)%3, padx=10, pady=8)
            self.slot_buttons[f"slot{i}"] = btn

        tk.Button(self, text="Back", font=("Arial", 14, "bold"), bg="#c0392b", fg="white",
                  command=lambda: controller.show_frame("MainScreen")).pack(pady=6, anchor='nw', padx=8)

    def refresh(self):
        try:
            if hasattr(self, "user_info") and self.user_info:
                try:
                    self.user_info.refresh()
                except Exception:
                    pass
            uid = self.controller.active_uid
            if not uid:
                try:
                    self.coin_frame_top.pack(pady=6)
                except Exception:
                    pass
            for i in range(1, 5):
                key = f"slot{i}"
                slot = self.controller.get_slot(key) or {}
                text = f"Slot {i}\nFree"
                color = "#2ecc71"
                try:
                    cur = slot.get("current_user", "none")
                    status = slot.get("status", "inactive")
                    if cur != "none":
                        if cur == uid:
                            text = f"Slot {i}\nIn Use"
                            color = "#95a5a6"
                        else:
                            text = f"Slot {i}\nOccupied"
                            color = "#e74c3c"
                    else:
                        if status == "active":
                            text = f"Slot {i}\nIn Use"
                            color = "#e74c3c"
                        else:
                            text = f"Slot {i}\nFree"
                            color = "#2ecc71"
                except Exception:
                    text = f"Slot {i}\nFree"
                    color = "#2ecc71"
                try:
                    self.slot_buttons[key].config(text=text, bg=color)
                except Exception:
                    pass

            uid = self.controller.active_uid
            if uid:
                rec = self.controller.coin_counters.get(uid)
                if rec:
                    val = rec.get('value', 0)
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
        user = self.controller.read_user(uid) or {}
        cb = user.get("charge_balance", 0) or 0
        if (cb or 0) <= 0:
            print("WARN: No charge balance; insert coins before selecting a slot.")
            return
        slot_key = f"slot{i}"
        slot = self.controller.get_slot(slot_key) or {}
        cur = slot.get("current_user", "none")
        status = slot.get("status", "inactive")
        if cur != "none" and cur != uid:
            print(f"WARN: {slot_key} is already assigned to another user.")
            return
        if status == "active" and cur != uid:
            print(f"WARN: {slot_key} is currently in use. Please choose another slot.")
            return
        try:
            self.controller.write_user(uid, {"occupied_slot": slot_key})
        except Exception:
            pass
        try:
            if getattr(self.controller, "users_ref", None):
                try:
                    self.controller.users_ref.child(uid).child("slot_status").update({slot_key: "inactive"})
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if getattr(self.controller, "users_ref", None):
                try:
                    self.controller.users_ref.child("slots").child(slot_key).update({"status": "inactive", "current_user": uid})
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.controller.append_audit_log(actor=uid, action='assign_slot', meta={'slot': slot_key})
        except Exception:
            pass
        self.controller.set_active_slot(slot_key)
        try:
            self.coin_frame_top.pack_forget()
        except Exception:
            pass
        print(f"INFO: You selected {slot_key}. Please plug your device and press Start Charging.")
        self.controller.show_frame("ChargingScreen")

    def insert_coin(self, amount):
        uid = self.controller.active_uid
        if not uid:
            print("WARN: No user; scan first.")
            return
        add = COIN_MAP.get(amount, 0)
        user = self.controller.read_user(uid) or {}
        newbal = (user.get("charge_balance", 0) or 0) + add
        try:
            self.controller.write_user(uid, {"charge_balance": newbal})
        except Exception:
            pass
        try:
            self.controller.append_audit_log(actor=uid, action='insert_coin', meta={'amount': amount, 'added_seconds': add, 'new_balance': newbal})
        except Exception:
            pass
        print(f"INFO: ₱{amount} added => {add} seconds to charging balance.")
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
