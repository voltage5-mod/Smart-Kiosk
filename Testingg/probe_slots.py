"""
probe_slots.py

Quick helper to print one current reading for every slot listed in pinmap.acs712_channels.
Usage:
    python probe_slots.py

This will show which channels are readable and the values returned by hardware_gpio.read_current(slot).
"""
import json
import os
from hardware_gpio import HardwareGPIO

BASE = os.path.dirname(__file__)
PINMAP = os.path.join(BASE, 'pinmap.json')

with open(PINMAP, 'r', encoding='utf-8') as f:
    pinmap = json.load(f)

hw = HardwareGPIO(pinmap=pinmap, mode='auto')
try:
    hw.setup()
except Exception as e:
    print('hw.setup() warning:', e)

ch_map = pinmap.get('acs712_channels') or {}
if not ch_map:
    print('No acs712_channels found in pinmap.json')
else:
    for slot, ch in ch_map.items():
        try:
            cur = hw.read_current(slot)
            print(f"{slot}: raw={cur.get('raw')} volts={cur.get('volts'):.3f} V amps={cur.get('amps'):.2f} A (raw={cur.get('amps_raw'):.3f}, ema={cur.get('amps_ema'):.3f}, med={cur.get('amps_med'):.3f})")
        except Exception as e:
            print(f"{slot}: read_current failed:", e)

# cleanup
try:
    hw.cleanup()
except Exception:
    pass
