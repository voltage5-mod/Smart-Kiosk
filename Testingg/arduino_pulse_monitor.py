"""
arduino_pulse_monitor.py

Listen to an Arduino serial port and print structured output useful during
hardware integration and coin-acceptor calibration.

Features:
- Timestamped raw line dump
- Regex-based parsing for common message formats emitted by sketches
  (e.g., "COIN_WATER 500", "COIN_CHARGE 10", "Coin inserted: 5P added 500mL, new total: 500")
- Optional pulse analyzer mode (group pulse lines into bursts and show counts/intervals)
- Optional logging to file

Usage examples:
python arduino_pulse_monitor.py --port /dev/ttyACM0 --baud 115200
python arduino_pulse_monitor.py --port COM3 --baud 115200 --pulse-mode --log pulses.log

"""
import argparse
import re
import time
import sys

try:
    import serial
except Exception:
    serial = None

# Patterns to recognize common Arduino outputs
_PATTERNS = [
    (re.compile(r"^COIN_WATER\s+(\d+)$"), 'COIN_WATER'),
    (re.compile(r"^COIN_CHARGE\s+(\d+)$"), 'COIN_CHARGE'),
    (re.compile(r"Coin inserted:\s*(\d+)P\s+added\s*(\d+)mL", re.IGNORECASE), 'COIN_HUMAN'),
    (re.compile(r"PULSE\b(?::\s*(\d+))?", re.IGNORECASE), 'PULSE'),
    (re.compile(r"FLOW_PULSES\s*[:=]?\s*(\d+)", re.IGNORECASE), 'FLOW_PULSES'),
]


class PulseAnalyzer:
    """Collects pulse timestamps and groups bursts into coin events.

    Behavior:
    - Whenever a pulse is recorded, we append its timestamp.
    - We consider pulses part of the same burst if the inter-pulse gap is <= burst_gap_s.
    - When a gap larger than burst_gap_s occurs, we emit a burst summary (count, duration, avg_freq).
    """

    def __init__(self, burst_gap_s=0.1, max_idle_s=2.0):
        self.timestamps = []
        self.burst_gap_s = burst_gap_s
        self.max_idle_s = max_idle_s
        self._last_emit = time.time()

    def pulse(self, ts=None):
        if ts is None:
            ts = time.time()
        self.timestamps.append(ts)
        self._last_emit = ts

    def maybe_emit(self):
        """If pulses are idle for > max_idle_s, emit a burst summary and reset."""
        if not self.timestamps:
            return None
        now = time.time()
        if (now - self._last_emit) >= self.max_idle_s:
            # analyze existing timestamps and create bursts by burst_gap_s
            bursts = []
            current = [self.timestamps[0]]
            for t in self.timestamps[1:]:
                if (t - current[-1]) <= self.burst_gap_s:
                    current.append(t)
                else:
                    bursts.append(current)
                    current = [t]
            if current:
                bursts.append(current)

            # Create summaries
            summaries = []
            for b in bursts:
                count = len(b)
                duration = b[-1] - b[0] if count > 1 else 0.0
                avg_freq = (count - 1) / duration if duration > 0 else float('inf')
                summaries.append({'count': count, 'duration': duration, 'avg_freq': avg_freq, 'start': b[0], 'end': b[-1]})

            # reset
            self.timestamps = []
            return summaries
        return None


def parse_line(line):
    """Try to parse known patterns from a serial line and return a dict.

    Returns dict with keys: event (str), groups (tuple), raw (str)
    """
    s = line.strip()
    for pat, name in _PATTERNS:
        m = pat.search(s)
        if m:
            return {'event': name, 'groups': m.groups(), 'raw': s}
    # fallback: if numeric
    if s.isdigit():
        return {'event': 'NUMBER', 'groups': (s,), 'raw': s}
    return {'event': 'RAW', 'groups': (), 'raw': s}


def human_time(ts=None):
    if ts is None:
        ts = time.time()
    t = time.localtime(ts)
    ms = int((ts - int(ts)) * 1000)
    return time.strftime(f"%H:%M:%S.{ms:03d}", t)


