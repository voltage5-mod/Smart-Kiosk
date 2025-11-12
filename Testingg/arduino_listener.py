"""ArduinoListener

Listens to an Arduino over serial and emits structured events.
- Uses pyserial if available.
- Provide register_callback(cb) where cb(event_dict) will be called for parsed events.
- Safe reconnect/backoff and graceful stop().

This module intentionally keeps side-effects optional; the example firebase handler
shows how to write to Firebase using existing helpers.
"""

import threading
import time
import re
from typing import Callable, Dict, Any, List, Optional

# optional firebase helpers from your project
try:
    from firebase_helpers import append_audit_log, users_ref
except Exception:
    append_audit_log = None
    users_ref = None

try:
    import serial  # type: ignore
except Exception:
    serial = None

DEFAULT_PORT = '/dev/ttyACM0'
DEFAULT_BAUD = 115200


class ArduinoListener(threading.Thread):
    def __init__(self, port: str = DEFAULT_PORT, baud: int = DEFAULT_BAUD, simulate: bool = False):
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.simulate = simulate
        self._stop = threading.Event()
        self._ser = None
        self._lock = threading.Lock()
        self._callbacks: List[Callable[[Dict[str, Any]], None]] = []
        # coin pulse calibration reported by Arduino (populated from CFG: line)
        # Example: {'coin1P':1, 'coin5P':5, 'coin10P':10, 'pulsesPerLiter':450.0}
        self._coin_cfg: Dict[str, Any] = {}

    def register_callback(self, cb: Callable[[Dict[str, Any]], None]):
        self._callbacks.append(cb)

    def _dispatch(self, ev: Dict[str, Any]):
        for cb in list(self._callbacks):
            try:
                cb(ev)
            except Exception:
                # callback errors shouldn't kill the listener
                pass

    def _parse_line(self, line: str) -> Optional[Dict[str, Any]]:
        if not line:
            return None
        line = line.strip()
        # Define regex-driven parsers for expected Arduino messages
        # Support multiple Arduino message formats (human-readable and compact tokens)
        m = re.search(r'Coin detected, new credit:\s*(\d+)\s*ml', line, re.I)
        if m:
            return {"event": "COIN_INSERTED", "volume_ml": int(m.group(1)), "raw": line}
        m = re.search(r'COIN_WATER\s+(\d+)', line, re.I)
        if m:
            return {"event": "COIN_INSERTED", "volume_ml": int(m.group(1)), "raw": line}
        m = re.search(r'COIN_CHARGE\s+(\d+)', line, re.I)
        if m:
            return {"event": "COIN_CHARGE", "peso": int(m.group(1)), "raw": line}
        # UNKNOWN_COIN <pulses> : Arduino couldn't match pulses to known coin counts
        m = re.search(r'UNKNOWN_COIN\s+(\d+)', line, re.I)
        if m:
            pulses = int(m.group(1))
            # If we have calibration info from CFG, attempt to map pulses to a coin
            try:
                cfg = self._coin_cfg
                if cfg:
                    # compute closest among coin1P, coin5P, coin10P
                    candidates = []
                    for k, peso in (('coin1P', 1), ('coin5P', 5), ('coin10P', 10)):
                        if k in cfg:
                            candidates.append((abs(pulses - int(cfg[k])), peso))
                    if candidates:
                        candidates.sort()
                        # choose best match
                        best_peso = candidates[0][1]
                        # For water, map peso to ml using common mapping
                        ml_map = {1: 100, 5: 500, 10: 1000}
                        return {"event": "COIN_INSERTED", "peso": best_peso, "volume_ml": ml_map.get(best_peso), "raw": line}
            except Exception:
                pass
            # fallback: emit UNKNOWN_COIN event
            return {"event": "COIN_UNKNOWN", "pulses": pulses, "raw": line}
        m = re.search(r'CREDIT_ML:\s*(\d+)', line, re.I)
        if m:
            return {"event": "CREDIT_UPDATE", "credit_ml": int(m.group(1)), "raw": line}
        m = re.search(r'Dispensing complete.*?(\d+(?:\.\d+)?)\s*ml', line, re.I)
        if m:
            return {"event": "DISPENSING_DONE", "total_ml": float(m.group(1)), "raw": line}
        m = re.search(r'DISPENSING:.*?YES', line, re.I)
        if m:
            return {"event": "DISPENSING_STARTED", "raw": line}
        m = re.search(r'FLOW_PULSES:\s*(\d+)', line, re.I)
        if m:
            return {"event": "FLOW_PULSES", "pulses": int(m.group(1)), "raw": line}
        # CREDIT_LEFT <ml> -- used by some sketches to report remaining credit
        m = re.search(r'CREDIT_LEFT\s+(\d+)', line, re.I)
        if m:
            return {"event": "CREDIT_UPDATE", "credit_ml": int(m.group(1)), "raw": line}
        # DISPENSE_PROGRESS ml=<float> remaining=<float>
        m = re.search(r'DISPENSE_PROGRESS\s+ml=(\d+(?:\.\d+)?)\s+remaining=(\d+(?:\.\d+)?)', line, re.I)
        if m:
            return {"event": "DISPENSE_PROGRESS", "dispensed_ml": float(m.group(1)), "remaining_ml": float(m.group(2)), "raw": line}
        # CFG: coin1P=1 coin5P=5 coin10P=10 pulsesPerLiter=450
        m = re.search(r'CFG:\s*coin1P=(\d+)\s*coin5P=(\d+)\s*coin10P=(\d+)\s*pulsesPerLiter=([0-9\.]+)', line, re.I)
        if m:
            try:
                self._coin_cfg = {
                    'coin1P': int(m.group(1)),
                    'coin5P': int(m.group(2)),
                    'coin10P': int(m.group(3)),
                    'pulsesPerLiter': float(m.group(4)),
                }
            except Exception:
                self._coin_cfg = {}
            return {"event": "CFG", "cfg": self._coin_cfg, "raw": line}
        if 'Cup detected' in line or re.search(r'\bCUP_DETECTED\b', line, re.I):
            return {"event": "CUP_DETECTED", "raw": line}
        if 'Cup removed' in line or re.search(r'\bCUP_REMOVED\b', line, re.I):
            return {"event": "CUP_REMOVED", "raw": line}
        if re.search(r'\bDISPENSE_START\b|\bDISPENSE_STARTED\b', line, re.I) or re.search(r'DISPENSE_START', line):
            return {"event": "DISPENSING_STARTED", "raw": line}
        m = re.search(r'DISPENSE_DONE\s*(\d+(?:\.\d+)?)', line, re.I)
        if m:
            return {"event": "DISPENSING_DONE", "total_ml": float(m.group(1)), "raw": line}
        # Fallback: return RAW event so higher layers can log/unpack details
        return {"event": "RAW", "raw": line}

    def _connect_serial(self):
        if self.simulate or serial is None:
            return None
        try:
            s = serial.Serial(self.port, self.baud, timeout=1)
            # allow Arduino to reset and warm up
            time.sleep(2)
            return s
        except Exception:
            return None

    def send_command(self, cmd: str) -> bool:
        """Send a command string to the Arduino (appends newline). Returns True on success."""
        if self.simulate:
            # in simulate mode, just noop
            return True
        if serial is None:
            return False
        try:
            with self._lock:
                if self._ser is None:
                    # attempt to open a connection if not already open
                    self._ser = self._connect_serial()
                if self._ser is None:
                    return False
                # ensure bytes
                out = cmd.encode() if isinstance(cmd, str) else cmd
                if not out.endswith(b"\n"):
                    out = out + b"\n"
                self._ser.write(out)
                try:
                    self._ser.flush()
                except Exception:
                    pass
                return True
        except Exception:
            try:
                if self._ser:
                    self._ser.close()
            except Exception:
                pass
            self._ser = None
            return False

    def stop(self):
        self._stop.set()
        try:
            if self._ser:
                try:
                    self._ser.close()
                except Exception:
                    pass
        except Exception:
            pass

    def run(self):
        backoff = 1.0
        while not self._stop.is_set():
            if self.simulate:
                # in simulate mode, we sleep and let tests call _parse_line directly
                time.sleep(0.2)
                continue
            if self._ser is None:
                self._ser = self._connect_serial()
                if self._ser is None:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
                    continue
                backoff = 1.0
            try:
                raw = self._ser.readline().decode(errors='ignore').strip()
                if not raw:
                    continue
                ev = self._parse_line(raw)
                if ev:
                    self._dispatch(ev)
            except Exception:
                try:
                    if self._ser:
                        self._ser.close()
                except Exception:
                    pass
                self._ser = None
                time.sleep(1.0)


