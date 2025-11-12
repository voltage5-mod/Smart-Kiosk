#!/usr/bin/env python3
"""
coin_acceptor_calibrator.py

Utility to read coin-acceptor pulse outputs via a Raspberry Pi GPIO pin,
calibrate pulses-per-coin, and run a live detector that prints detected coin values.

Features:
- Uses RPi.GPIO when available; falls back to a keyboard-simulated mode when running on non-Pi.
- Interactive calibration: prompt for coin denomination, collect multiple samples, store medians.
- Live detection: groups pulse bursts separated by a short inactivity timeout and maps pulses
  to the nearest calibrated denomination.
- Saves/loads calibration to/from `coin_calibration.json` in the script directory.

Usage examples:
  # Calibrate interactively on GPIO pin 17, 5 samples per denomination
  python coin_acceptor_calibrator.py --pin 17 --calibrate --samples 5

  # Run live detector using saved calibration
  python coin_acceptor_calibrator.py --pin 17

If RPi.GPIO isn't available this script will prompt you to press Enter to simulate
each coin insertion (useful for development on non-Pi machines).
"""

import time
import json
import argparse
import statistics
import os
import threading
import sys

CAL_FILE = os.path.join(os.path.dirname(__file__), 'coin_calibration.json')


class PulseReaderBase:
    def start(self):
        raise NotImplementedError()

    def stop(self):
        raise NotImplementedError()


class SimulatedPulseReader(PulseReaderBase):
    """Simulates pulse bursts by waiting for Enter key presses.
    Each press emits a configurable number of pulses (default 1) or prompts user for pulse count.
    """
    def __init__(self):
        self._running = False
        self._callback = None

    def start(self, callback):
        self._callback = callback
        self._running = True
        print("Running in SIMULATION mode. Press Enter to simulate a coin insert (or type a pulse count and Enter). Ctrl-C to quit.")
        try:
            while self._running:
                line = input()
                if not self._running:
                    break
                line = line.strip()
                try:
                    pulses = int(line) if line else 1
                except Exception:
                    pulses = 1
                # call callback with pulses
                callback(pulses)
        except (KeyboardInterrupt, EOFError):
            self._running = False

    def stop(self):
        self._running = False


class RPiPulseReader(PulseReaderBase):
    """Reads pulses from a GPIO pin using RPi.GPIO. Groups pulses by inactivity timeout
    and invokes a callback with the pulse count for each burst.
    """
    def __init__(self, pin, bounce_time=5, group_timeout=0.5):
        self.pin = pin
        self.bounce_time = bounce_time
        self.group_timeout = group_timeout
        self._running = False
        self._pulses = 0
        self._last_pulse = None
        self._lock = threading.Lock()
        self._callback = None
        try:
            import RPi.GPIO as GPIO
            self.GPIO = GPIO
        except Exception as e:
            raise RuntimeError('RPi.GPIO not available') from e

    def _edge_cb(self, channel):
        with self._lock:
            self._pulses += 1
            self._last_pulse = time.time()

    def _monitor_loop(self):
        # monitor for grouped pulses separated by group_timeout
        while self._running:
            time.sleep(0.05)
            with self._lock:
                if self._pulses > 0 and self._last_pulse is not None:
                    if (time.time() - self._last_pulse) >= self.group_timeout:
                        pulses = self._pulses
                        self._pulses = 0
                        self._last_pulse = None
                        # call callback outside lock
                        callback = self._callback
                        if callback:
                            try:
                                callback(pulses)
                            except Exception:
                                pass

    def start(self, callback):
        self._callback = callback
        self._running = True
        GPIO = self.GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        # Use event detect for rising edges
        GPIO.add_event_detect(self.pin, GPIO.RISING, callback=self._edge_cb, bouncetime=self.bounce_time)
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def stop(self):
        self._running = False
        try:
            self._monitor_thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            self.GPIO.remove_event_detect(self.pin)
        except Exception:
            pass
        try:
            self.GPIO.cleanup(self.pin)
        except Exception:
            pass


def load_calibration(path=CAL_FILE):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_calibration(cal, path=CAL_FILE):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cal, f, indent=2)
        print(f"Saved calibration to {path}")
    except Exception as e:
        print('Failed to save calibration:', e)


def find_nearest(pulse_count, calibration):
    # calibration: dict of denomination(str)->median_pulses
    best = None
    best_diff = None
    for denom_str, median in calibration.items():
        try:
            median_v = float(median)
        except Exception:
            continue
        diff = abs(pulse_count - median_v)
        if best is None or diff < best_diff:
            best = denom_str
            best_diff = diff
    return best, best_diff


