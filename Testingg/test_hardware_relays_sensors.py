#!/usr/bin/env python3
"""
test_hardware_relays_sensors.py

Interactive/automated test utility to exercise relays and read sensors defined
in `pinmap.json` using the project's `HardwareGPIO` abstraction.

Features:
 - Lists detected relays (power, lock, pump) and allows per-relay on/off testing
 - Reads ACS712 channels (via `read_current`) and prints summary statistics
 - Optional TM1637 display smoke test (if configured)
 - Safe defaults: does not toggle relays unless `--yes` or `--auto` is provided

Usage examples:
  # Dry run (print what would be tested)
  python test_hardware_relays_sensors.py --dry-run

  # Interactive run (default): prompts before toggling each relay
  python test_hardware_relays_sensors.py

  # Automatic run: toggle each relay for 2 seconds without prompts
  python test_hardware_relays_sensors.py --auto --hold 2 --yes

  # Run in simulation mode (no real GPIO even if available)
  python test_hardware_relays_sensors.py --mode sim --auto --yes

"""
from __future__ import annotations
import argparse
import json
import os
import statistics
import time
from typing import Dict, Any

from hardware_gpio import HardwareGPIO

HERE = os.path.dirname(__file__)
PINMAP_PATH = os.path.join(HERE, 'pinmap.json')


def load_pinmap(path: str) -> Dict[str, Any]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print('Failed to load pinmap.json:', e)
        return {}


def confirm(prompt: str) -> bool:
    r = input(f"{prompt} [y/N]: ").strip().lower()
    return r in ('y', 'yes')


def _try_print(msg: str):
    try:
        print(msg)
    except Exception:
        pass


def test_relays(hw: HardwareGPIO, pinmap: Dict[str, Any], args: argparse.Namespace):
    print('\n=== Relay Test ===')
    power_map = pinmap.get('power_relay') or {}
    lock_map = pinmap.get('lock_relay') or {}
    pump = pinmap.get('pump_relay')

    relays = []
    for name, pin in power_map.items():
        relays.append(('power', name, pin))
    for name, pin in lock_map.items():
        relays.append(('lock', name, pin))
    if pump is not None:
        relays.append(('pump', 'pump_relay', pump))

    if not relays:
        print('No relays found in pinmap.json')
        return

    for kind, name, pin in relays:
        print(f"\nFound relay: kind={kind} name={name} pin={pin}")
        if args.dry_run:
            continue
        should_run = args.auto or args.yes or confirm(f"Toggle relay {name} (pin {pin}) now?")
        if not should_run:
            print('Skipping')
            continue
        try:
            print('-> TURNING ON')
            hw.relay_on(name)
            time.sleep(args.hold)
        except Exception as e:
            print('relay_on failed:', e)
        try:
            print('-> TURNING OFF')
            hw.relay_off(name)
            time.sleep(0.3)
        except Exception as e:
            print('relay_off failed:', e)


def test_sensors(hw: HardwareGPIO, pinmap: Dict[str, Any], args: argparse.Namespace):
    print('\n=== Sensor Test (ACS712) ===')
    ch_map = pinmap.get('acs712_channels') or {}
    if not ch_map:
        print('No acs712_channels found in pinmap.json')
        return

    samples = args.samples
    delay = args.interval

    for slot, ch in ch_map.items():
        print(f"\nReading slot {slot} (ADC ch {ch}) {samples} samples @ {delay}s interval")
        vals = []
        try:
            for i in range(samples):
                cur = hw.read_current(slot)
                amps = cur.get('amps')
                raw = cur.get('raw')
                volts = cur.get('volts')
                vals.append(amps if amps is not None else 0.0)
                print(f"  sample {i+1:02d}: raw={raw} volts={(volts or 0):.3f} V amps={(amps or 0):.3f} A")
                time.sleep(delay)
        except Exception as e:
            print('read_current failed:', e)
            continue
        if vals:
            try:
                print('  summary:', f"min={min(vals):.3f}", f"max={max(vals):.3f}", f"mean={statistics.mean(vals):.3f}", f"stdev={statistics.pstdev(vals):.3f}")
            except Exception:
                pass


def test_tm1637(hw: HardwareGPIO, pinmap: Dict[str, Any], args: argparse.Namespace):
    print('\n=== TM1637 Display Test ===')
    tm = pinmap.get('tm1637') or {}
    if not tm:
        print('No tm1637 entry in pinmap.json')
        return
    try:
        # initialize once and show a short countdown
        disp = hw.tm1637_init()
        if not disp:
            print('tm1637_init returned None')
            return
        print('Displaying 00:05 countdown on display...')
        for s in range(5, -1, -1):
            try:
                disp.show_time(s)
            except Exception:
                pass
            time.sleep(1.0)
        # clear
        try:
            disp.show_time(0)
        except Exception:
            pass
    except Exception as e:
        print('TM1637 test failed:', e)


def main():
    parser = argparse.ArgumentParser(description='Test relays and sensors using HardwareGPIO and pinmap.json')
    parser.add_argument('--pinmap', type=str, default=PINMAP_PATH, help='Path to pinmap.json')
    parser.add_argument('--mode', choices=('auto', 'real', 'sim'), default='auto', help='HardwareGPIO mode')
    parser.add_argument('--dry-run', action='store_true', help='Only print what would be tested')
    parser.add_argument('--auto', action='store_true', help='Run without prompting (use with caution)')
    parser.add_argument('--yes', action='store_true', help='Implicitly answer yes to prompts')
    parser.add_argument('--hold', type=float, default=1.5, help='Seconds to hold a relay ON during test')
    parser.add_argument('--samples', type=int, default=8, help='Samples per sensor channel')
    parser.add_argument('--interval', type=float, default=0.5, help='Seconds between sensor samples')

    args = parser.parse_args()

    pinmap = load_pinmap(args.pinmap)
    if not pinmap:
        print('Missing or invalid pinmap.json; exiting.')
        return

    # instantiate hardware interface
    hw = HardwareGPIO(pinmap=pinmap, mode=args.mode)
    try:
        hw.setup()
    except Exception as e:
        print('hw.setup() warning or failure, continuing in sim mode if available:', e)

    try:
        test_relays(hw, pinmap, args)
        test_sensors(hw, pinmap, args)
        # optional display test
        try:
            if confirm('Run TM1637 display test?') or args.auto or args.yes:
                test_tm1637(hw, pinmap, args)
        except Exception:
            pass
    finally:
        try:
            hw.cleanup()
        except Exception:
            pass

    print('\nHardware test complete.')


if __name__ == '__main__':
    main()
