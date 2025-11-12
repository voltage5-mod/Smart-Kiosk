"""
Coin Acceptor Calibrator

- Connect the coin acceptor to an Arduino digital pin and upload the provided
  `coin_pulse_reader.ino` sketch (or ensure your existing Arduino prints "PULSE" per pulse).
- Run this script on the Raspberry Pi and provide the serial port (e.g. /dev/ttyACM0).

What it does:
- Listens to serial lines from the Arduino and collects pulse timestamps.
- Groups pulses into coin events based on inter-pulse timeout (default 200ms).
- Prints detected events with pulse count and allows you to label the pulse-count -> coin value.
- Saves mapping to `coin_mapping.json` next to this script.

Usage:
  python coin_calibrator.py --port /dev/ttyACM0
  python coin_calibrator.py --simulate   # simulate pulses locally

"""
import argparse
import json
import os
import queue
import threading
import time

try:
    import serial
except Exception:
    serial = None

BASE = os.path.dirname(__file__)
MAPPING_FILE = os.path.join(BASE, 'coin_mapping.json')

# Defaults
INTER_PULSE_MS = 200  # pulses closer than this are part of the same coin event
EVENT_GAP_MS = 500    # gap to consider event finished if no further pulses


def load_mapping():
    if os.path.exists(MAPPING_FILE):
        try:
            return json.load(open(MAPPING_FILE, 'r', encoding='utf-8'))
        except Exception:
            return {}
    return {}


def save_mapping(mapping):
    try:
        json.dump(mapping, open(MAPPING_FILE, 'w', encoding='utf-8'), indent=2)
        print(f"Saved mapping to {MAPPING_FILE}")
    except Exception as e:
        print("Failed to save mapping:", e)


class SerialPulseReader(threading.Thread):
    def __init__(self, port, baud=115200, q=None, simulate=False):
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.q = q or queue.Queue()
        self.simulate = simulate
        self._stop = threading.Event()
        self.ser = None

    def run(self):
        if self.simulate:
            print("Running in simulate mode. Type ENTER to emit a single pulse, or 'b' then ENTER to emit burst.")
            while not self._stop.is_set():
                try:
                    s = input()
                except EOFError:
                    break
                if s.strip().lower() == 'b':
                    # emit a short burst of 3 pulses
                    for _ in range(3):
                        self.q.put(time.time())
                        time.sleep(0.08)
                else:
                    self.q.put(time.time())
            return

        if serial is None:
            print('pyserial not available. Install with: pip install pyserial')
            return
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=1)
            print(f'Opened {self.port} @ {self.baud}')
        except Exception as e:
            print('Failed to open serial port:', e)
            return
        try:
            while not self._stop.is_set():
                try:
                    line = self.ser.readline()
                except Exception:
                    break
                if not line:
                    continue
                try:
                    text = line.decode('utf-8', errors='ignore').strip()
                except Exception:
                    text = str(line)
                if not text:
                    continue
                # Accept lines that contain PULSE (sent by Arduino on each pulse)
                if 'PULSE' in text.upper():
                    self.q.put(time.time())
                else:
                    # also accept numeric tokens representing pulses
                    if text.isdigit():
                        self.q.put(time.time())
                    else:
                        # print other debug lines so user can see them
                        print('SERIAL:', text)
        finally:
            try:
                if self.ser and self.ser.is_open:
                    self.ser.close()
            except Exception:
                pass

    def stop(self):
        self._stop.set()
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass


def calibrate(port=None, baud=115200, simulate=False):
    pulse_q = queue.Queue()
    reader = SerialPulseReader(port=port, baud=baud, q=pulse_q, simulate=simulate)
    reader.start()

    mapping = load_mapping()
    print('Current mapping (pulse_count -> peso):', mapping)
    print('Waiting for pulses... (Ctrl+C to quit)')

    # event accumulation
    current_event = []  # timestamps
    last_ts = None
    totals = {}

    try:
        while True:
            try:
                ts = pulse_q.get(timeout=0.2)
            except queue.Empty:
                # check for event timeout
                if current_event and (time.time() - (current_event[-1])) * 1000.0 > EVENT_GAP_MS:
                    # finalize event
                    count = len(current_event)
                    print('\nDetected coin event: pulses=', count)
                    # map if known
                    mapped = mapping.get(str(count))
                    if mapped is not None:
                        print(f"Mapped to ₱{mapped}")
                        totals[mapped] = totals.get(mapped, 0) + 1
                        print('Totals so far:', totals)
                    else:
                        print('Unmapped pulse-count. Please enter coin value in pesos (e.g. 1,5,10) or press ENTER to skip:')
                        val = input().strip()
                        if val:
                            try:
                                peso = int(val)
                                mapping[str(count)] = peso
                                totals[peso] = totals.get(peso, 0) + 1
                                save_mapping(mapping)
                                print('Saved mapping. Totals:', totals)
                            except Exception:
                                print('Invalid value, skipped.')
                        else:
                            print('Skipped labeling this event.')
                    current_event = []
                continue

            # got a pulse timestamp
            if last_ts is None:
                current_event = [ts]
                last_ts = ts
                print('.', end='', flush=True)
            else:
                # check inter-pulse interval
                dt_ms = (ts - last_ts) * 1000.0
                if dt_ms <= INTER_PULSE_MS:
                    current_event.append(ts)
                else:
                    # gap larger than threshold -> finalize previous event first
                    count = len(current_event)
                    print('\nDetected coin event: pulses=', count)
                    mapped = mapping.get(str(count))
                    if mapped is not None:
                        print(f"Mapped to ₱{mapped}")
                        totals[mapped] = totals.get(mapped, 0) + 1
                        print('Totals so far:', totals)
                    else:
                        print('Unmapped pulse-count. Please enter coin value in pesos (e.g. 1,5,10) or press ENTER to skip:')
                        val = input().strip()
                        if val:
                            try:
                                peso = int(val)
                                mapping[str(count)] = peso
                                totals[peso] = totals.get(peso, 0) + 1
                                save_mapping(mapping)
                                print('Saved mapping. Totals:', totals)
                            except Exception:
                                print('Invalid value, skipped.')
                        else:
                            print('Skipped labeling this event.')
                    # start new event
                    current_event = [ts]
                last_ts = ts
    except KeyboardInterrupt:
        print('\nExiting...')
    finally:
        reader.stop()
        print('Final totals:', totals)
        save_mapping(mapping)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--port', '-p', help='Serial port (e.g. /dev/ttyACM0)')
    p.add_argument('--baud', '-b', type=int, default=115200)
    p.add_argument('--simulate', action='store_true', help='Run in keyboard-simulate mode')
    args = p.parse_args()

    if args.simulate:
        calibrate(port=None, baud=args.baud, simulate=True)
    else:
        if not args.port:
            print('Please provide --port or use --simulate')
        else:
            calibrate(port=args.port, baud=args.baud, simulate=False)
