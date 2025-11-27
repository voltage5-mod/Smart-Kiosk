"""
services/session_manager.py

Single-threaded Session Manager for Smart Kiosk.

Responsibilities:
- Consume Event objects from an event queue and drive services (water, charging).
- Expose programmatic APIs for starting/stopping sessions used by UI.
- Interact with GPIOManager for hardware actions (relays, ACS readings, displays).
- Publish session updates to registered callbacks (UI subscribers).
- Enqueue DB work onto a db_queue (actual firebase_worker processes that queue).

Design choices:
- One consumer thread (or run loop invoked by caller) to avoid races.
- Event idempotency via Event.id to avoid double-processing.
- Conservative default thresholds and timeouts; prefer explicit configuration via constructor.
"""

from __future__ import annotations
import threading
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional, Callable, List
import queue

from utils.events import Event
from hardware.gpio_manager import get_gpio_manager, GPIOManager

_LOGGER = logging.getLogger("[SESSION_MANAGER]")

# Default thresholds (can be overridden via constructor)
DEFAULT_PLUG_THRESHOLD_A = 0.8    # amps -> considered "plug detected"
DEFAULT_UNPLUG_THRESHOLD_A = 0.5  # amps -> considered "unplugged"
DEFAULT_CHARGING_CURRENT_A = 0.8  # above this considered actively charging

@dataclass
class Session:
    """
    Minimal session object. Extend as needed.
    """
    session_id: str
    uid: Optional[str] = None
    user_type: Optional[str] = None  # "member"|"nonmember"
    service: str = "unknown"         # "water"|"charging"
    slot: Optional[int] = None
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    coins: List[int] = field(default_factory=list)
    total_credit_ml: Optional[int] = None
    dispensed_ml: int = 0
    status: str = "active"           # active|paused|completed|cancelled
    meta: Dict[str, Any] = field(default_factory=dict)


