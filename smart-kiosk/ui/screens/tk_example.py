"""
Simple Tkinter UI example for Smart Kiosk demonstrating UIController usage.

This example is intentionally self-contained and uses a "fake core" thread that reads
events from the shared event_queue and emits back session updates via UIController's
_internal callback path so the UI shows changes.

Place at: smart-kiosk/ui/screens/tk_example.py
Run: python -m ui.screens.tk_example
"""

from __future__ import annotations
import threading
import time
import queue
import tkinter as tk
from tkinter import ttk, scrolledtext
import uuid
import logging

# Import the UIController created earlier
from ui.ui_controller import UIController
from utils.events import Event

logging.basicConfig(level=logging.INFO)
_LOGGER = logging.getLogger("tk_example")


# ---------- Fake core (very small simulation of SessionManager) ----------
def fake_core_loop(event_q: "queue.Queue[Event]", ui_controller: UIController, stop_event: threading.Event):
    """
    Very small fake SessionManager:
      - consumes events from event_q
      - for START_SESSION, creates a fake session and calls ui_controller._on_session_update()
      - for COIN, updates the session meta and sends an update
      - for STOP_SESSION, sends a final update and clears session
    This is ONLY for demo purposes to show how UIController and screens interact.
    """
    sessions = {}

    while not stop_event.is_set():
        try:
            ev = event_q.get(timeout=0.2)
        except queue.Empty:
            continue

        _LOGGER.info("Fake core received: %s %s", ev.name, ev.args)
        name = ev.name.upper()
        args = ev.args or {}

        if name in ("START_SESSION", "SESSION_START"):
            sid = args.get("session_id") or f"ses-{uuid.uuid4().hex[:8]}"
            service = args.get("service", "water")
            slot = args.get("slot")
            session = {
                "session_id": sid,
                "service": service,
                "status": "active",
                "slot": slot,
                "coins": [],
                "credit_ml": None,
                "dispensed_ml": 0,
                "meta": {},
            }
            sessions[sid] = session
            # Send update back to UI (simulate SessionManager.notify)
            ui_controller._on_session_update(type("S", (), session)())
            _LOGGER.info("Fake core created session %s", sid)

        elif name == "COIN":
            sid = args.get("session_id")
            val = int(args.get("value", 0) or 0)
            if sid and sid in sessions:
                sessions[sid]["coins"].append(val)
                # increase credit for demo purpose
                if sessions[sid].get("credit_ml") is None:
                    sessions[sid]["credit_ml"] = 0
                sessions[sid]["credit_ml"] += 500  # each coin -> 500 ml (demo)
                ui_controller._on_session_update(type("S", (), sessions[sid])())
                _LOGGER.info("Fake core credited session %s with %d (coins=%s)", sid, val, sessions[sid]["coins"])
            else:
                _LOGGER.info("COIN with no session or unknown session_id: %s", sid)

        elif name in ("STOP_SESSION", "SESSION_STOP", "CANCEL_SESSION"):
            sid = args.get("session_id")
            if sid and sid in sessions:
                sessions[sid]["status"] = "completed"
                sessions[sid]["end_time"] = int(time.time())
                ui_controller._on_session_update(type("S", (), sessions[sid])())
                # remove session after a small delay
                sessions.pop(sid, None)
                _LOGGER.info("Fake core stopped session %s", sid)

        elif name == "RESERVE_SLOT":
            slot = args.get("slot")
            sid = args.get("session_id")
            # echo a session update showing slot reserved
            if sid and sid in sessions:
                sessions[sid]["slot"] = slot
                sessions[sid]["meta"]["reserved"] = True
                ui_controller._on_session_update(type("S", (), sessions[sid])())
                _LOGGER.info("Fake core reserved slot %s for %s", slot, sid)

        else:
            _LOGGER.debug("Fake core ignoring event %s", name)