def main(argv=None):
    p = argparse.ArgumentParser(description='Arduino serial + pulse monitor for coin acceptor calibration')
    p.add_argument('--port', '-p', required=True, help='Serial port (e.g. /dev/ttyACM0 or COM3)')
    p.add_argument('--baud', '-b', type=int, default=115200, help='Baud rate')
    p.add_argument('--pulse-mode', action='store_true', help='Enable pulse analyzer (group pulses into bursts)')
    p.add_argument('--burst-gap', type=float, default=0.1, help='Max seconds between pulses in same burst')
    p.add_argument('--idle', type=float, default=1.5, help='Idle seconds to flush pulse burst')
    p.add_argument('--log', help='Append output to a log file')
    args = p.parse_args(argv)

    if serial is None:
        print('pyserial is required. Install with: pip install pyserial')
        sys.exit(1)

    ser = None
    try:
        ser = serial.Serial(args.port, args.baud, timeout=1)
        print(f"Opened {args.port} @ {args.baud}")
    except Exception as e:
        print('Failed to open serial port:', e)
        sys.exit(2)

    logfh = None
    if args.log:
        try:
            logfh = open(args.log, 'a', encoding='utf-8')
        except Exception as e:
            print('Failed to open log file:', e)
            logfh = None

    pa = PulseAnalyzer(burst_gap_s=args.burst_gap, max_idle_s=args.idle) if args.pulse_mode else None

    try:
        while True:
            try:
                raw = ser.readline()
            except Exception as e:
                print('Serial read error:', e)
                break
            if not raw:
                # periodically allow pulse analyzer to flush
                if pa:
                    summaries = pa.maybe_emit()
                    if summaries:
                        for s in summaries:
                            out = f"[{human_time()}] PULSE_BURST count={s['count']} dur={s['duration']:.3f}s avg_freq={s['avg_freq']:.1f}Hz"
                            print(out)
                            if logfh:
                                logfh.write(out + '\n')
                                logfh.flush()
                continue
            try:
                line = raw.decode('utf-8', errors='replace').rstrip('\r\n')
            except Exception:
                line = str(raw)
            ts = time.time()
            h = human_time(ts)
            parsed = parse_line(line)
            # If pulse mode and a pulse-like event, feed analyzer
            if pa and parsed.get('event') in ('PULSE', 'NUMBER'):
                # interpret 'NUMBER' as a pulse event when a single-digit or pulse count appears
                pa.pulse(ts)
                print(f"[{h}] RAW -> {parsed.get('event')} : {parsed.get('raw')}")
                if logfh:
                    logfh.write(f"[{h}] RAW -> {parsed.get('event')} : {parsed.get('raw')}\n")
                continue

            # Normal printing: structured when matched
            if parsed['event'] == 'RAW':
                out = f"[{h}] RAW: {parsed['raw']}"
            elif parsed['event'] == 'COIN_WATER':
                ml = parsed['groups'][0]
                out = f"[{h}] COIN_WATER: {ml} ml"
            elif parsed['event'] == 'COIN_CHARGE':
                peso = parsed['groups'][0]
                out = f"[{h}] COIN_CHARGE: ₱{peso}"
            elif parsed['event'] == 'COIN_HUMAN':
                peso, ml = parsed['groups']
                out = f"[{h}] COIN_HUMAN: ₱{peso} -> {ml} ml"
            elif parsed['event'] == 'FLOW_PULSES':
                pulses = parsed['groups'][0]
                out = f"[{h}] FLOW_PULSES: {pulses}"
            elif parsed['event'] == 'PULSE':
                # PULSE with optional count
                cnt = parsed['groups'][0] if parsed['groups'] and parsed['groups'][0] else '1'
                out = f"[{h}] PULSE ({cnt})"
                if pa:
                    pa.pulse(ts)
            elif parsed['event'] == 'NUMBER':
                out = f"[{h}] NUMBER: {parsed['raw']}"
            else:
                out = f"[{h}] {parsed['event']}: {parsed['raw']}"

            print(out)
            if logfh:
                logfh.write(out + '\n')
                logfh.flush()

    except KeyboardInterrupt:
        print('\nExiting on user request')
    finally:
        try:
            if ser and ser.is_open:
                ser.close()
        except Exception:
            pass
        if logfh:
            logfh.close()


if __name__ == '__main__':
    main()