def interactive_calibrate(reader, samples_per_denom=5):
    print('Interactive calibration mode')
    cal = load_calibration()
    print('Existing calibration:', cal)
    while True:
        denom = input('Enter denomination label (e.g. 1,5,10) or blank to finish: ').strip()
        if denom == '':
            break
        print(f'Please insert the coin for denomination {denom} {samples_per_denom} times when prompted. Press Enter to start each sample.')
        samples = []
        for i in range(samples_per_denom):
            input(f'Press Enter and insert coin sample #{i+1} for denomination {denom}...')
            # wait for a pulse event from reader; the reader callback will collect pulses and call a handler
            got = []

            def _cb(pulses):
                got.append(pulses)

            # run a short listener to collect one burst
            t = threading.Thread(target=lambda: reader.start(_cb) if isinstance(reader, SimulatedPulseReader) else None)
            # If using RPiPulseReader, it should already be started externally; we rely on callback to populate 'got'
            if isinstance(reader, SimulatedPulseReader):
                # For SimulatedPulseReader, start blocks; so run start in thread until it reads one line
                t.daemon = True
                t.start()
                # wait until got is filled or timeout
                timeout = 10.0
                waited = 0.0
                while waited < timeout and not got:
                    time.sleep(0.1)
                    waited += 0.1
                # stop simulated reader
                try:
                    reader.stop()
                except Exception:
                    pass
            else:
                # RPi mode: wait for data to be received via callback
                timeout = 10.0
                waited = 0.0
                while waited < timeout and not got:
                    time.sleep(0.1)
                    waited += 0.1

            if got:
                print(f'Recorded pulses: {got[0]}')
                samples.append(got[0])
            else:
                print('Timed out waiting for pulses. Try again.')
        if samples:
            median = int(statistics.median(samples))
            cal[denom] = median
            print(f'Denom {denom} median pulses: {median}')
            save_calibration(cal)
        else:
            print('No successful samples collected for this denomination.')
    print('Calibration complete. Final calibration:', cal)
    return cal


def run_detector(reader, calibration):
    print('Starting live detector. Press Ctrl-C to quit.')

    def on_burst(pulses):
        denom, diff = find_nearest(pulses, calibration) if calibration else (None, None)
        ts = time.strftime('%Y-%m-%d %H:%M:%S')
        if denom is not None:
            print(f"{ts} - Detected burst: {pulses} pulses -> denomination {denom} (diff {diff:.1f})")
        else:
            print(f"{ts} - Detected burst: {pulses} pulses -> unknown (no calibration)")

    try:
        # For SimulatedPulseReader, start() blocks; run it in main thread
        reader.start(on_burst)
    except KeyboardInterrupt:
        print('Exiting...')
    except Exception as e:
        print('Detector error:', e)
    finally:
        try:
            reader.stop()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description='Coin acceptor pulse calibrator/detector')
    parser.add_argument('--pin', type=int, default=17, help='GPIO BCM pin for pulse input (default 17)')
    parser.add_argument('--simulate', action='store_true', help='Run in simulation mode (no RPi.GPIO)')
    parser.add_argument('--calibrate', action='store_true', help='Enter interactive calibration mode')
    parser.add_argument('--samples', type=int, default=5, help='Samples per denomination during calibration')
    parser.add_argument('--outfile', type=str, default=CAL_FILE, help='Calibration output file')
    args = parser.parse_args()

    # load calibration if available
    cal = load_calibration(args.outfile)

    # choose reader
    reader = None
    if args.simulate:
        reader = SimulatedPulseReader()
    else:
        try:
            reader = RPiPulseReader(pin=args.pin)
        except Exception as e:
            print('RPi.GPIO not available or GPIO init failed; falling back to SIMULATION mode. Error:', e)
            reader = SimulatedPulseReader()

    # If calibration mode: ensure reader is running for RPi (start reader but keep detector callback separate)
    if args.calibrate:
        # For RPi reader, start monitoring thread so callbacks populate; for simulated, we'll start/stop per sample
        if isinstance(reader, RPiPulseReader):
            # start the reader with a no-op callback; interactive_calibrate will override per-sample
            reader.start(lambda pulses: None)
        cal = interactive_calibrate(reader, samples_per_denom=args.samples)
        save_calibration(cal, args.outfile)
        # stop reader if RPi
        try:
            reader.stop()
        except Exception:
            pass
        return

    # Live detector
    cal = load_calibration(args.outfile)  # reload
    try:
        run_detector(reader, cal)
    finally:
        try:
            reader.stop()
        except Exception:
            pass


if __name__ == '__main__':
    main()
