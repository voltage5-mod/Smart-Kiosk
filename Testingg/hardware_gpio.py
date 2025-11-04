"""
Hardware abstraction for Raspberry Pi GPIO and MCP3008 ADC (ACS712)
Provides a simple interface used by tests and later integrated into `FULL_UI.py`.

Modes:
 - auto: try to use real RPi libraries; if not available, fall back to simulation.
 - sim: simulation only (no hardware access).

API summary:
 - HardwareGPIO(pinmap, mode='auto')
 - setup()
 - relay_on(pin_or_name)
 - relay_off(pin_or_name)
 - lock_slot(slot, lock=True)
 - read_adc(channel) -> int (0-1023)
 - read_current(slot) -> { 'raw':adc, 'volts':v, 'amps':i }
 - cleanup()

Note: this module keeps dependencies minimal. Install on RPi with:
  pip install RPi.GPIO spidev

Use carefully; ensure relays are wired with proper drivers and common ground.
"""
from typing import Dict, Any
import json
import time
import os
from collections import deque
import math
import statistics

PINMAP_PATH = os.path.join(os.path.dirname(__file__), 'pinmap.json')

# Try imports for real hardware
_real_gpio = None
_spidev = None
try:
    import RPi.GPIO as GPIO
    _real_gpio = GPIO
except Exception:
    _real_gpio = None
try:
    import spidev
    _spidev = spidev
except Exception:
    _spidev = None

