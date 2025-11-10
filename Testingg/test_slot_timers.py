import time
import threading
import json
import os
import argparse
import hardware_gpio as hwmod
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
    # require explicit --real to avoid accidental simulation runs on a hardware test bench
    parser = argparse.ArgumentParser(description='Test 4 slot timers (requires real GPIO and MCP3008).')
    parser.add_argument('--real', action='store_true', help='Use real Raspberry Pi GPIO and SPI (required for hardware tests)')
    args = parser.parse_args()

    if not args.real:
        print('This test script requires --real to run against actual hardware. Aborting to avoid simulation.')
        return

    # ensure the hardware drivers are available in the hardware_gpio module
    if not getattr(hwmod, '_real_gpio', None) or not getattr(hwmod, '_spidev', None):
        print('ERROR: Real GPIO or SPI libraries are not available in this Python environment.')
        print('Make sure RPi.GPIO and spidev are installed and you are running on a Raspberry Pi.')
        return

    hw = HardwareGPIO(pinmap=pinmap, mode='real')
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
