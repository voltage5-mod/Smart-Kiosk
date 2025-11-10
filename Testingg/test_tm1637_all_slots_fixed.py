#!/usr/bin/env python3
"""
Improved TM1637 Multi-Slot Display Test (fixed)

This script provides a more robust per-slot worker-thread approach instead of
relying on repeated threading.Timer scheduling which can sometimes be fragile
in constrained environments.

Behavior:
 - Starts one thread per slot that loops once per second.
 - Updates each slot's TM1637 display with MM:SS and prints concise logs.
 - Supports --real flag to use real GPIO or sim mode for local testing.
 - Graceful shutdown on KeyboardInterrupt or timeout.

Usage examples:
  python3 test_tm1637_all_slots_fixed.py --real --duration 30
  python3 test_tm1637_all_slots_fixed.py --duration 20

"""
import time
import threading
import json
import os
import sys
import argparse
from typing import Dict

BASE = os.path.dirname(__file__)
PINMAP_PATH = os.path.join(BASE, 'pinmap.json')

try:
    with open(PINMAP_PATH, 'r', encoding='utf-8') as f:
        PINMAP = json.load(f)
except Exception:
    PINMAP = {}

try:
    from hardware_gpio import HardwareGPIO
    from hardware_gpio import TM1637Display
except Exception:
    HardwareGPIO = None
    TM1637Display = None


def make_display(hw, slot, clk, dio, brightness=2):
    """Return an object with show_time(seconds) and set_brightness(level).
    Falls back to a safe no-op object in sim or on error.
    """
    # Try external library wrapper if available
    try:
        import tm1637 as _tm_lib
        disp = _tm_lib.TM1637(clk=clk, dio=dio)

        class Wrapper:
            def __init__(self, d):
                self.d = d
            def show_time(self, seconds: int):
                mm = seconds // 60
                ss = seconds % 60
                try:
                    if hasattr(self.d, 'numbers'):
                        self.d.numbers(mm, ss)
                    elif hasattr(self.d, 'show'):
                        self.d.show(f"{mm:02d}:{ss:02d}")
                except Exception:
                    pass
            def set_brightness(self, level: int):
                try:
                    if hasattr(self.d, 'brightness'):
                        self.d.brightness(level)
                except Exception:
                    pass
        w = Wrapper(disp)
        try:
            w.set_brightness(brightness)
        except Exception:
            pass
        return w
    except Exception:
        # Fallback to internal driver if available
        try:
            if TM1637Display is not None:
                d = TM1637Display(clk_pin=clk, dio_pin=dio, gpio=(getattr(hw, 'gpio', None) if hw else None), mode=(getattr(hw, 'mode', 'sim') if hw else 'sim'))
                try:
                    d.set_brightness(brightness)
                except Exception:
                    pass
                return d
        except Exception:
            pass

    # final fallback: safe no-op display
    class Noop:
        def show_time(self, seconds: int):
            return False
        def set_brightness(self, level: int):
            return False
    return Noop()


class SlotWorker(threading.Thread):
    def __init__(self, hw, slot_name: str, start_seconds: int, display, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.hw = hw
        self.slot = slot_name
        self.remaining = start_seconds
        self.display = display
        self.stop_event = stop_event
        self._running = False

    def run(self):
        self._running = True
        # try to power slot (best-effort)
        try:
            if self.hw is not None:
                self.hw.relay_on(self.slot)
        except Exception:
            pass

        print(f"[WORKER] {self.slot}: started, {self.remaining}s")
        last_print = time.time()
        while not self.stop_event.is_set() and self.remaining > 0:
            t0 = time.time()
            # update display
            try:
                self.display.show_time(self.remaining)
            except Exception:
                pass

            # periodic console status every 5s or when near end
            if (self.remaining % 5 == 0) or (self.remaining <= 3) or (time.time() - last_print) > 5:
                print(f"[TICK] {self.slot}: {self.remaining}s")
                last_print = time.time()

            # sleep until next whole second while allowing early exit
            to_sleep = 1.0 - ((time.time() - t0) % 1.0)
            if to_sleep < 0.01:
                to_sleep = 1.0
            try:
                self.stop_event.wait(timeout=to_sleep)
            except Exception:
                pass

            if self.stop_event.is_set():
                break

            self.remaining = max(0, self.remaining - 1)

        # end of worker
        try:
            if self.hw is not None:
                self.hw.relay_off(self.slot)
        except Exception:
            pass
        print(f"[WORKER] {self.slot}: finished (remaining {self.remaining}s)")
        self._running = False


def main():
    parser = argparse.ArgumentParser(description='Robust TM1637 multi-slot test (worker threads)')
    parser.add_argument('--real', action='store_true', help='Use real GPIO (RPi)')
    parser.add_argument('--duration', type=int, default=30, help='Seconds per slot')
    parser.add_argument('--brightness', type=int, default=2, help='Brightness 0-7')
    parser.add_argument('--timeout', type=int, default=0, help='Optional global timeout in seconds (0 = no timeout)')
    args = parser.parse_args()

    mode = 'real' if args.real else 'sim'
    print(f"Mode: {mode}, duration={args.duration}, brightness={args.brightness}")

    hw = None
    if HardwareGPIO is not None:
        try:
            hw = HardwareGPIO(pinmap=PINMAP, mode=mode)
            hw.setup()
        except Exception as e:
            print('Hardware init failed:', e)
            hw = None

    durations = {f'slot{i}': args.duration for i in range(1,5)}

    stop_event = threading.Event()
    workers = []

    # prepare displays
    displays = {}
    for slot in durations:
        dio_map = PINMAP.get('tm1637', {}).get('dio', {})
        clk = PINMAP.get('tm1637', {}).get('clk')
        dio = dio_map.get(slot)
        if clk is None or dio is None:
            print(f"[WARN] {slot}: pins not found in pinmap (clk={clk}, dio={dio}) - using noop display")
            displays[slot] = make_display(hw, slot, None, None)
        else:
            displays[slot] = make_display(hw, slot, clk, dio, brightness=args.brightness)

    # start worker threads
    for slot, secs in durations.items():
        w = SlotWorker(hw, slot, secs, displays[slot], stop_event)
        workers.append(w)
        w.start()
        time.sleep(0.05)

    # wait for completion or timeout
    start = time.time()
    try:
        while True:
            alive = any(getattr(w, '_running', False) for w in workers)
            if not alive:
                break
            if args.timeout and (time.time() - start) > args.timeout:
                print('[MAIN] Global timeout reached, signaling stop')
                stop_event.set()
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        print('[MAIN] Interrupted by user, stopping workers...')
        stop_event.set()

    # join threads
    for w in workers:
        w.join(timeout=1.0)

    # final cleanup
    try:
        if hw is not None:
            hw.cleanup()
    except Exception:
        pass

    print('[MAIN] Test complete')


if __name__ == '__main__':
    main()
