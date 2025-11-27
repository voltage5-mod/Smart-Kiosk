"""
services/water_service.py

Water dispensing service (FSM) for Smart Kiosk.

Design:
- Coins increment credit (ml) for water sessions.
- When a cup is detected, and session has credit, begin dispensing by sending command to Arduino.
- While dispensing, process DISPENSE_REPORT events from Arduino to accumulate dispensed_ml.
- Stop dispensing when target is reached, or on error/timeout.
- All external actions (sending commands, DB writes, GPIO reads) are injected so this module is testable.

Usage (example):
    svc = WaterService(arduino_send=arduino_listener.send_command, db_queue=db_q)
    svc.handle_event(ev)   # call from SessionManager when it receives an Event
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, Optional
import time
import logging
import threading
import queue

from utils.events import Event

_LOGGER = logging.getLogger("[WATER_SERVICE]")

# States
STATE_IDLE = "idle"
STATE_WAITING_FOR_CUP = "waiting_for_cup"
STATE_DISPENSING = "dispensing"
STATE_FINISHING = "finishing"

@dataclass
class WaterSessionState:
    session_id: str
    uid: Optional[str] = None
    coins: list[int] = field(default_factory=list)
    credit_ml: int = 0             # how many ml are paid for
    dispensed_ml: int = 0
    state: str = STATE_IDLE
    start_ts: float = field(default_factory=time.time)
    last_update_ts: float = field(default_factory=time.time)
    target_ml: Optional[int] = None
    slot: Optional[int] = None     # optional hardware slot id (if relevant)
    meta: Dict[str, Any] = field(default_factory=dict)


class WaterService:
    """
    WaterService implements the dispensing state-machine.
    Parameters:
      - arduino_send: callable(cmd_str) to send commands to Arduino (required)
      - db_queue: queue.Queue to push DB operations (optional)
      - ml_per_coin: how many milliliters each coin buys
      - max_dispense_time_s: cutoff to avoid runaway dispensing
      - time_fn: optional injection of time.time for testability
    """

    def __init__(
        self,
        arduino_send: Callable[[str], bool],
        db_queue: Optional["queue.Queue[Dict[str, Any]]] = None,
        ml_per_coin: int = 500,
        max_dispense_time_s: int = 60,
        time_fn: Callable[[], float] = time.time,
    ):
        if not callable(arduino_send):
            raise ValueError("arduino_send must be callable")
        self.arduino_send = arduino_send
        self.db_queue = db_queue or queue.Queue()
        self.ml_per_coin = int(ml_per_coin)
        self.max_dispense_time = int(max_dispense_time_s)
        self.time = time_fn

        # active sessions tracked by session_id
        self._sessions: Dict[str, WaterSessionState] = {}
        # internal lock to guard state
        self._lock = threading.RLock()

    # ------------------------
    # Public API
    # ------------------------
    def create_session(self, session_id: str, uid: Optional[str] = None, slot: Optional[int] = None) -> WaterSessionState:
        with self._lock:
            s = WaterSessionState(session_id=session_id, uid=uid, slot=slot)
            self._sessions[session_id] = s
            _LOGGER.info("Water session created: %s", session_id)
            return s

    def end_session(self, session_id: str, reason: Optional[str] = None) -> None:
        with self._lock:
            s = self._sessions.get(session_id)
            if not s:
                return
            # enqueue DB summary
            self._enqueue_db({"op": "water_session_end", "session_id": session_id, "summary": asdict(s), "reason": reason})
            # try to stop dispensing if active
            if s.state == STATE_DISPENSING:
                try:
                    self.arduino_send(f"STOP_DISPENSE {session_id}")
                except Exception:
                    _LOGGER.exception("Failed to send STOP_DISPENSE for %s", session_id)
            _LOGGER.info("Water session ended: %s (%s)", session_id, reason)
            # drop from active map
            self._sessions.pop(session_id, None)

    def handle_event(self, ev: Event) -> None:
        """
        Handle an Event (coming from SessionManager).
        Relevant event types:
          - COIN {value, session_id?}
          - CUP_DETECTED {slot, session_id?}
          - DISPENSE_REPORT {session_id, ml}
          - STOP_SESSION / CANCEL_SESSION may be forwarded by SessionManager
        """
        try:
            if ev.name.upper() == "COIN":
                self._handle_coin(ev)
            elif ev.name.upper() == "CUP_DETECTED":
                self._handle_cup_detect(ev)
            elif ev.name.upper() == "DISPENSE_REPORT":
                self._handle_dispense_report(ev)
            elif ev.name.upper() in ("STOP_SESSION", "CANCEL_SESSION"):
                sid = ev.args.get("session_id") or ev.args.get("session")
                if sid:
                    self.end_session(str(sid), reason="stop_requested")
            else:
                # ignore other events
                _LOGGER.debug("WaterService ignoring event %s", ev)
        except Exception:
            _LOGGER.exception("Error handling event in WaterService: %s", ev)

    # ------------------------
    # Handlers
    # ------------------------
    def _handle_coin(self, ev: Event) -> None:
        args = ev.args or {}
        raw_val = args.get("value") or args.get("amount") or args.get("val")
        try:
            val = int(raw_val)
        except Exception:
            _LOGGER.warning("COIN event missing numeric value: %s", raw_val)
            return

        sid = args.get("session_id")
        if not sid:
            _LOGGER.debug("COIN not tied to session; will not auto-assign")
            return

        with self._lock:
            s = self._sessions.get(str(sid)) or self.create_session(str(sid))
            s.coins.append(val)
            # increment credit: each coin -> ml_per_coin (preserves legacy contract)
            s.credit_ml += self.ml_per_coin
            s.last_update_ts = self.time()
            _LOGGER.info("Session %s credited %d ml (coins=%s) total_credit=%d", s.session_id, self.ml_per_coin, s.coins, s.credit_ml)
            # enqueue db record
            self._enqueue_db({"op": "coin", "session_id": s.session_id, "value": val, "credit_ml": s.credit_ml})
            # If cup already present and we are idle/waiting, start dispensing
            if s.state in (STATE_IDLE, STATE_WAITING_FOR_CUP):
                # if cup detected meta flag exists, start immediately
                if s.meta.get("cup_present"):
                    self._start_dispense(s)
            return

    def _handle_cup_detect(self, ev: Event) -> None:
        args = ev.args or {}
        sid = args.get("session_id")
        slot = args.get("slot")
        # If session_id provided, use it; else best-effort try to find a session for slot
        with self._lock:
            s = None
            if sid:
                s = self._sessions.get(str(sid))
            else:
                # find any session with matching slot
                for ss in self._sessions.values():
                    if ss.slot == slot:
                        s = ss
                        break

            if not s:
                # create a temporary session only if coin present? For safety, create idle session and mark cup_present
                s = self.create_session(session_id=f"tmp-{int(self.time()*1000)}", uid=None, slot=slot)
                s.meta["ephemeral"] = True

            s.meta["cup_present"] = True
            s.last_update_ts = self.time()
            _LOGGER.info("CUP_DETECTED for session %s slot=%s", s.session_id, slot)

            # If we already have credit, begin dispensing
            if s.credit_ml and s.state in (STATE_IDLE, STATE_WAITING_FOR_CUP):
                self._start_dispense(s)
            else:
                # move to waiting state
                s.state = STATE_WAITING_FOR_CUP
                self._enqueue_db({"op": "cup_detected", "session_id": s.session_id, "slot": slot})
            return

    def _handle_dispense_report(self, ev: Event) -> None:
        """
        Expected args: session_id, ml (integer)
        The Arduino periodically reports ml dispensed; we accumulate and stop when target reached.
        """
        args = ev.args or {}
        sid = args.get("session_id")
        if not sid:
            _LOGGER.warning("DISPENSE_REPORT without session_id: %s", args)
            return
        with self._lock:
            s = self._sessions.get(str(sid))
            if not s:
                _LOGGER.warning("DISPENSE_REPORT for unknown session %s", sid)
                return
            ml = int(args.get("ml") or args.get("amount") or 0)
            s.dispensed_ml += ml
            s.last_update_ts = self.time()
            _LOGGER.debug("Session %s dispense_report +%d ml (total=%d/%s)", s.session_id, ml, s.dispensed_ml, s.credit_ml)
            # enqueue DB record of the increment
            self._enqueue_db({"op": "dispense_increment", "session_id": s.session_id, "ml": ml})
            # check if we've reached target
            if s.credit_ml and s.dispensed_ml >= s.credit_ml:
                _LOGGER.info("Session %s reached credit target (%d ml). Stopping dispense.", s.session_id, s.credit_ml)
                self._stop_dispense(s)
                # finalize session
                self._finalize_session(s)
            else:
                # still dispensing; optionally extend timeout if needed
                pass

    # ------------------------
    # Dispense control
    # ------------------------
    def _start_dispense(self, s: WaterSessionState) -> None:
        """
        Send command to Arduino to start dispensing.
        The command uses the legacy contract: "START_DISPENSE {session_id} {target_ml}"
        """
        if s.state == STATE_DISPENSING:
            _LOGGER.debug("Session %s already dispensing", s.session_id)
            return

        # Decide target: remaining credit
        remaining_ml = max(0, s.credit_ml - s.dispensed_ml)
        if remaining_ml <= 0:
            _LOGGER.info("Session %s has no remaining credit to dispense", s.session_id)
            return

        s.target_ml = remaining_ml
        s.state = STATE_DISPENSING
        s.start_ts = self.time()
        s.last_update_ts = s.start_ts

        cmd = f"START_DISPENSE {s.session_id} {int(s.target_ml)}"
        success = False
        try:
            success = bool(self.arduino_send(cmd))
        except Exception:
            _LOGGER.exception("Failed to send START_DISPENSE for %s", s.session_id)
        if not success:
            _LOGGER.error("Arduino refused START_DISPENSE for %s — marking as WAITING_FOR_CUP", s.session_id)
            s.state = STATE_WAITING_FOR_CUP
            self._enqueue_db({"op": "start_dispense_failed", "session_id": s.session_id})
            return

        _LOGGER.info("START_DISPENSE sent for %s target=%d ml", s.session_id, s.target_ml)
        self._enqueue_db({"op": "start_dispense", "session_id": s.session_id, "target_ml": s.target_ml})
        # start a watchdog thread to enforce max_dispense_time
        threading.Thread(target=self._dispense_watchdog, args=(s.session_id,), daemon=True).start()

    def _stop_dispense(self, s: WaterSessionState) -> None:
        try:
            self.arduino_send(f"STOP_DISPENSE {s.session_id}")
        except Exception:
            _LOGGER.exception("Failed to request STOP_DISPENSE for %s", s.session_id)
        s.state = STATE_FINISHING
        s.last_update_ts = self.time()
        self._enqueue_db({"op": "stop_dispense", "session_id": s.session_id, "dispensed_ml": s.dispensed_ml})

    def _dispense_watchdog(self, session_id: str) -> None:
        """
        Ensure dispensing doesn't run beyond max_dispense_time; if exceeded, call _stop_dispense.
        """
        start = self.time()
        while True:
            with self._lock:
                s = self._sessions.get(session_id)
                if not s:
                    return
                if s.state != STATE_DISPENSING:
                    return
                elapsed = self.time() - s.start_ts
                if elapsed > self.max_dispense_time:
                    _LOGGER.warning("Dispense watchdog: session %s exceeded max time (%ds). Stopping.", session_id, elapsed)
                    self._stop_dispense(s)
                    # finalize as error
                    self._enqueue_db({"op": "dispense_timeout", "session_id": session_id, "elapsed_s": elapsed})
                    self._finalize_session(s, reason="timeout")
                    return
            time.sleep(0.5)

    def _finalize_session(self, s: WaterSessionState, reason: Optional[str] = None) -> None:
        s.state = STATE_FINISHING
        s.last_update_ts = self.time()
        # enqueue summary
        self._enqueue_db({"op": "water_session_summary", "session_id": s.session_id, "summary": asdict(s), "reason": reason})
        _LOGGER.info("Finalizing water session %s (dispensed=%d ml)", s.session_id, s.dispensed_ml)
        # remove session from active sessions
        with self._lock:
            try:
                self._sessions.pop(s.session_id, None)
            except Exception:
                pass

    # ------------------------
    # Utilities
    # ------------------------
    def _enqueue_db(self, payload: Dict[str, Any]) -> None:
        try:
            self.db_queue.put_nowait(payload)
        except queue.Full:
            _LOGGER.warning("db_queue full; dropping payload %s", payload)

    # ------------------------
    # Test / debug helpers
    # ------------------------
    def get_session(self, session_id: str) -> Optional[WaterSessionState]:
        return self._sessions.get(session_id)

    def list_sessions(self) -> list:
        return list(self._sessions.keys())
