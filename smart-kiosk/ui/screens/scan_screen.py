import tkinter as tk
from tkinter import ttk
import logging

_LOGGER = logging.getLogger("ScanScreen")


class ScanScreen(ttk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        main = ttk.Frame(self, padding=40)
        main.grid(row=0, column=0, sticky="nsew")

        title = ttk.Label(main, text="Scan your QR or Enter Number", font=("Arial", 26, "bold"))
        title.pack(pady=20)

        self.input_var = tk.StringVar()
        entry = ttk.Entry(main, textvariable=self.input_var, font=("Arial", 22))
        entry.pack(pady=10)

        # ========= RFID ALWAYS TYPES INTO INPUT FIELD =========
        entry.focus_set()
        self.bind("<Visibility>", lambda *_: entry.focus_set())
        self.bind("<FocusIn>", lambda *_: entry.focus_set())
        self.bind("<Button-1>", lambda *_: entry.focus_set())

        # Pressing Enter triggers submit (RFID readers send Enter)
        entry.bind("<Return>", self._on_submit)

        submit_btn = ttk.Button(main, text="Continue", command=self._on_submit)
        submit_btn.pack(pady=10)

        self.status_label = ttk.Label(main, text="", font=("Arial", 14), foreground="red")
        self.status_label.pack(pady=10)

    # ---------------------------------------------------------
    # SUBMIT LOGIC (NO AUTO-SUBMIT)
    # ---------------------------------------------------------
    def _on_submit(self, *_):
        uid = self.input_var.get().strip()
        if not uid:
            self.status_label.config(text="Please enter a valid ID.")
            return

        self.status_label.config(text="")
        _LOGGER.info(f"ScanScreen: ID entered: {uid}")

        # Try controller-provided get_user
        try:
            user_data = self.controller.get_user(uid)
        except Exception:
            _LOGGER.exception("controller.get_user failed")
            user_data = None

        # Existing user
        if user_data:
            _LOGGER.info(f"Existing user detected: {uid}")
            try:
                self.controller.set_active_user(uid)
                self.controller.append_audit_log(actor=uid, action="login", meta={})
            except Exception:
                pass

            self.controller.show_frame("MainScreen")
            return

        # New user
        _LOGGER.info(f"New user detected: {uid}")

        try:
            self.controller.write_user(uid, {"type": "nonmember"})
            self.controller.set_active_user(uid)
            self.controller.append_audit_log(actor=uid, action="register_start", meta={})
        except Exception:
            pass

        self.controller.show_frame("RegisterChoiceScreen")
