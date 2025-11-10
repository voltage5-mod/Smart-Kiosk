import time
import threading
import json
import os
from hardware_gpio import HardwareGPIO

BASE = os.path.dirname(__file__)
PINMAP_PATH = os.path.join(BASE, 'pinmap.json')

# Load pinmap for HardwareGPIO
try:
    with open(PINMAP_PATH, 'r', encoding='utf-8') as f:
        pinmap = json.load(f)
except Exception as e:
    print('Could not load pinmap.json:', e)
    pinmap = {}

# Simple per-slot timer that uses threading.Timer for ticks
class SlotTimer:
    def __init__(self, hw, slot_name, start_seconds):
        self.hw = hw
        self.slot = slot_name
        self.remaining = start_seconds
        self._lock = threading.Lock()
        self._running = False
        self._timer = None

    def start(self):
        print(f'[TEST] Starting timer for {self.slot} -> {self.remaining}s')
        self._running = True
        # power on the slot
        try:
            self.hw.relay_on(self.slot)
        except Exception:
            pass
        self._schedule_tick()

    def _schedule_tick(self):
        self._timer = threading.Timer(1.0, self._tick)
        self._timer.start()

    def _tick(self):
        with self._lock:
            if not self._running:
                return
            self.remaining = max(0, self.remaining - 1)
            print(f'[TICK] {self.slot}: {self.remaining}s remaining')
            try:
                # update simulated display if available
                tm = getattr(self.hw, 'tm', None)
                if tm:
                    try:
                        tm.show_time(self.remaining)
                    except Exception:
                        pass
            except Exception:
                pass
            if self.remaining <= 0:
                print(f'[END] {self.slot} time up -> turning off relay')
                try:
                    self.hw.relay_off(self.slot)
                except Exception:
                    pass
                self._running = False
                return
            self._schedule_tick()

    def stop(self):
        with self._lock:
            self._running = False
            try:
                if self._timer:
                    self._timer.cancel()
            except Exception:
                pass
            try:
                self.hw.relay_off(self.slot)
            except Exception:
                pass


def main():
    hw = HardwareGPIO(pinmap=pinmap, mode='sim')
    hw.setup()

    # create four slot timers with short durations for quick test
    durations = {'slot1': 8, 'slot2': 12, 'slot3': 6, 'slot4': 10}
    timers = []
    for s, sec in durations.items():
        t = SlotTimer(hw, s, sec)
        timers.append(t)

    # start all timers
    for t in timers:
        t.start()
        # small stagger to show concurrency but not required
        time.sleep(0.1)

    # wait for all to finish
    try:
        while any(t._running for t in timers):
            time.sleep(0.5)
    except KeyboardInterrupt:
        print('Interrupted; stopping timers')
        for t in timers:
            t.stop()

    print('All timers completed.')

if __name__ == '__main__':
    main()
