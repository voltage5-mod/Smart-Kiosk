"""
services/charging_service.py

Per-slot charging state machine for Smart Kiosk.

Design goals:
- Use gpio_manager.read_acs(slot) for current readings (RMS amps returned).
- Minimal side effects in library: hardware actions are via injected gpio_manager/unlock callbacks.
- Notify subscribers (SessionManager/UI) about slot & session updates via callbacks.
- Enqueue DB operations to provided db_queue (non-blocking).
- Testable: inject gpio_manager, time function, and callbacks.

States:
  - idle: slot free
  - reserved: a session has reserved the slot (reservation may carry session_id)
  - plugged: plug detected but not yet actively charging (waiting for handshake/timeout)
  - charging: actively charging; a timer counts down or up depending on billing model
  - paused: user or error paused charging
  - done: completed charging, can unlock
  - error: error state

Typical flow:
  SessionManager reserves slot -> state reserved
  User plugs cable -> ACS reading crosses plug_threshold -> state plugged
  After handshake or immediate, charging begins -> state charging
  When charging ends or unplug detected beyond thresholds -> state done/unplugged -> unlock
"""

from __future__ import annotations
import threading
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, Optional, List
import queue

_LOGGER = logging.getLogger("[CHARGING_SERVICE]")

# Slot states
SLOT_IDLE = "idle"
SLOT_RESERVED = "reserved"
SLOT_PLUGGED = "plugged"
SLOT_CHARGING = "charging"
SLOT_PAUSED = "paused"
SLOT_DONE = "done"
SLOT_ERROR = "error"

@dataclass
class SlotState:
    slot_id: int
    state: str = SLOT_IDLE
    reserved_by: Optional[str] = None   # session_id
    last_state_change: float = field(default_factory=time.time)
    current_amps: float = 0.0
    charging_started_at: Optional[float] = None
    charging_total_seconds: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)


