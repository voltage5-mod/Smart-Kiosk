# kiosk_app.py
import tkinter as tk
from tkinter import messagebox
import importlib
import logging
import sys

_LOGGER = logging.getLogger("KioskApp")


class KioskApp(tk.Tk):
    """
    Central UI controller.
    Loads screens dynamically and exposes helper methods for:
    - read_user / write_user
    - read_slot / write_slot
    - audit log
    - active user & slot tracking
    """

    def __init__(self, screen_modules=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.title("Smart Kiosk")
        self.geometry("1024x600")

        # -------------------------
        # Controller State
        # -------------------------
        self.active_uid: str | None = None
        self.active_slot: str | None = None
        self.current_frame = None

        self.coin_counters = {}
        self.frames = {}

        # will be assigned from __main__
        self.read_user = None
        self.write_user = None
        self.read_slot = None
        self.write_slot = None
        self.users_ref = None
        self.append_audit_log = None

        self._wire_main_functions()

        # -------------------------
        # Build container for screens
        # -------------------------
        container = tk.Frame(self)
        container.pack(side="top", fill="both", expand=True)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        # -------------------------
        # Screen modules to load
        # -------------------------
        if screen_modules is None:
            screen_modules = {
                "ui.screens.scan_screen": "ScanScreen",
                "ui.screens.user_info": "UserInfoFrame",
                "ui.screens.main_screen": "MainScreen",
                "ui.screens.slot_select_screen": "SlotSelectScreen",
                "ui.screens.charging_screen": "ChargingScreen",
                "ui.screens.water_screen": "WaterScreen",
                "ui.screens.register_choice_screen": "RegisterChoiceScreen"
            }

        # -------------------------
        # Load Screens Dynamically
        # -------------------------
        for mod_path, cls_name in screen_modules.items():
            try:
                mod = importlib.import_module(mod_path)
                cls = getattr(mod, cls_name, None)

                if cls is None:
                    _LOGGER.warning("Module %s missing class %s", mod_path, cls_name)
                    continue

                frame = cls(container, self)
                self.frames[cls_name] = frame
                frame.grid(row=0, column=0, sticky="nsew")
                _LOGGER.info("Loaded screen %s from %s", cls_name, mod_path)

            except Exception as e:
                _LOGGER.error("Failed loading %s (%s): %s", mod_path, cls_name, e)

        # -------------------------
        # Show first screen
        # -------------------------
        if "ScanScreen" in self.frames:
            self.show_frame("ScanScreen")
        else:
            first = next(iter(self.frames), None)
            if first:
                self.show_frame(first)
            else:
                messagebox.showerror("Startup Error", "No screens could be loaded.")
                self.destroy()
                sys.exit(1)

    # =========================================================
    # MAIN-MODULE WIRING
    #=========================================================
    def _wire_main_functions(self):
        """
        Inject read_user, write_user, users_ref, read_slot, write_slot, append_audit_log
        from the __main__ module (main.py).
        """
        main_mod = sys.modules.get("__main__")

        # read_user
        if hasattr(main_mod, "read_user"):
            self.read_user = main_mod.read_user
        else:
            self.read_user = lambda uid: None

        # write_user
        if hasattr(main_mod, "write_user"):
            self.write_user = main_mod.write_user
        else:
            self.write_user = lambda uid, data: None

        # slot read/write
        if hasattr(main_mod, "read_slot"):
            self.read_slot = main_mod.read_slot
        else:
            self.read_slot = lambda slot: None

        if hasattr(main_mod, "write_slot"):
            self.write_slot = main_mod.write_slot
        else:
            self.write_slot = lambda slot, data: None

        # users_ref
        if hasattr(main_mod, "users_ref"):
            self.users_ref = main_mod.users_ref
        else:
            self.users_ref = None

        # audit log
        if hasattr(main_mod, "append_audit_log"):
            self.append_audit_log = main_mod.append_audit_log
        else:
            self.append_audit_log = lambda **k: None

    # =========================================================
    # NAVIGATION
    #=========================================================
    def show_frame(self, screen: str, **kwargs):
        """Show a UI screen by class name."""
        try:
            frame = self.frames.get(screen)
            if not frame:
                _LOGGER.warning("show_frame: %s not found", screen)
                return

            self.current_frame = screen
            frame.tkraise()

            if hasattr(frame, "refresh"):
                try:
                    frame.refresh()
                except Exception:
                    _LOGGER.exception("refresh() failed on %s", screen)

        except Exception:
            _LOGGER.exception("show_frame failed")

    # =========================================================
    # USER + SLOT CONTROLLER API
    #=========================================================
    def get_user(self, uid):
        """Used by ScanScreen to detect existing user."""
        try:
            return self.read_user(uid)
        except Exception as e:
            _LOGGER.error("get_user failed: %s", e)
            return None

    def set_active_user(self, uid):
        self.active_uid = uid

    def set_active_slot(self, slot: str):
        """Mark a slot as active on the controller and try to persist state."""
        try:
            self.active_slot = slot
            # persist slot assignment if writer available
            try:
                if callable(self.write_slot):
                    self.write_slot(slot, {"current_user": self.active_uid, "status": "active"})
            except Exception:
                pass
            # also record on the user record if available
            try:
                if callable(self.write_user) and self.active_uid:
                    self.write_user(self.active_uid, {"occupied_slot": slot})
            except Exception:
                pass
        except Exception:
            _LOGGER.exception("set_active_slot failed for %s", slot)

    def clear_active_user(self):
        self.active_uid = None
        self.active_slot = None

    def get_slot(self, slot):
        try:
            return self.read_slot(slot)
        except Exception:
            return None

    # =========================================================
    # COIN POPUP + LOCAL TRACKING
    #=========================================================
    def record_coin_insert(self, uid, peso, added):
        """Record coin insert for session summary UI."""
        rec = self.coin_counters.get(uid, {"coins": 0, "seconds": 0, "value": 0})
        rec["coins"] += int(peso or 0)
        rec["value"] += int(added or 0)
        rec["seconds"] += int(added or 0)
        self.coin_counters[uid] = rec

    def show_coin_popup(self, **kwargs):
        # placeholder; you can implement your design here
        _LOGGER.info("Coin popup: %s", kwargs)

    def show_totals_popup(self, **kwargs):
        # placeholder
        _LOGGER.info("Totals popup: %s", kwargs)