# Example firebase handler (optional)
def firebase_handler(event: Dict[str, Any]):
    typ = event.get('event')
    # NOTE: This example needs an application-specific mapping from Arduino events to user/session
    if typ == 'COIN_INSERTED':
        # In your app you likely have controller.active_uid
        uid = None
        try:
            # attempt to pick a default user if available in env (not implemented)
            # append_audit_log(actor=uid, action='arduino_coin', meta={'ml': event['volume_ml']})
            if users_ref is not None and uid is not None:
                users_ref.child(uid).update({'temp_water_time': event['volume_ml']})
        except Exception:
            pass


if __name__ == '__main__':
    # Simple CLI runner: prints parsed events to stdout.
    import argparse

    parser = argparse.ArgumentParser(description='ArduinoListener CLI - print parsed Arduino events')
    parser.add_argument('--port', '-p', help='serial port (e.g. /dev/ttyACM0)', default=DEFAULT_PORT)
    parser.add_argument('--baud', '-b', help='baud rate', type=int, default=DEFAULT_BAUD)
    parser.add_argument('--simulate', action='store_true', help='run in simulate mode (no serial)')
    args = parser.parse_args()

    port = args.port if not args.simulate else None
    L = ArduinoListener(port=port, baud=args.baud, simulate=args.simulate)

    # register firebase handler if available
    try:
        L.register_callback(firebase_handler)
    except Exception:
        pass

    # printing callback for CLI
    def _printer(ev: Dict[str, Any]):
        try:
            print(f"EVENT: {ev.get('event')} - { {k:v for k,v in ev.items() if k!='event'} }")
        except Exception:
            print(f"RAW EVENT: {ev}")

    L.register_callback(_printer)
    L.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print('Stopping listener...')
        L.stop()
