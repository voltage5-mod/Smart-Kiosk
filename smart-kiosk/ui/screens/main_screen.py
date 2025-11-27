import tkinter as tk
from tkinter import ttk
import time
import logging

_LOGGER = logging.getLogger("MainScreen")

# minimal UserInfoFrame import fallback handled in file if needed
try:
    from ui.screens.user_info import UserInfoFrame
except Exception:
    class UserInfoFrame(ttk.Frame):
        def __init__(self, parent, controller):
            super().__init__(parent)
        def refresh(self): pass

class MainScreen(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, bg="#34495e")
        self.controller = controller
        self.user_info = UserInfoFrame(self, controller)
        self.user_info.pack(fill="x")

        body = tk.Frame(self, bg="#34495e")
        body.pack(fill="both", expand=True, pady=8)
        tk.Label(body, text="Select Service", font=("Arial", 24, "bold"), fg="white", bg="#34495e").pack(pady=12)
        btn_frame = tk.Frame(body, bg="#34495e")
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="Water Vendo", font=("Arial", 18, "bold"),
                  bg="#2980b9", fg="white", width=20, height=2, command=lambda: controller.show_frame("WaterScreen")).grid(row=0, column=0, padx=10, pady=8)
        tk.Button(btn_frame, text="Phone Charging", font=("Arial", 18, "bold"),
                  bg="#27ae60", fg="white", width=20, height=2, command=lambda: controller.show_frame("SlotSelectScreen")).grid(row=0, column=1, padx=10, pady=8)

        self.register_small = tk.Button(self, text="Register as Member", font=("Arial", 10, "underline"),
                                        fg="white", bg="#34495e", bd=0, command=self.goto_register)
        self.unlock_my_slot = tk.Button(self, text="", font=("Arial", 12, "bold"),
                                        bg="#f39c12", fg="white", command=self._unlock_my_slot)
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
        user = self.controller.get_user(uid) or {}
        if user.get("type") == "member":
            print("INFO: User is already a member.")
            return
        try:
            req_ref = None
            if hasattr(self.controller, "users_ref") and self.controller.users_ref:
                req_ref = self.controller.users_ref.child(f"registration_requests/{uid}")
            # fallback: just log locally
            ts = int(time.time() * 1000)
            if req_ref is not None:
                existing = req_ref.get()
                if existing and existing.get('status') == 'pending':
                    print("INFO: Registration request already pending.")
                    return
                req_ref.set({'timestamp': ts, 'status': 'pending'})
            self.controller.append_audit_log(actor=uid, action='registration_request', meta={'ts': ts, 'uid': uid})
            print("INFO: Registration request submitted.")
            self.register_small.config(text='Registration Requested', state='disabled')
        except Exception as e:
            print('Error submitting registration request:', e)
            print('ERROR: Failed to submit registration request. Please try again later.')

    def logout(self):
        self.controller.clear_active_user()
        self.controller.show_frame("ScanScreen")

    def refresh(self):
        try:
            self.user_info.refresh()
        except Exception:
            pass
        uid = self.controller.active_uid
        if uid:
            user = self.controller.get_user(uid) or {}
            occ = user.get("occupied_slot", "none") if user else "none"
            if not occ:
                occ = "none"
            if occ != "none":
                self.controller.active_slot = occ
                self.unlock_my_slot.config(text=f"Unlock {occ}")
                try:
                    self.unlock_my_slot.pack(side="bottom", pady=4)
                    self.end_session_btn.pack(side="bottom", pady=4)
                except Exception:
                    pass
            else:
                try:
                    self.unlock_my_slot.pack_forget()
                    self.end_session_btn.pack_forget()
                except Exception:
                    pass
        else:
            try:
                self.unlock_my_slot.pack_forget()
            except Exception:
                pass

    def _end_charging_session(self):
        uid = self.controller.active_uid
        if not uid:
            print("WARN: No user; scan first.")
            return
        # attempt to stop session via ChargingScreen instance
        try:
            charging_frame = self.controller.frames.get("ChargingScreen")
            if charging_frame:
                charging_frame.stop_session()
                return
        except Exception:
            pass
        user = self.controller.get_user(uid) or {}
        slot = (user.get("occupied_slot") if user else None) or "none"
        try:
            self.controller.set_user(uid, {"charging_status": "idle", "occupied_slot": "none"})
            if slot != "none":
                self.controller.set_slot(slot, {"status": "inactive", "current_user": "none"})
                if hasattr(self.controller, "users_ref") and self.controller.users_ref:
                    self.controller.users_ref.child(uid).child("slot_status").update({slot: "inactive"})
        except Exception:
            pass
        self.controller.clear_active_slot()
        print("INFO: Charging session ended.")

    def _unlock_my_slot(self):
        uid = self.controller.active_uid
        if not uid:
            print("WARN: No user; scan first.")
            return
        user = self.controller.get_user(uid) or {}
        slot = user.get("occupied_slot", "none") or "none"
        if slot == "none":
            print("INFO: No slot assigned to this user.")
            try:
                self.unlock_my_slot.pack_forget()
            except Exception:
                pass
            return
        try:
            self.controller.set_slot(slot, {"status": "inactive", "current_user": "none"})
            if hasattr(self.controller, "users_ref") and self.controller.users_ref:
                self.controller.users_ref.child(uid).child("slot_status").update({slot: "inactive"})
            self.controller.set_user(uid, {"occupied_slot": "none"})
            print(f"INFO: {slot} unlocked. You may unplug your device.")
            self.controller.clear_active_slot()
            try:
                self.unlock_my_slot.pack_forget()
            except Exception:
                pass
        except Exception:
            pass