# ---------- Tk UI Implementation ----------
class MainApp(tk.Tk):
    def __init__(self, event_queue: "queue.Queue[Event]"):
        super().__init__()
        self.title("Smart Kiosk — Tk Example")
        self.geometry("760x520")

        self.event_queue = event_queue
        # Create UIController WITHOUT SessionManager (UIController will still put events on queue)
        self.ui = UIController(event_queue=event_queue, session_manager=None)

        # register a UI listener to update UI when sessions update
        self.ui.register_ui_listener(self.on_session_update)

        # layout: left controls, right logs
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        left = ttk.Frame(self, padding=10)
        left.grid(row=0, column=0, sticky="ns")
        right = ttk.Frame(self, padding=10)
        right.grid(row=0, column=1, sticky="nsew")

        # Controls
        ttk.Label(left, text="Screens", font=("Arial", 12, "bold")).pack(anchor="w", pady=(0,6))

        ttk.Button(left, text="Home", command=self.show_home).pack(fill="x", pady=3)
        ttk.Button(left, text="Charge Screen", command=self.show_charge).pack(fill="x", pady=3)
        ttk.Button(left, text="Water Screen", command=self.show_water).pack(fill="x", pady=3)

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=8)

        # Quick action helpers
        ttk.Label(left, text="Quick Actions", font=("Arial", 10, "bold")).pack(anchor="w", pady=(6,4))
        ttk.Button(left, text="Create Session (Charge)", command=self.create_charge_session).pack(fill="x", pady=3)
        ttk.Button(left, text="Create Session (Water)", command=self.create_water_session).pack(fill="x", pady=3)
        ttk.Button(left, text="Insert Coin (500ml)", command=self.insert_coin_demo).pack(fill="x", pady=3)
        ttk.Button(left, text="Reserve Slot 1", command=lambda: self.reserve_slot_demo(1)).pack(fill="x", pady=3)
        ttk.Button(left, text="Stop Session", command=self.stop_session_demo).pack(fill="x", pady=3)

        # Session panel
        ttk.Label(left, text="Active Session ID").pack(anchor="w", pady=(10,2))
        self.session_id_var = tk.StringVar()
        ttk.Entry(left, textvariable=self.session_id_var).pack(fill="x", pady=2)

        # Right: logs / session info
        ttk.Label(right, text="Events / Logs", font=("Arial", 12, "bold")).pack(anchor="w")
        self.log = scrolledtext.ScrolledText(right, width=60, height=30, state="disabled")
        self.log.pack(fill="both", expand=True, pady=(6,0))

        # Currently shown screen (placeholder)
        self.screen_frame = ttk.Frame(right)
        self.screen_frame.pack(fill="x", pady=8)
        self.current_screen_label = ttk.Label(self.screen_frame, text="Home Screen", font=("Arial", 11))
        self.current_screen_label.pack(anchor="w")

        # keep a map of sessions (for demo)
        self._sessions = {}

    # -------------------------
    # UI actions
    # -------------------------
    def append_log(self, msg: str):
        self.log.configure(state="normal")
        ts = time.strftime("%H:%M:%S")
        self.log.insert("end", f"[{ts}] {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def show_home(self):
        self.current_screen_label.config(text="Home Screen")
        self.append_log("Switched to Home")

    def show_charge(self):
        self.current_screen_label.config(text="Charge Screen")
        self.append_log("Switched to Charge screen")

    def show_water(self):
        self.current_screen_label.config(text="Water Screen")
        self.append_log("Switched to Water screen")

    def create_charge_session(self):
        sid = f"ui-{int(time.time()*1000)}"
        self.session_id_var.set(sid)
        # create a session_id and send START_SESSION
        self.ui.start_session(session_id=sid, uid="user-demo", service="charging", slot=1, user_type="nonmember")
        self.append_log(f"Requested START_SESSION (charging) {sid}")

    def create_water_session(self):
        sid = f"ui-{int(time.time()*1000)}"
        self.session_id_var.set(sid)
        self.ui.start_session(session_id=sid, uid="user-demo", service="water")
        self.append_log(f"Requested START_SESSION (water) {sid}")

    def insert_coin_demo(self):
        sid = self.session_id_var.get().strip() or None
        if not sid:
            self.append_log("No session id set; coin will be recorded global")
        self.ui.coin_inserted(value=1, session_id=sid)
        self.append_log(f"Inserted coin for session {sid}")

    def reserve_slot_demo(self, slot: int):
        sid = self.session_id_var.get().strip()
        if not sid:
            self.append_log("No session id set; cannot reserve slot")
            return
        self.ui.reserve_slot(slot=slot, session_id=sid)
        self.append_log(f"Requested reserve slot {slot} for {sid}")

    def stop_session_demo(self):
        sid = self.session_id_var.get().strip()
        if not sid:
            self.append_log("No session id set; nothing to stop")
            return
        self.ui.stop_session(session_id=sid)
        self.append_log(f"Requested stop session {sid}")

    # -------------------------
    # UIController listener
    # -------------------------
    def on_session_update(self, payload: dict):
        # payload example: {"type": "session_update", "data": {...}}
        try:
            if payload.get("type") != "session_update":
                return
            data = payload.get("data", {})
            sid = data.get("session_id")
            status = data.get("status")
            service = data.get("service")
            slot = data.get("slot")
            coins = data.get("coins", [])
            credit = data.get("credit_ml")
            dispensed = data.get("dispensed_ml", 0)
            self.append_log(f"Session update: id={sid} status={status} service={service} slot={slot} coins={coins} credit={credit} dispensed={dispensed}")
        except Exception:
            _LOGGER.exception("Error in on_session_update")

# ---------- Bootstrap the example app ----------
def main():
    event_q = queue.Queue()
    stop_event = threading.Event()

    # Create UI controller hooking up to the shared event queue
    app = MainApp(event_queue=event_q)

    # Start fake core in background (so UI sees session updates)
    t = threading.Thread(target=fake_core_loop, args=(event_q, app.ui, stop_event), daemon=True)
    t.start()

    try:
        app.mainloop()
    finally:
        stop_event.set()
        t.join(timeout=1.0)
        _LOGGER.info("Example app exiting")

if __name__ == "__main__":
    main()