class SessionManager:
    """
    Main SessionManager.
    Usage:
        evt_q = queue.Queue()
        db_q = queue.Queue()
        sm = SessionManager(event_queue=evt_q, db_queue=db_q)
        sm.start()   # background thread
        ...
        sm.stop()
    """

    def __init__(
        self,
        event_queue: "queue.Queue[Event]",
        db_queue: Optional["queue.Queue[Dict[str, Any]]"] = None,
        gpio_manager: Optional[GPIOManager] = None,
        plug_threshold: float = DEFAULT_PLUG_THRESHOLD_A,
        unplug_threshold: float = DEFAULT_UNPLUG_THRESHOLD_A,
        charging_current_threshold: float = DEFAULT_CHARGING_CURRENT_A,
        loop_sleep: float = 0.05,
    ):
        self.event_queue = event_queue
        self.db_queue = db_queue or queue.Queue()
        self.gpio = gpio_manager or get_gpio_manager()
        self.plug_threshold = float(plug_threshold)
        self.unplug_threshold = float(unplug_threshold)
        self.charging_current_threshold = float(charging_current_threshold)
        self.loop_sleep = float(loop_sleep)

        # Session storage: session_id -> Session
        self.sessions: Dict[str, Session] = {}

        # Slot state: slot_id -> dict(state info)
        self.slots: Dict[int, Dict[str, Any]] = {}

        # seen event ids to avoid duplicate processing
        self._seen_event_ids: set[str] = set()
        self._seen_event_ids_max = 4000

        # callbacks for UI or other listeners: fn(session: Session)
        self._callbacks: List[Callable[[Session], None]] = []

        # control
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

    # -----------------------
    # Lifecycle
    # -----------------------
    def start(self, background: bool = True) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                _LOGGER.debug("SessionManager already running")
                return
            self._stop_event.clear()
            if background:
                self._thread = threading.Thread(target=self._run_loop, name="SessionManager", daemon=True)
                self._thread.start()
                _LOGGER.info("SessionManager started (background thread)")
            else:
                _LOGGER.info("SessionManager running in blocking mode")
                self._run_loop()

    def stop(self) -> None:
        _LOGGER.info("Stopping SessionManager...")
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        _LOGGER.info("SessionManager stopped")

    # -----------------------
    # Event subscription
    # -----------------------
    def register_callback(self, fn: Callable[[Session], None]) -> None:
        """Register a callback to receive session updates (UI)."""
        with self._lock:
            self._callbacks.append(fn)

    def _notify(self, session: Session) -> None:
        """Call callbacks (non-blocking)."""
        for cb in list(self._callbacks):
            try:
                cb(session)
            except Exception:
                _LOGGER.exception("Session callback raised exception")

    # -----------------------
    # Public APIs for UI
    # -----------------------
    def start_session(self, session_id: str, uid: Optional[str], service: str, slot: Optional[int] = None, user_type: Optional[str] = None) -> Session:
        """Create and register a new session."""
        with self._lock:
            if session_id in self.sessions:
                _LOGGER.warning("start_session called but session %s already exists", session_id)
                return self.sessions[session_id]

            s = Session(session_id=session_id, uid=uid, service=service, slot=slot, user_type=user_type)
            self.sessions[session_id] = s
            # If charging service and slot defined, mark slot reserved
            if service == "charging" and slot is not None:
                self.slots.setdefault(slot, {})["reserved_by"] = session_id
            _LOGGER.info("Session started: %s", session_id)
            self._notify(s)
            # enqueue db log (non-blocking) if desired
            self._enqueue_db({"op": "session_start", "session": asdict(s)})
            return s

    def stop_session(self, session_id: str, reason: Optional[str] = None) -> Optional[Session]:
        """Stop and finalize a session."""
        with self._lock:
            s = self.sessions.get(session_id)
            if not s:
                _LOGGER.warning("stop_session: unknown session %s", session_id)
                return None
            s.end_time = time.time()
            s.status = "completed" if reason is None else f"stopped:{reason}"
            # if reserved slot, release it
            if s.service == "charging" and s.slot is not None:
                info = self.slots.get(s.slot, {})
                if info.get("reserved_by") == session_id:
                    info.pop("reserved_by", None)
            # persist session summary to DB
            self._enqueue_db({"op": "session_end", "session": asdict(s)})
            _LOGGER.info("Session %s stopped (%s)", session_id, reason)
            self._notify(s)
            # remove session from active map (optionally keep archive elsewhere)
            self.sessions.pop(session_id, None)
            return s

    def add_coin(self, session_id: str, value: int) -> Optional[Session]:
        """Add coin value to a session (for water billing)."""
        with self._lock:
            s = self.sessions.get(session_id)
            if not s:
                _LOGGER.warning("add_coin: session not found %s", session_id)
                return None
            s.coins.append(int(value))
            # enqueue billing DB operation
            self._enqueue_db({"op": "coin", "session_id": session_id, "value": int(value)})
            self._notify(s)
            return s

    # -----------------------
    # Internal run loop
    # -----------------------
    def _run_loop(self) -> None:
        _LOGGER.info("SessionManager loop running")
        while not self._stop_event.is_set():
            try:
                ev: Event = self.event_queue.get(timeout=self.loop_sleep)
            except queue.Empty:
                # periodic maintenance: check charging slots for state changes
                try:
                    self._periodic_slot_checks()
                except Exception:
                    _LOGGER.exception("Periodic slot checks failed")
                continue

            # Deduplicate events by event.id
            if ev.id in self._seen_event_ids:
                _LOGGER.debug("Dropping duplicate event id=%s", ev.id)
                continue
            self._seen_event_ids.add(ev.id)
            if len(self._seen_event_ids) > self._seen_event_ids_max:
                # trim to half
                to_remove = list(self._seen_event_ids)[: self._seen_event_ids_max // 2]
                for r in to_remove:
                    self._seen_event_ids.discard(r)

            # Dispatch by source / name
            try:
                self._dispatch_event(ev)
            except Exception:
                _LOGGER.exception("Error handling event %s", ev)

        _LOGGER.info("SessionManager loop terminated")

    # -----------------------
    # Event dispatch
    # -----------------------
    def _dispatch_event(self, ev: Event) -> None:
        """Route events to appropriate handlers."""
        _LOGGER.debug("Dispatching event: %s", ev.short())

        if ev.source == "arduino":
            self._handle_arduino_event(ev)
        elif ev.source == "ui":
            self._handle_ui_event(ev)
        elif ev.source == "hardware":
            self._handle_hardware_event(ev)
        else:
            # generic or system events
            self._handle_generic_event(ev)

    # -----------------------
    # Handlers
    # -----------------------
    def _handle_arduino_event(self, ev: Event) -> None:
        """
        Example Arduino events:
          - COIN: value
          - CUP_DETECTED: slot
          - DISPENSE_REPORT: ml=..., session_id=...
          - MODE: WATER
          - SLOT:1,CURRENT:0.45  (parsed as ARDUINO event with args)
        Behavior:
          - If coin and session exists, credit session
          - For DISPENSE_REPORT update dispensed_ml etc.
          - For slot current updates, evaluate plug/unplug
        """
        name = ev.name.upper()
        args = ev.args or {}

        if name == "COIN":
            # coin insertion could be global or tied to session via args.session_id
            val = int(args.get("value") or args.get("amount") or 0)
            sid = args.get("session_id")
            if sid:
                self.add_coin(str(sid), val)
            else:
                # Best-effort: find a single active water session (non-ideal)
                with self._lock:
                    for s in self.sessions.values():
                        if s.service == "water":
                            self.add_coin(s.session_id, val)
                            break
            return

        if name == "DISPENSE_REPORT":
            sid = args.get("session_id")
            ml = int(args.get("ml") or args.get("amount") or 0)
            if sid and sid in self.sessions:
                with self._lock:
                    s = self.sessions[sid]
                    s.dispensed_ml += ml
                    self._enqueue_db({"op": "dispense_report", "session_id": sid, "ml": ml})
                    self._notify(s)
            return

        if name == "CUP_DETECTED":
            # convert or route to water FSM; we'll simply notify listeners
            slot = args.get("slot")
            _LOGGER.info("CUP_DETECTED slot=%s", slot)
            return

        # Generic handling for current reports e.g., name == "CURRENT" or ev.name == "ARDUINO" with args slot/current
        if name == "CURRENT" or (name == "ARDUINO" and "CURRENT" in args):
            slot = args.get("slot") or args.get("SLOT") or args.get("slot_id")
            current_val = args.get("current") or args.get("CURRENT") or args.get("value")
            # prefer float conversion
            try:
                current = float(current_val)
            except Exception:
                # fallback: ask gpio manager for current
                try:
                    if slot is not None:
                        current = float(self.gpio.read_acs(int(slot)))
                    else:
                        return
                except Exception:
                    return

            # Evaluate plug/unplug for the given slot if reserved/associated with session
            if slot is None:
                _LOGGER.debug("Current event missing slot; ignoring")
                return
            try:
                slot_id = int(slot)
            except Exception:
                _LOGGER.debug("Invalid slot id in current event: %s", slot)
                return

            self._evaluate_slot_current(slot_id, float(current))
            return

        # Fallback: other arduino events forward to generic handler
        _LOGGER.debug("Unhandled arduino event: %s", ev)

    def _handle_ui_event(self, ev: Event) -> None:
        """
        Handle events emitted from UI (button presses, start/stop session)
        Example:
          - name: START_SESSION  args: {session_id, uid, service, slot}
          - name: STOP_SESSION   args: {session_id, reason}
        """
        name = ev.name.upper()
        args = ev.args or {}

        if name in ("START_SESSION", "SESSION_START"):
            sid = args.get("session_id")
            uid = args.get("uid")
            service = args.get("service", "water")
            slot = args.get("slot")
            user_type = args.get("user_type")
            if not sid:
                _LOGGER.error("UI requested START_SESSION but no session_id provided")
                return
            self.start_session(str(sid), uid=uid, service=service, slot=slot, user_type=user_type)
            return

        if name in ("STOP_SESSION", "SESSION_STOP", "CANCEL_SESSION"):
            sid = args.get("session_id")
            reason = args.get("reason")
            if not sid:
                _LOGGER.error("STOP_SESSION missing session_id")
                return
            self.stop_session(str(sid), reason=reason)
            return

        if name == "SET_MOCK_CURRENT":  # for tests via UI
            slot = args.get("slot")
            amps = args.get("amps") or args.get("value")
            try:
                slot_id = int(slot)
                if hasattr(self.gpio, "set_mock_current"):
                    self.gpio.set_mock_current(slot_id, float(amps))
                else:
                    _LOGGER.warning("GPIO manager does not support set_mock_current")
            except Exception:
                _LOGGER.exception("SET_MOCK_CURRENT failed")
            return

        _LOGGER.debug("Unhandled UI event: %s", ev)

    def _handle_hardware_event(self, ev: Event) -> None:
        # reserved for future hardware-originated events
        _LOGGER.debug("Hardware event: %s", ev)

    def _handle_generic_event(self, ev: Event) -> None:
        _LOGGER.debug("Generic event: %s", ev)

    # -----------------------
    # Slot current evaluation
    # -----------------------
    def _evaluate_slot_current(self, slot_id: int, amps: float) -> None:
        """
        Update slot state according to current reading.
        Logic:
          - If current >= plug_threshold and previously unplugged -> mark PLUGGED
          - If current <= unplug_threshold and previously plugged -> mark UNPLUGGED
          - If current >= charging_current_threshold -> mark CHARGING
        """
        info = self.slots.setdefault(slot_id, {"state": "unknown", "last_change": time.time()})
        prev_state = info.get("state", "unknown")
        now = time.time()

        if amps >= self.plug_threshold and prev_state in ("unknown", "unplugged", "reserved"):
            info["state"] = "plugged"
            info["last_change"] = now
            _LOGGER.info("Slot %s → PLUGGED (amps=%.3fA)", slot_id, amps)
            # If reserved, mark session ready to start charging
            reserved_by = info.get("reserved_by")
            if reserved_by and reserved_by in self.sessions:
                s = self.sessions[reserved_by]
                s.meta["plug_time"] = now
                self._notify(s)
            return

        if amps <= self.unplug_threshold and prev_state in ("plugged", "charging"):
            info["state"] = "unplugged"
            info["last_change"] = now
            _LOGGER.info("Slot %s → UNPLUGGED (amps=%.3fA)", slot_id, amps)
            # If initial session associated, notify
            reserved_by = info.get("reserved_by")
            if reserved_by and reserved_by in self.sessions:
                s = self.sessions[reserved_by]
                s.meta["unplug_time"] = now
                # optionally stop session
                # self.stop_session(reserved_by, reason="unplugged")
                self._notify(s)
            return

        if amps >= self.charging_current_threshold and prev_state in ("plugged", "reserved"):
            info["state"] = "charging"
            info["last_change"] = now
            _LOGGER.info("Slot %s → CHARGING (amps=%.3fA)", slot_id, amps)
            reserved_by = info.get("reserved_by")
            if reserved_by and reserved_by in self.sessions:
                s = self.sessions[reserved_by]
                s.meta["charging_since"] = now
                self._notify(s)
            return

        # Otherwise, no state change
        _LOGGER.debug("Slot %s no state change (amps=%.3fA state=%s)", slot_id, amps, prev_state)

    # -----------------------
    # Periodic checks (poll ACS if needed)
    # -----------------------
    def _periodic_slot_checks(self) -> None:
        """
        Called when event_queue is empty for a loop tick.
        Use to poll ACS channels and update slot state proactively.
        """
        # Iterate over known slots from pinmap
        # Use gpio.read_acs(slot) to get RMS amps (our configured behavior)
        try:
            for sid_str, conf in self.gpio._pinmap.get("slots", {}).items():
                try:
                    slot_id = int(sid_str)
                except Exception:
                    continue
                amps = self.gpio.read_acs(slot_id)
                # Only react if a slot is reserved or we already know about it (avoid scanning noise)
                info = self.slots.setdefault(slot_id, {"state": "unknown", "last_change": time.time()})
                reserved = info.get("reserved_by")
                if reserved or info.get("state") != "unknown":
                    self._evaluate_slot_current(slot_id, amps)
        except Exception:
            _LOGGER.exception("Periodic slot checks error")

    # -----------------------
    # DB helper
    # -----------------------
    def _enqueue_db(self, payload: Dict[str, Any]) -> None:
        """Enqueue a DB operation for firebase_worker to process."""
        try:
            self.db_queue.put_nowait(payload)
        except queue.Full:
            _LOGGER.warning("DB queue full; dropping payload %s", payload)

