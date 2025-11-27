# arduino/arduino_listener.py
import threading
import time
import serial
import logging
from utils.events import Event
import queue

_LOGGER = logging.getLogger("ArduinoListener")


class ArduinoListener(threading.Thread):
    """
    Fully patched listener for Arduino → Raspberry Pi serial communication.
    Reads events from the Arduino firmware (coins, dispense, cup detect, etc.)
    and forwards them to KioskApp services and UI.

    This file matches EXACTLY the protocol from your Arduino sketch:
      COIN_INSERTED <peso>
      COIN_WATER <ml>
      COIN_CHARGE <peso>
      UNKNOWN_COIN <pulses>

      CUP_DETECTED
      CUP_REMOVED
      DISPENSE_START
      DISPENSE_PROGRESS ml=<x> remaining=<y>
      DISPENSE_DONE <ml>
      CREDIT_LEFT <ml>

      MODE: WATER
      MODE: CHARGE
      System reset.
    """

    def __init__(self, controller, port="/dev/ttyACM0", baud=115200):
        super().__init__(daemon=True)
        self.controller = controller
        self.port = port
        self.baud = baud
        self.serial = None
        self.running = True

    # ------------------------------------------
    # SERIAL INITIALIZATION
    # ------------------------------------------
    def open_serial(self):
        """Open USB serial port safely."""
        try:
            self.serial = serial.Serial(self.port, self.baud, timeout=1)
            _LOGGER.info(f"Arduino serial connected on {self.port} @ {self.baud}")
        except Exception as e:
            _LOGGER.error(f"Failed to open Arduino serial: {e}")
            self.serial = None

    # ------------------------------------------
    # SEND COMMAND TO ARDUINO
    # ------------------------------------------
    def send(self, msg: str):
        """Send a command to the Arduino (e.g., MODE WATER)."""
        try:
            if self.serial:
                self.serial.write((msg + "\n").encode())
                _LOGGER.info(f"[TX] → Arduino: {msg}")
        except Exception as e:
            _LOGGER.error(f"Serial send failed: {e}")

    # ------------------------------------------
    # THREAD MAIN LOOP
    # ------------------------------------------
    def run(self):
        """Background thread that continuously reads Arduino output."""
        self.open_serial()
        if not self.serial:
            _LOGGER.error("Arduino listener disabled — serial unavailable.")
            return

        while self.running:
            try:
                line = self.serial.readline().decode(errors="ignore").strip()
                if line:
                    _LOGGER.info(f"[RX] Arduino → {line}")
                    self.process_line(line)
            except Exception:
                continue

    # ------------------------------------------
    # PROCESS INCOMING ARDUINO MESSAGE
    # ------------------------------------------
    def process_line(self, line: str):
        """Pattern matches all Arduino events."""

        # ---------------------------
        # COIN EVENTS
        # ---------------------------
        if line.startswith("COIN_INSERTED"):
            # COIN_INSERTED 5
            parts = line.split()
            if len(parts) >= 2:
                peso = int(parts[1])
                self._handle_coin_insert(peso)
            return

        if line.startswith("COIN_WATER"):
            # COIN_WATER 250ml credit
            parts = line.split()
            if len(parts) >= 2:
                ml = int(parts[1])
                self._handle_coin_water(ml)
            return

        if line.startswith("COIN_CHARGE"):
            parts = line.split()
            if len(parts) >= 2:
                peso = int(parts[1])
                self._handle_coin_charge(peso)
            return

        # ---------------------------
        # DISPENSING EVENTS
        # ---------------------------
        if line == "CUP_DETECTED":
            self._handle_cup_detected()
            return

        if line == "CUP_REMOVED":
            self._handle_cup_removed()
            return

        if line == "DISPENSE_START":
            self._handle_dispense_start()
            return

        if line.startswith("DISPENSE_PROGRESS"):
            # Format: DISPENSE_PROGRESS ml=100 remaining=150
            try:
                parts = line.replace("DISPENSE_PROGRESS", "").strip().split()
                ml = int(parts[0].split("=")[1])
                rem = int(parts[1].split("=")[1])
                self._handle_dispense_progress(ml, rem)
            except Exception:
                pass
            return

        if line.startswith("DISPENSE_DONE"):
            parts = line.split()
            if len(parts) >= 2:
                ml = int(parts[1])
                self._handle_dispense_done(ml)
            return

        if line.startswith("CREDIT_LEFT"):
            parts = line.split()
            if len(parts) >= 2:
                ml = int(parts[1])
                self._handle_credit_left(ml)
            return

        # ---------------------------
        # MODE UPDATES
        # ---------------------------
        if line.startswith("MODE:"):
            mode = line.split(":")[1].strip()
            self._handle_mode(mode)
            return

        if line == "System reset.":
            _LOGGER.warning("Arduino reports: System Reset.")
            return

    # ============================================================
    # EVENT HANDLERS — these connect Arduino → UI → Services
    # ============================================================

    # ---------------- COIN INSERT ----------------
    def _handle_coin_insert(self, peso):
        uid = self.controller.active_uid
        if not uid:
            _LOGGER.warning("Coin inserted but no active user.")
            return
        # Prefer pushing a normalized Event into SessionManager/event_queue
        ev = Event(source="arduino", name="COIN", args={"value": int(peso), "uid": uid})
        self._emit_event(ev)
        # Also update local UI briefly
        try:
            # Try to record locally (safe no-op if not available)
            self.controller.record_coin_insert(uid, peso, 0)
            self.controller.show_coin_popup(uid, peso=peso)
        except Exception:
            pass
        _LOGGER.info("[COIN_EMIT] peso=%s uid=%s", peso, uid)

    # ---------------- WATER COIN ----------------
    def _handle_coin_water(self, ml):
        uid = self.controller.active_uid
        if not uid:
            _LOGGER.warning("Water coin but no active user.")
            return
        ev = Event(source="arduino", name="COIN", args={"value": int(ml), "uid": uid})
        self._emit_event(ev)
        try:
            self.controller.show_coin_popup(uid, added_ml=ml)
        except Exception:
            pass

    # ---------------- CHARGE COIN ----------------
    def _handle_coin_charge(self, peso):
        uid = self.controller.active_uid
        if not uid:
            _LOGGER.warning("Charge coin but no active user.")
            return
        # Emit coin event for billing/session handling
        ev = Event(source="arduino", name="COIN", args={"value": int(peso), "uid": uid, "type": "charge"})
        self._emit_event(ev)
        _LOGGER.info("[COIN_CHARGE_EMIT] peso=%s uid=%s", peso, uid)

    # ---------------- CUP EVENTS ----------------
    def _handle_cup_detected(self):
        ev = Event(source="arduino", name="CUP_DETECTED", args={})
        self._emit_event(ev)

    def _handle_cup_removed(self):
        ev = Event(source="arduino", name="CUP_REMOVED", args={})
        self._emit_event(ev)

    # ---------------- DISPENSING EVENTS ----------------
    def _handle_dispense_start(self):
        ev = Event(source="arduino", name="DISPENSE_START", args={})
        self._emit_event(ev)

    def _handle_dispense_progress(self, ml, remaining):
        ev = Event(source="arduino", name="DISPENSE_REPORT", args={"ml": int(ml), "remaining": int(remaining)})
        self._emit_event(ev)

    def _handle_dispense_done(self, total_ml):
        ev = Event(source="arduino", name="DISPENSE_DONE", args={"ml": int(total_ml)})
        self._emit_event(ev)

    def _handle_credit_left(self, ml_left):
        ev = Event(source="arduino", name="CREDIT_LEFT", args={"ml": int(ml_left)})
        self._emit_event(ev)

    def _emit_event(self, ev: Event) -> None:
        """Try to deliver Event into the system in order of preference:
        1. controller.event_queue (queue.Queue)
        2. controller.session_manager.event_queue
        3. fallback: call service handlers directly if available
        """
        # Preferred: event_queue on controller
        try:
            q = getattr(self.controller, "event_queue", None)
            if isinstance(q, queue.Queue):
                q.put_nowait(ev)
                _LOGGER.debug("Emitted event to controller.event_queue: %s", ev.short())
                return
        except Exception:
            _LOGGER.exception("Failed to put event into controller.event_queue")

        # Next: controller.session_manager.event_queue
        try:
            sm = getattr(self.controller, "session_manager", None)
            if sm and hasattr(sm, "event_queue") and isinstance(sm.event_queue, queue.Queue):
                sm.event_queue.put_nowait(ev)
                _LOGGER.debug("Emitted event to session_manager.event_queue: %s", ev.short())
                return
        except Exception:
            _LOGGER.exception("Failed to put event into session_manager.event_queue")

        # Fallback: call specific service handlers directly
        try:
            # WaterService handle_event
            ws = getattr(self.controller, "water_service", None)
            if ws and hasattr(ws, "handle_event"):
                try:
                    ws.handle_event(ev)
                    _LOGGER.debug("Delivered event to water_service.handle_event: %s", ev.short())
                    return
                except Exception:
                    _LOGGER.exception("water_service.handle_event failed")
        except Exception:
            pass

        try:
            bs = getattr(self.controller, "billing_service", None)
            if bs and hasattr(bs, "handle_coin_event") and ev.name.upper() == "COIN":
                try:
                    bs.handle_coin_event(ev)
                    _LOGGER.debug("Delivered coin event to billing_service.handle_coin_event: %s", ev.short())
                    return
                except Exception:
                    _LOGGER.exception("billing_service.handle_coin_event failed")
        except Exception:
            pass

        _LOGGER.warning("Dropping event (no delivery path): %s", ev.short())

    # ---------------- MODE CHANGE ----------------
    def _handle_mode(self, mode):
        if mode.upper() == "WATER":
            self.controller.water_mode = True
            self.controller.charge_mode = False
        elif mode.upper() == "CHARGE":
            self.controller.water_mode = False
            self.controller.charge_mode = True
        _LOGGER.info(f"Arduino switched to {mode} mode.")