class HardwareGPIO:
    def __init__(self, pinmap: Dict[str, Any]=None, mode: str='auto', relay_active_high: bool = False):
        """relay_active_high: if True, GPIO.HIGH activates a relay; if False (default), GPIO.LOW activates (active-low modules).
        """
        self.mode = mode
        if self.mode == 'auto':
            if _real_gpio and _spidev:
                self.mode = 'real'
            else:
                self.mode = 'sim'
        self.pinmap = pinmap or self._load_pinmap()
        self.spi = None
        self.gpio = _real_gpio if self.mode == 'real' else None
        # assume many relay modules are active-low by default; allow override
        self.relay_active_high = relay_active_high
        self._inited = False
        # per-slot calibration baseline (volts) and raw
        self._baseline = {}
        # smoothing helpers: exponential moving average and recent samples for RMS
        self._ema = {}
        self._recent = {}
        # tuning: RMS window and EMA alpha
        # increase RMS window to reduce sensitivity to single-sample spikes
        self._rms_window = 10
        # increase EMA alpha so EMA reacts reasonably fast but still smooths
        self._ema_alpha = 0.6
        # TM1637 display helper (created on demand)
        self.tm = None

    def _load_pinmap(self):
        try:
            with open(PINMAP_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def setup(self):
        if self.mode == 'real':
            GPIO.setmode(GPIO.BCM)
            # setup digital outputs (relays, tm1637 clk if used)
            # power relays
            inactive_level = GPIO.LOW if self.relay_active_high else GPIO.HIGH
            active_level = GPIO.HIGH if self.relay_active_high else GPIO.LOW
            for k, v in (self.pinmap.get('power_relay') or {}).items():
                GPIO.setup(v, GPIO.OUT)
                GPIO.output(v, inactive_level)
            for k, v in (self.pinmap.get('lock_relay') or {}).items():
                GPIO.setup(v, GPIO.OUT)
                GPIO.output(v, inactive_level)
            # other outputs (pump)
            pump = self.pinmap.get('pump_relay')
            if pump is not None:
                GPIO.setup(pump, GPIO.OUT)
                GPIO.output(pump, inactive_level)
            # setup SPI for MCP3008
            sp = spidev.SpiDev()
            sp.open(0, 0)  # bus 0, device 0 (CE0)
            sp.max_speed_hz = 1350000
            self.spi = sp
            self._inited = True
        else:
            print('[HW_SIM] Running in simulation mode. No hardware will be toggled.')
            self._inited = True

    def relay_on(self, pin_or_name):
        pin = self._resolve_pin(pin_or_name)
        if pin is None:
            print('[HW] Unknown relay:', pin_or_name)
            return
        if self.mode == 'real':
            level = GPIO.HIGH if self.relay_active_high else GPIO.LOW
            GPIO.output(pin, level)
        else:
            print(f'[HW_SIM] RELAY ON pin={pin} ({pin_or_name})')

    def relay_off(self, pin_or_name):
        pin = self._resolve_pin(pin_or_name)
        if pin is None:
            print('[HW] Unknown relay:', pin_or_name)
            return
        if self.mode == 'real':
            level = GPIO.LOW if self.relay_active_high else GPIO.HIGH
            GPIO.output(pin, level)
        else:
            print(f'[HW_SIM] RELAY OFF pin={pin} ({pin_or_name})')

    def lock_slot(self, slot: str, lock: bool=True):
        # slot like 'slot1' maps to lock_relay.slot1
        locks = self.pinmap.get('lock_relay') or {}
        pin = locks.get(slot)
        if pin is None:
            # maybe slot provided as number or direct pin
            pin = self._resolve_pin(slot)
        if pin is None:
            print('[HW] No lock pin for', slot)
            return
        if lock:
            self.relay_on(pin)
        else:
            self.relay_off(pin)

    def _resolve_pin(self, pin_or_name):
        # Accept numeric pin or string names like 'slot1' or 'slot1_lock'
        if isinstance(pin_or_name, int):
            return pin_or_name
        if isinstance(pin_or_name, str):
            # direct mapping keys
            pr = self.pinmap.get('power_relay') or {}
            lr = self.pinmap.get('lock_relay') or {}
            if pin_or_name in pr:
                return pr.get(pin_or_name)
            if pin_or_name in lr:
                return lr.get(pin_or_name)
            # accept 'slot1' by returning power relay pin
            if pin_or_name.startswith('slot') and pin_or_name in pr:
                return pr.get(pin_or_name)
            # try top-level keys
            v = self.pinmap.get(pin_or_name)
            if isinstance(v, int):
                return v
        return None

    def read_adc(self, channel: int) -> int:
        # MCP3008 channel 0..7
        if self.mode == 'real':
            if not self.spi:
                raise RuntimeError('SPI not initialized')
            if channel < 0 or channel > 7:
                raise ValueError('channel out of range')
            # MCP3008 protocol: start bit, single/diff, channel
            cmd = 0b11 << 6 | (channel & 0x07) << 3
            resp = self.spi.xfer2([1, (8+channel) << 4, 0])
            # combine bits
            val = ((resp[1] & 3) << 8) | resp[2]
            return val
        else:
            # simulate a baseline with random noise
            import random
            baseline = 512
            return baseline + random.randint(-5, 5)

    def read_current(self, slot: str) -> Dict[str, Any]:
        # read ADC channel for slot and convert to volts and amps
        ch_map = self.pinmap.get('acs712_channels') or {}
        ch = ch_map.get(slot)
        if ch is None:
            # if slot passed like 'slot1', try fetch
            try:
                ch = int(slot)
            except Exception:
                return {'error': 'no_channel'}
        adc = self.read_adc(ch)
        vref = 3.3
        volts = (adc / 1023.0) * vref
        # ACS712 5A typical sensitivity ~185 mV/A (0.185 V/A)
        sensitivity = 0.185
        # use calibrated baseline if available (volts at zero current)
        baseline_v = self._baseline.get(slot, (vref / 2))
        amps = (volts - baseline_v) / sensitivity
        # maintain recent samples for RMS smoothing
        if slot not in self._recent:
            self._recent[slot] = deque(maxlen=self._rms_window)
        self._recent[slot].append(amps)
        # compute RMS over recent window to reduce spikes
        if len(self._recent[slot]) > 0:
            rms = math.sqrt(sum((x or 0.0) ** 2 for x in self._recent[slot]) / len(self._recent[slot]))
            # median provides robustness against a few spikes while preserving response
            try:
                med = float(statistics.median(list(self._recent[slot])))
            except Exception:
                med = rms
        else:
            rms = amps
            med = amps
        # exponential moving average for another smoothing view
        prev_ema = self._ema.get(slot)
        if prev_ema is None:
            ema = amps
        else:
            ema = (self._ema_alpha * amps) + ((1 - self._ema_alpha) * prev_ema)
        self._ema[slot] = ema

        # Return both raw and smoothed values; keep 'amps' as the RMS for backward compatibility
        return {'raw': adc, 'volts': volts, 'amps': rms, 'amps_raw': amps, 'amps_ema': ema, 'amps_med': med}

    def calibrate_zero(self, slot: str, samples: int = 20, delay: float = 0.05):
        """Calibrate zero-current baseline for a slot by averaging ADC readings.

        Call this with no load connected to the slot. Stores baseline volts in self._baseline.
        """
        ch_map = self.pinmap.get('acs712_channels') or {}
        ch = ch_map.get(slot)
        if ch is None:
            raise ValueError('No ADC channel for slot: ' + str(slot))
        vals = []
        for i in range(samples):
            v = self.read_adc(ch)
            vals.append(v)
            time.sleep(delay)
        avg = sum(vals) / len(vals)
        vref = 3.3
        baseline_v = (avg / 1023.0) * vref
        self._baseline[slot] = baseline_v
        # reset smoothing buffers for this slot so subsequent reads start clean
        try:
            self._ema[slot] = 0.0
            self._recent[slot] = deque([0.0] * min(self._rms_window, samples), maxlen=self._rms_window)
        except Exception:
            pass
        if self.mode == 'sim':
            print(f'[HW_SIM] calibrated {slot}: raw_avg={avg:.1f} baseline_v={baseline_v:.3f} V')
        else:
            print(f'Calibrated {slot}: raw_avg={avg:.1f} baseline_v={baseline_v:.3f} V')
        return {'raw_avg': avg, 'baseline_v': baseline_v}

    def is_charging(self, slot: str, threshold_amps: float = 0.3) -> bool:
        """Return True if measured amps exceed baseline by threshold_amps."""
        try:
            cur = self.read_current(slot)
            baseline_v = self._baseline.get(slot)
            if baseline_v is None:
                # require calibration
                raise RuntimeError('Baseline not calibrated for ' + str(slot))
            return (cur.get('amps', 0) - 0.0) >= threshold_amps
        except Exception:
            return False

    def wait_for_unplug(self, slot: str, threshold_amps: float = 0.3, grace_seconds: int = 3, confirm_seconds: float = 1.0):
        """Block until current falls below threshold for grace_seconds and then remains low for confirm_seconds.

        This implements a two-stage confirmation to avoid treating short spikes/dips as an unplug event.
        - grace_seconds: primary window of consecutive low samples required (e.g. 3s)
        - confirm_seconds: additional short confirmation window (e.g. 1s) that must also be all below threshold

        Returns True when unplug detected. Non-blocking alternatives can poll `is_charging`.
        """
        below_count = 0
        interval = 0.5
        needed = max(1, int(grace_seconds / interval))
        confirm_needed = max(1, int(confirm_seconds / interval))
        while True:
            cur = self.read_current(slot)
            amps = cur.get('amps', 0)
            if amps < threshold_amps:
                below_count += 1
                if below_count >= needed:
                    # primary window satisfied; perform a short confirmation window
                    conf_ok = True
                    for _ in range(confirm_needed):
                        time.sleep(interval)
                        cur2 = self.read_current(slot)
                        if cur2.get('amps', 0) >= threshold_amps:
                            conf_ok = False
                            break
                    if conf_ok:
                        return True
                    # failed confirmation: reset and continue polling
                    below_count = 0
            else:
                below_count = 0
            time.sleep(interval)

    # TM1637 minimal driver (bit-banged)
    def tm1637_init(self):
        if self.tm is None:
            clk = self.pinmap.get('tm1637', {}).get('clk')
            dio_map = self.pinmap.get('tm1637', {}).get('dio', {})
            # create an instance bound to clk and dio for slot1 only (we'll support slot1 display)
            # Prefer using the `tm1637` library if available (better compatibility); fall back to local driver
            try:
                import tm1637 as _tm1637_lib
                disp = _tm1637_lib.TM1637(clk=clk, dio=dio_map.get('slot1'))
                # wrap to expose show_time(seconds) and set_brightness(level)
                class _Wrap:
                    def __init__(self, d):
                        self.d = d
                    def show_time(self, seconds: int):
                        mm = seconds // 60
                        ss = seconds % 60
                        try:
                            # some libraries have `numbers` helper
                            if hasattr(self.d, 'numbers'):
                                self.d.numbers(mm, ss)
                            else:
                                s = f"{mm:02d}{ss:02d}"
                                if hasattr(self.d, 'show'):
                                    self.d.show(s)
                        except Exception:
                            pass
                    def set_brightness(self, level: int):
                        # try common API names for brightness
                        try:
                            if hasattr(self.d, 'brightness'):
                                self.d.brightness(level)
                                return
                            if hasattr(self.d, 'set_brightness'):
                                self.d.set_brightness(level)
                                return
                            if hasattr(self.d, 'setLight'):
                                self.d.setLight(level)
                                return
                        except Exception:
                            pass
                self.tm = _Wrap(disp)
                # set a low default brightness to avoid very-bright LEDs
                try:
                    self.tm.set_brightness(1)
                except Exception:
                    pass
            except Exception:
                self.tm = TM1637Display(clk_pin=clk, dio_pin=dio_map.get('slot1'), gpio=self.gpio, mode=self.mode)
        return self.tm

    def cleanup(self):
        """Turn off relays, close SPI and cleanup GPIO."""
        if self.mode == 'real' and self.gpio:
            try:
                inactive_level = self.gpio.LOW if self.relay_active_high else self.gpio.HIGH
                for k, v in (self.pinmap.get('power_relay') or {}).items():
                    try:
                        self.gpio.output(v, inactive_level)
                    except Exception:
                        pass
                for k, v in (self.pinmap.get('lock_relay') or {}).items():
                    try:
                        self.gpio.output(v, inactive_level)
                    except Exception:
                        pass
                pump = self.pinmap.get('pump_relay')
                if pump is not None:
                    try:
                        self.gpio.output(pump, inactive_level)
                    except Exception:
                        pass
                # close SPI
                try:
                    if self.spi:
                        self.spi.close()
                except Exception:
                    pass
                try:
                    self.gpio.cleanup()
                except Exception:
                    pass
            except Exception:
                pass
        else:
            print('[HW_SIM] cleanup()')


class TM1637Display:
    """Minimal TM1637 4-digit display driver (blocking, not optimized).
    Only supports basic 4-digit numeric display and colon. Designed for single-display usage in tests.
    """
    SEGMENTS = {
        '0': 0x3f, '1': 0x06, '2': 0x5b, '3': 0x4f,
        '4': 0x66, '5': 0x6d, '6': 0x7d, '7': 0x07,
        '8': 0x7f, '9': 0x6f, ' ': 0x00, '-': 0x40
    }

    def __init__(self, clk_pin: int, dio_pin: int, gpio=None, mode='sim'):
        self.clk = clk_pin
        self.dio = dio_pin
        self.gpio = gpio
        self.mode = mode
        # brightness level 0..7 (TM1637 displays typically support 0-7)
        self.brightness_level = 1
        if self.mode == 'real' and self.gpio:
            self.gpio.setmode(self.gpio.BCM)
            self.gpio.setup(self.clk, self.gpio.OUT)
            self.gpio.setup(self.dio, self.gpio.OUT)

    def _start(self):
        if self.mode == 'real':
            self.gpio.output(self.dio, self.gpio.HIGH)
            self.gpio.output(self.clk, self.gpio.HIGH)
            self.gpio.output(self.dio, self.gpio.LOW)
            self.gpio.output(self.clk, self.gpio.LOW)
        else:
            pass

    def _stop(self):
        if self.mode == 'real':
            self.gpio.output(self.clk, self.gpio.LOW)
            self.gpio.output(self.dio, self.gpio.LOW)
            self.gpio.output(self.clk, self.gpio.HIGH)
            self.gpio.output(self.dio, self.gpio.HIGH)
        else:
            pass

    def _write_byte(self, b: int):
        if self.mode == 'real':
            for i in range(8):
                self.gpio.output(self.clk, self.gpio.LOW)
                bit = (b >> i) & 1
                self.gpio.output(self.dio, self.gpio.HIGH if bit else self.gpio.LOW)
                self.gpio.output(self.clk, self.gpio.HIGH)
            # ack
            self.gpio.output(self.clk, self.gpio.LOW)
            self.gpio.setup(self.dio, self.gpio.IN)
            time.sleep(0.00005)
            # read ack (ignore)
            try:
                _ = self.gpio.input(self.dio)
            except Exception:
                pass
            self.gpio.setup(self.dio, self.gpio.OUT)
        else:
            # simulation: print byte
            print(f'[TM_SIM] write_byte 0x{b:02x}')

    def show_time(self, seconds: int):
        # format mmss into 4 digits
        seconds = max(0, int(seconds))
        mm = seconds // 60
        ss = seconds % 60
        s = f'{mm:02d}{ss:02d}'
        segs = [self.SEGMENTS.get(ch, 0x00) for ch in s]
        # Turn on the colon / decimal point between minutes and seconds.
        # Many TM1637 modules use the DP bit (0x80) on the second digit to show the colon.
        # If your module uses a different position, change this index.
        try:
            segs[1] = segs[1] | 0x80
        except Exception:
            pass
        # send data to TM1637
        if self.mode == 'real':
            self._start()
            self._write_byte(0x40)  # data command
            self._stop()
            self._start()
            self._write_byte(0xC0)  # address command
            for b in segs:
                self._write_byte(b)
            self._stop()
            # set display control (brightness using brightness_level 0..7)
            try:
                level = int(self.brightness_level) & 0x07
            except Exception:
                level = 1
            ctrl = 0x88 | (level & 0x07)
            self._start()
            self._write_byte(ctrl)
            self._stop()
        else:
            print(f'[TM_SIM] display {s[:2]}:{s[2:]}')

    def set_brightness(self, level: int):
        try:
            lvl = int(level) & 0x07
            self.brightness_level = lvl
            if self.mode == 'real':
                ctrl = 0x88 | (lvl & 0x07)
                self._start()
                self._write_byte(ctrl)
                self._stop()
        except Exception:
            pass

    def cleanup(self):
        if self.mode == 'real' and self.gpio:
            try:
                self.gpio.cleanup()
            except Exception:
                pass
        else:
            print('[HW_SIM] cleanup()')

if __name__ == '__main__':
    # quick smoke test when run directly
    h = HardwareGPIO()
    h.setup()
    print('Mode:', h.mode)
    try:
        print('Read CH0:', h.read_adc(0))
        print('Current slot1:', h.read_current('slot1'))
    finally:
        h.cleanup()
