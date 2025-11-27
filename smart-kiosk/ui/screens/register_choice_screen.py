import tkinter as tk
from tkinter import ttk
import logging
import time

_LOGGER = logging.getLogger("RegisterChoiceScreen")


class RegisterChoiceScreen(ttk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller

        self.columnconfigure(0, weight=1)
        main = ttk.Frame(self, padding=40)
        main.grid(row=0, column=0, sticky="nsew")

        title = ttk.Label(main, text="New ID Detected", font=("Arial", 28, "bold"))
        title.pack(pady=20)

        desc = ttk.Label(main, text="Choose how you want to use the kiosk:", font=("Arial", 16))
        desc.pack(pady=10)

        btn_frame = ttk.Frame(main)
        btn_frame.pack(pady=20)

        reg_btn = ttk.Button(btn_frame, text="Register", command=self._on_register)
        reg_btn.grid(row=0, column=0, padx=20, pady=10)

        guest_btn = ttk.Button(btn_frame, text="Use as Guest", command=self._on_guest)
        guest_btn.grid(row=0, column=1, padx=20, pady=10)

        sub_btn = ttk.Button(btn_frame, text="Subscribe", command=self._on_subscribe)
        sub_btn.grid(row=0, column=2, padx=20, pady=10)

        self.status_label = ttk.Label(main, text="", foreground="green", font=("Arial", 14))
        self.status_label.pack(pady=10)

    def _on_register(self):
        uid = self.controller.active_uid
        if not uid:
            self.status_label.config(text="ERROR: No user ID.")
            return

        _LOGGER.info(f"[INFO] Starting registration request for {uid}")

        try:
            # create an admin-facing registration request if users_ref available
            try:
                if getattr(self.controller, "users_ref", None):
                    req_ref = self.controller.users_ref.child("registration_requests").child(uid)
                    ts = int(time.time() * 1000)
                    req_ref.set({
                        "timestamp": ts,
                        "status": "pending",
                        "type": "member"
                    })
            except Exception:
                _LOGGER.exception("failed to create registration_requests entry")

            # Add audit log
            try:
                self.controller.append_audit_log(actor=uid, action="registration_request", meta={"type": "member", "uid": uid})
            except Exception:
                pass

            # Keep base user but mark as requested; do not overwrite to incorrect type
            try:
                self.controller.write_user(uid, {"registration_requested": True})
            except Exception:
                pass

            self.status_label.config(text="Registration request sent ✔")
        except Exception as e:
            _LOGGER.error(f"Registration request failed: {e}")
            self.status_label.config(text="ERROR sending request.")
            return

        # go to main screen (user remains "nonmember" until admin approves)
        try:
            self.controller.show_frame("MainScreen")
        except Exception:
            pass

    def _on_guest(self):
        uid = self.controller.active_uid
        if not uid:
            self.status_label.config(text="ERROR: No user ID.")
            return

        _LOGGER.info(f"[INFO] {uid} now using kiosk as Guest")
        try:
            # persist guest explicitly (so subsequent scans detect it)
            self.controller.write_user(uid, {
                "type": "guest",
                "name": "Guest",
                "charge_balance": 0,
                "water_balance": None,
                "occupied_slot": "none",
                "charging_status": "idle",
            })
        except Exception:
            _LOGGER.exception("write_user for guest failed")

        try:
            self.controller.append_audit_log(actor=uid, action="use_as_guest", meta={"uid": uid})
        except Exception:
            pass

        try:
            self.controller.show_frame("MainScreen")
        except Exception:
            pass

    def _on_subscribe(self):
        uid = self.controller.active_uid
        if not uid:
            self.status_label.config(text="ERROR: No user ID.")
            return

        _LOGGER.info(f"[INFO] Starting subscription request for {uid}")
        try:
            if getattr(self.controller, "users_ref", None):
                try:
                    req_ref = self.controller.users_ref.child("subscription_requests").child(uid)
                    ts = int(time.time() * 1000)
                    req_ref.set({"timestamp": ts, "status": "pending", "type": "subscriber"})
                except Exception:
                    _LOGGER.exception("failed to write subscription request")
            try:
                self.controller.append_audit_log(actor=uid, action="subscription_request", meta={"type": "subscriber", "uid": uid})
            except Exception:
                pass
            # Persist a marker so user won't be treated as "new" on next scan
            try:
                self.controller.write_user(uid, {"subscription_requested": True})
            except Exception:
                pass
            self.status_label.config(text="Subscription request sent ✔")
        except Exception as e:
            _LOGGER.error(f"Subscription request failed: {e}")
            self.status_label.config(text="ERROR sending request.")
            return

        try:
            self.controller.show_frame("MainScreen")
        except Exception:
            pass
