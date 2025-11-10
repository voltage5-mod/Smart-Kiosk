"""
test_all_slots.py

Test script to exercise power relays for each charging slot and record ACS712 readings.

Usage (on the Pi where hardware_gpio.py works in real mode):
    python test_all_slots.py --slots slot1 slot2 slot3 slot4 --samples 30 --interval 0.25

The script will:
 - Instantiate HardwareGPIO (auto mode, falls back to sim if hardware libs missing)
 - Ensure SPI/GPIO setup
 - For each configured slot:
    - Calibrate baseline if none exists (prompt can be bypassed by passing --no-cal)
    - Turn relay ON (power on slot)
    - Wait short settle then sample `read_current(slot)` N times at given interval
    - Turn relay OFF and sample a few more readings
    - Save samples to CSV: test_all_slots_<slot>_<timestamp>.csv

Be careful: this toggles relays and powers the slot lines. Do not include solenoid locks in this test unless intended.
"""

import argparse
import time
import csv
import json
import os
from datetime import datetime

from hardware_gpio import HardwareGPIO

HERE = os.path.dirname(__file__)
PINMAP_PATH = os.path.join(HERE, 'pinmap.json')


def load_pinmap():
    try:
        with open(PINMAP_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print('Failed to load pinmap.json:', e)
        return {}


def ensure_setup(hw: HardwareGPIO):
    try:
        hw.setup()
    except Exception as e:
        print('Warning: hw.setup() failed or running in sim mode:', e)


def calibrate_if_needed(hw: HardwareGPIO, slot: str, samples: int = 20, delay: float = 0.05, no_cal: bool = False):
    if slot in hw._baseline and hw._baseline.get(slot) is not None and not no_cal:
        print(f'Baseline already present for {slot}: {hw._baseline.get(slot):.3f} V')
        return
    if no_cal:
        print(f'Skipping calibration for {slot} (no_cal=True)')
        return
    print(f'Calibrating baseline for {slot} ({samples} samples). Ensure nothing is plugged into the port...')
    try:
        res = hw.calibrate_zero(slot, samples=samples, delay=delay)
        print(f'Calibrated {slot}: raw_avg={res["raw_avg"]:.1f} baseline_v={res["baseline_v"]:.3f} V')
    except Exception as e:
        print('Calibration failed for', slot, e)


def sample_slot(hw: HardwareGPIO, slot: str, samples: int, interval: float, settle: float = 0.5, out_prefix: str = None):
    if out_prefix is None:
        out_prefix = 'test_all_slots'
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_name = f"{out_prefix}_{slot}_{timestamp}.csv"
    out_path = os.path.join(HERE, out_name)
    fieldnames = ['ts', 'raw', 'volts', 'amps_rms', 'amps_raw', 'amps_ema', 'amps_med']

    print(f"Power ON {slot}")
    hw.relay_on(slot)
    time.sleep(settle)

    rows = []
    print(f'Sampling {samples} readings from {slot} every {interval} s...')
    for i in range(samples):
        ts = time.time()
        cur = hw.read_current(slot)
        row = {
            'ts': ts,
            'raw': cur.get('raw'),
            'volts': cur.get('volts'),
            'amps_rms': cur.get('amps'),
            'amps_raw': cur.get('amps_raw'),
            'amps_ema': cur.get('amps_ema'),
            'amps_med': cur.get('amps_med')
        }
        rows.append(row)
        print(f"{i+1}/{samples} t={ts:.2f} raw={row['raw']} V={row['volts']:.3f} A_rms={row['amps_rms']:.3f} A_raw={row['amps_raw']:.3f}")
        time.sleep(interval)

    print(f"Power OFF {slot}")
    hw.relay_off(slot)
    # sample a few after power off
    for i in range(4):
        ts = time.time()
        cur = hw.read_current(slot)
        row = {
            'ts': ts,
            'raw': cur.get('raw'),
            'volts': cur.get('volts'),
            'amps_rms': cur.get('amps'),
            'amps_raw': cur.get('amps_raw'),
            'amps_ema': cur.get('amps_ema'),
            'amps_med': cur.get('amps_med')
        }
        rows.append(row)
        print(f"post-off {i+1}/4 t={ts:.2f} raw={row['raw']} V={row['volts']:.3f} A_rms={row['amps_rms']:.3f}")
        time.sleep(interval)

    # write CSV
    try:
        with open(out_path, 'w', newline='', encoding='utf-8') as csvf:
            w = csv.DictWriter(csvf, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print('Wrote sample CSV:', out_path)
    except Exception as e:
        print('Failed to write CSV', e)

    # summary
    amps = [r['amps_rms'] for r in rows if r['amps_rms'] is not None]
    if amps:
        mx = max(amps)
        med = sorted(amps)[len(amps)//2]
        print(f'Summary for {slot}: max_rms={mx:.3f} A median_rms≈{med:.3f} A')
    return out_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--slots', nargs='+', help='List of slots to test (e.g. slot1 slot2). If omitted, defaults to all entries in pinmap.acs712_channels', default=None)
    parser.add_argument('--samples', type=int, default=30, help='Number of samples while powered')
    parser.add_argument('--interval', type=float, default=0.25, help='Seconds between samples')
    parser.add_argument('--no-cal', action='store_true', help='Skip auto-calibration even if baseline missing')
    parser.add_argument('--only', action='store_true', help='Only sample and do not toggle relays (for passive monitoring)')
    args = parser.parse_args()

    pinmap = load_pinmap()
    hw = HardwareGPIO(pinmap=pinmap, mode='auto')
    ensure_setup(hw)

    ch_map = pinmap.get('acs712_channels') or {}
    all_slots = sorted(ch_map.keys())
    if not all_slots:
        print('No acs712_channels found in pinmap.json. Exiting.')
        return

    sel = args.slots or all_slots
    for slot in sel:
        if slot not in all_slots:
            print('Skipping unknown slot:', slot)
            continue
        print('\n=== Testing', slot, '===')
        calibrate_if_needed(hw, slot, samples=20, delay=0.05, no_cal=args.no_cal)
        if args.only:
            print('Only monitoring mode: sampling without toggling relay')
            sample_slot(hw, slot, args.samples, args.interval, settle=0.1, out_prefix='monitor')
        else:
            sample_slot(hw, slot, args.samples, args.interval, settle=0.5, out_prefix='test_all_slots')

    print('\nAll done.')

if __name__ == '__main__':
    main()