class ChargingService:
    """
    ChargingService orchestrates multiple slots.

    Constructor args:
      - slot_ids: iterable of slot integers to manage
      - gpio_manager: optional GPIO manager (get_gpio_manager used if not provided)
      - db_queue: queue for DB ops (optional)
      - notify_cb: optional callback called on slot state changes: fn(slot_state: SlotState)
      - plug_threshold / unplug_threshold / charging_threshold: amps thresholds
      - max_charge_seconds: maximum allowed charging duration
      - poll_interval: seconds between active polls (when charging/reserved)
      - time_fn: injection for time.time (default time.time)
    """

    def __init__(
        self,
        slot_ids: List[int],
        gpio_manager: Optional[Any] = None,
        db_queue: Optional["queue.Queue[Dict[str, Any]]"] = None,
        notify_cb: Optional[Callable[[SlotState], None]] = None,
        plug_threshold: float = 0.8,
        unplug_threshold: float = 0.5,
        charging_threshold: float = 0.8,
        max_charge_seconds: int = 60 * 60,  # default 1 hour
        poll_interval: float = 1.0,
        time_fn: Callable[[], float] = time.time,
    ):
        self.slot_ids = list(slot_ids)
        self.gpio = gpio_manager
        self.db_queue = db_queue or queue.Queue()
        self.notify_cb = notify_cb
        self.plug_threshold = float(plug_threshold)
        self.unplug_threshold = float(unplug_threshold)
        self.charging_threshold = float(charging_threshold)
        self.max_charge_seconds = int(max_charge_seconds)
        self.poll_interval = float(poll_interval)
        self.time = time_fn

        # Initialize slot states
        self.slots: Dict[int, SlotState] = {sid: SlotState(slot_id=sid) for sid in self.slot_ids}

        # internal control
        self._lock = threading.RLock()
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # If no gpio manager injected, import lazily to avoid circular import in tests
        if self.gpio is None:
            try:
                from hardware.gpio_manager import get_gpio_manager
                self.gpio = get_gpio_manager()
            except Exception:
                _LOGGER.warning("No gpio_manager available; ChargingService will run in mock/no-read mode")

    # -------------------------
    # Lifecycle
    # -------------------------
    def start(self) -> None:
        with self._lock:
            if self._poll_thread and self._poll_thread.is_alive():
                return
            self._stop_event.clear()
            self._poll_thread = threading.Thread(target=self._poll_loop, name="ChargingServicePoll", daemon=True)
            self._poll_thread.start()
            _LOGGER.info("ChargingService started for slots: %s", self.slot_ids)

    def stop(self) -> None:
        self._stop_event.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=2.0)
        _LOGGER.info("ChargingService stopped")

    # -------------------------
    # Reservation / control API (used by SessionManager)
    # -------------------------
    def reserve_slot(self, slot_id: int, session_id: str) -> bool:
        with self._lock:
            s = self.slots.get(slot_id)
            if not s:
                _LOGGER.error("reserve_slot: unknown slot %s", slot_id)
                return False
            if s.state not in (SLOT_IDLE, SLOT_DONE):
                _LOGGER.warning("reserve_slot: slot %s not available (state=%s)", slot_id, s.state)
                return False
            s.state = SLOT_RESERVED
            s.reserved_by = session_id
            s.last_state_change = self.time()
            self._notify(s)
            self._enqueue_db({"op": "slot_reserved", "slot": slot_id, "session_id": session_id})
            _LOGGER.info("Slot %s reserved by %s", slot_id, session_id)
            return True

    def release_slot(self, slot_id: int) -> None:
        with self._lock:
            s = self.slots.get(slot_id)
            if not s:
                return
            prev = s.state
            s.state = SLOT_IDLE
            s.reserved_by = None
            s.last_state_change = self.time()
            s.charging_started_at = None
            s.charging_total_seconds = 0.0
            s.meta.clear()
            self._notify(s)
            self._enqueue_db({"op": "slot_released", "slot": slot_id, "prev_state": prev})
            _LOGGER.info("Slot %s released (was %s)", slot_id, prev)

    def force_unlock(self, slot_id: int, unlock_callback: Optional[Callable[[int], None]] = None) -> None:
        """
        Force unlock the physical lock for a slot by calling supplied callback
        (or using gpio if available).
        """
        if unlock_callback:
            try:
                unlock_callback(slot_id)
            except Exception:
                _LOGGER.exception("unlock_callback failed for slot %s", slot_id)
        else:
            # attempt to use gpio manager if it exposes set_relay_lock
            try:
                if self.gpio and hasattr(self.gpio, "set_relay_lock"):
                    self.gpio.set_relay_lock(slot_id, locked=False)
                    _LOGGER.info("Force unlock via gpio for slot %s", slot_id)
                else:
                    _LOGGER.warning("No unlock callback and gpio cannot unlock slot %s", slot_id)
            except Exception:
                _LOGGER.exception("Failed to force unlock slot %s", slot_id)

    # -------------------------
    # Polling loop (reads ACS and updates slot states)
    # -------------------------
    def _poll_loop(self) -> None:
        _LOGGER.debug("ChargingService poll loop started")
        while not self._stop_event.is_set():
            with self._lock:
                for sid in list(self.slot_ids):
                    try:
                        self._poll_slot(sid)
                    except Exception:
                        _LOGGER.exception("Error polling slot %s", sid)
            time.sleep(self.poll_interval)
        _LOGGER.debug("ChargingService poll loop exiting")

    def _poll_slot(self, slot_id: int) -> None:
        s = self.slots.get(slot_id)
        if not s:
            return

        # read current (uses rms current from gpio manager)
        try:
            amps = 0.0
            if self.gpio and hasattr(self.gpio, "read_acs"):
                amps = float(self.gpio.read_acs(slot_id))
            s.current_amps = amps
        except Exception:
            _LOGGER.exception("Failed to read ACS for slot %s", slot_id)
            s.current_amps = 0.0

        now = self.time()
        prev_state = s.state

        # State transitions
        if s.state == SLOT_RESERVED:
            # detect plug
            if s.current_amps >= self.plug_threshold:
                s.state = SLOT_PLUGGED
                s.last_state_change = now
                _LOGGER.info("Slot %s: PLUGGED (amps=%.3f)", slot_id, s.current_amps)
                self._enqueue_db({"op": "slot_plugged", "slot": slot_id, "session_id": s.reserved_by, "amps": s.current_amps})
                self._notify(s)
            # else remain reserved
            return

        if s.state == SLOT_PLUGGED:
            # if current above charging threshold -> start charging
            if s.current_amps >= self.charging_threshold:
                s.state = SLOT_CHARGING
                s.charging_started_at = now
                s.last_state_change = now
                _LOGGER.info("Slot %s: CHARGING started (amps=%.3f)", slot_id, s.current_amps)
                self._enqueue_db({"op": "slot_charging_start", "slot": slot_id, "session_id": s.reserved_by, "amps": s.current_amps})
                self._notify(s)
            # if current dropped below unplug threshold quickly -> go back to reserved/unplugged
            elif s.current_amps <= self.unplug_threshold:
                s.state = SLOT_RESERVED
                s.last_state_change = now
                _LOGGER.info("Slot %s: detected unplug while plugged (amps=%.3f) -> RESERVED", slot_id, s.current_amps)
                self._enqueue_db({"op": "slot_unplugged", "slot": slot_id, "session_id": s.reserved_by, "amps": s.current_amps})
                self._notify(s)
            return

        if s.state == SLOT_CHARGING:
            # accumulate charging time
            if s.charging_started_at:
                s.charging_total_seconds = (now - s.charging_started_at)
            # if unplug detected (amps <= unplug_threshold) -> paused or done
            if s.current_amps <= self.unplug_threshold:
                # mark paused/unplugged
                s.state = SLOT_PAUSED
                s.last_state_change = now
                _LOGGER.info("Slot %s: charging paused/unplugged (amps=%.3f)", slot_id, s.current_amps)
                self._enqueue_db({"op": "slot_charging_paused", "slot": slot_id, "session_id": s.reserved_by, "amps": s.current_amps})
                self._notify(s)
                # optionally automatically finish if charge time > minimal threshold — configurable externally
                return
            # if exceeded max time -> mark done and unlock
            if s.charging_total_seconds >= self.max_charge_seconds:
                s.state = SLOT_DONE
                s.last_state_change = now
                _LOGGER.info("Slot %s: charging DONE (max seconds reached)", slot_id)
                self._enqueue_db({"op": "slot_charging_done", "slot": slot_id, "session_id": s.reserved_by, "total_seconds": s.charging_total_seconds})
                self._notify(s)
                # force unlock
                self.force_unlock(slot_id)
                return
            return

        if s.state == SLOT_PAUSED:
            # If current rises back above charging threshold -> resume charging
            if s.current_amps >= self.charging_threshold:
                s.state = SLOT_CHARGING
                # do not reset charging_started_at; continue measuring
                s.last_state_change = now
                _LOGGER.info("Slot %s: resume CHARGING (amps=%.3f)", slot_id, s.current_amps)
                self._enqueue_db({"op": "slot_charging_resumed", "slot": slot_id, "session_id": s.reserved_by, "amps": s.current_amps})
                self._notify(s)
            # If long time in paused (configurable), could mark done; we leave that to higher level
            return

        if s.state in (SLOT_IDLE, SLOT_DONE, SLOT_ERROR):
            # nothing to do except notify current reading if someone cares
            return

    # -------------------------
    # Notifications and DB
    # -------------------------
    def _notify(self, slot_state: SlotState) -> None:
        try:
            if self.notify_cb:
                self.notify_cb(slot_state)
        except Exception:
            _LOGGER.exception("notify_cb raised exception for slot %s", slot_state.slot_id)

    def _enqueue_db(self, payload: Dict[str, Any]) -> None:
        try:
            self.db_queue.put_nowait(payload)
        except queue.Full:
            _LOGGER.warning("db_queue full; dropping payload %s", payload)

    # -------------------------
    # Utility getters
    # -------------------------
    def get_slot(self, slot_id: int) -> Optional[SlotState]:
        return self.slots.get(slot_id)

    def list_slots(self) -> List[SlotState]:
        return list(self.slots.values())
