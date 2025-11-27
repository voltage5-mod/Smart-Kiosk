"""
ui/ui_controller.py

UI Controller layer for Smart Kiosk.

Responsibilities:
- Provide a small API for UI screens (start/stop session, select service, insert coin, reserve slot, etc.)
- Serialize UI actions into Event objects and put them onto the shared event_queue consumed by SessionManager.
- Receive session updates via SessionManager.register_callback and forward them to registered UI listeners.
- Keep UI logic thin: no hardware calls, no DB writes. Pure orchestration.

Design:
- Dependency-injected: accepts event_queue and an optional session_manager instance.
- Thread-safe: uses a lock where necessary for callback management.
- Testable: UI can be driven programmatically by calling methods and asserting events enqueued.
"""

from __future__ import annotations
import threading
import logging
import time
from typing import Any, Callable, Dict, Optional, List
import queue

from utils.events import Event

_LOGGER = logging.getLogger("[UI_CONTROLLER]")


class UIController:
    """
    UIController API (example):

        ui = UIController(event_queue=event_q, session_manager=session_manager)
        ui.register_ui_listener(my_screen_update_fn)

        # user taps "Charge" screen and selects slot 2
        ui.select_service(service="charging", slot=2, uid="user123", session_id="sess-abc")

        # user inserts coin (UI knows coin value)
        ui.coin_inserted(value=1, session_id="sess-abc")

        # user presses cancel
        ui.stop_session(session_id="sess-abc")

    The UIController will create Event objects and put them on the shared event_queue.
    """

    def __init__(self, event_queue: "queue.Queue[Event]", session_manager: Optional[Any] = None):
        if not isinstance(event_queue, queue.Queue):
            raise ValueError("event_queue must be a queue.Queue")
        self.event_queue = event_queue
        self.session_manager = session_manager
        self._ui_listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._lock = threading.RLock()

        # If session_manager provided, register to get session updates
        if self.session_manager is not None:
            try:
                self.session_manager.register_callback(self._on_session_update)
            except Exception:
                _LOGGER.exception("Failed to register callback on provided session_manager")

    # -------------------------
    # UI listeners (for screen updates)
    # -------------------------
    def register_ui_listener(self, fn: Callable[[Dict[str, Any]], None]) -> None:
        """Register a UI listener function that accepts a dict payload for screen updates."""
        with self._lock:
            self._ui_listeners.append(fn)

    def unregister_ui_listener(self, fn: Callable[[Dict[str, Any]], None]) -> None:
        with self._lock:
            try:
                self._ui_listeners.remove(fn)
            except ValueError:
                pass

    def _notify_ui_listeners(self, payload: Dict[str, Any]) -> None:
        """Call listeners in a best-effort, non-blocking way."""
        for fn in list(self._ui_listeners):
            try:
                fn(payload)
            except Exception:
                _LOGGER.exception("UI listener raised exception")

    # -------------------------
    # SessionManager callback
    # -------------------------
    def _on_session_update(self, session) -> None:
        """
        Called by SessionManager when sessions change.
        We convert the session dataclass to a JSON-friendly dict and broadcast to UI listeners.
        """
        try:
            payload = {}
            # support dataclass-ish objects and plain dicts
            try:
                # dataclass-like: has session_id attribute or can be dictified
                if hasattr(session, "session_id"):
                    payload = {
                        "session_id": getattr(session, "session_id"),
                        "service": getattr(session, "service", None),
                        "status": getattr(session, "status", None),
                        "slot": getattr(session, "slot", None),
                        "coins": getattr(session, "coins", []),
                        "credit_ml": getattr(session, "total_credit_ml", None) or getattr(session, "credit_ml", None),
                        "dispensed_ml": getattr(session, "dispensed_ml", 0),
                        "meta": getattr(session, "meta", {}),
                    }
                elif isinstance(session, dict):
                    payload = session
                else:
                    payload = {"repr": str(session)}
            except Exception:
                payload = {"repr": str(session)}

            self._notify_ui_listeners({"type": "session_update", "data": payload})
        except Exception:
            _LOGGER.exception("Error in _on_session_update")

    # -------------------------
    # UI -> Core (event creators)
    # -------------------------
    def _put_event(self, ev: Event) -> bool:
        """Put an Event into the shared event_queue. Returns True on success."""
        try:
            self.event_queue.put_nowait(ev)
            _LOGGER.debug("UIController enqueued Event: %s", ev)
            return True
        except queue.Full:
            _LOGGER.warning("Event queue full; dropping UI event %s", ev)
            return False

    def select_service(self, service: str, session_id: Optional[str] = None, uid: Optional[str] = None, slot: Optional[int] = None, user_type: Optional[str] = None) -> bool:
        """
        UI called when user selects a service (e.g., "water" or "charging").
        This creates a START_SESSION event if session_id provided, otherwise emits MODE or SELECT event.
        """
        service = str(service).lower()
        if session_id:
            ev = Event(source="ui", name="START_SESSION", args={"session_id": session_id, "uid": uid, "service": service, "slot": slot, "user_type": user_type})
            return self._put_event(ev)
        else:
            ev = Event(source="ui", name="MODE", args={"mode": service})
            return self._put_event(ev)

    def start_session(self, session_id: str, uid: Optional[str], service: str, slot: Optional[int] = None, user_type: Optional[str] = None) -> bool:
        """Explicit API for UI to start a session (wraps select_service)."""
        return self.select_service(service=service, session_id=session_id, uid=uid, slot=slot, user_type=user_type)

    def stop_session(self, session_id: str, reason: Optional[str] = None) -> bool:
        ev = Event(source="ui", name="STOP_SESSION", args={"session_id": session_id, "reason": reason})
        return self._put_event(ev)

    def cancel_session(self, session_id: str) -> bool:
        return self.stop_session(session_id, reason="cancelled_by_ui")

    def coin_inserted(self, value: int, session_id: Optional[str] = None, uid: Optional[str] = None, actor: Optional[str] = None) -> bool:
        """
        Called by UI when coin insertion is detected on-screen (or via hardware event forwarded to UI).
        We emit a COIN event that SessionManager or BillingService will process.
        """
        ev = Event(source="ui", name="COIN", args={"value": int(value), "session_id": session_id, "uid": uid, "actor": actor or "ui"})
        return self._put_event(ev)

    def reserve_slot(self, slot: int, session_id: str) -> bool:
        """
        Reserve a charging slot via UI before user plugs in.
        This emits a UI event which SessionManager will translate into a session reservation.
        """
        ev = Event(source="ui", name="RESERVE_SLOT", args={"slot": int(slot), "session_id": session_id})
        return self._put_event(ev)

    def set_mock_current(self, slot: int, amps: float) -> bool:
        """
        Testing helper: instructs the system (via a UI event) to set mock ACS value for a slot.
        SessionManager will handle SET_MOCK_CURRENT and forward to gpio mock if supported.
        """
        ev = Event(source="ui", name="SET_MOCK_CURRENT", args={"slot": slot, "amps": float(amps)})
        return self._put_event(ev)

    def send_raw_command(self, cmd: str) -> bool:
        """
        Advanced: allow UI to send raw commands to Arduino (useful for maintenance screen).
        Emits an event that ArduinoListener or a maintenance handler can pick up.
        """
        ev = Event(source="ui", name="RAW_CMD", args={"cmd": cmd})
        return self._put_event(ev)

    # -------------------------
    # Utilities for UI screens
    # -------------------------
    def build_start_session_payload(self, uid: Optional[str], service: str, slot: Optional[int] = None, user_type: Optional[str] = None) -> Dict[str, Any]:
        """Return a simple payload representing a proposed session (useful for screens)."""
        return {
            "session_id": f"ui-{int(time.time()*1000)}",
            "uid": uid,
            "service": service,
            "slot": slot,
            "user_type": user_type,
            "ts": int(time.time() * 1000)
        }

    # Backwards-compatible helper for old UI code expecting synchronous result
    def start_session_and_wait_ack(self, session_id: str, uid: Optional[str], service: str, slot: Optional[int] = None, timeout: float = 2.0) -> bool:
        """
        Fire-and-wait helper used by tests/legacy UI: creates a START_SESSION event and waits
        for a session_update with matching session_id to appear via registered UI listeners.
        NOTE: This is a best-effort synchronous convenience and should not be used for production blocking.
        """
        ack_event = threading.Event()
        result = {"ok": False}

        def _listener(payload: Dict[str, Any]):
            try:
                if payload.get("type") == "session_update":
                    data = payload.get("data") or {}
                    if data.get("session_id") == session_id:
                        result["ok"] = True
                        ack_event.set()
            except Exception:
                pass

        self.register_ui_listener(_listener)
        try:
            self.start_session(session_id=session_id, uid=uid, service=service, slot=slot)
            ack_event.wait(timeout=timeout)
            return result["ok"]
        finally:
            self.unregister_ui_listener(_listener)

