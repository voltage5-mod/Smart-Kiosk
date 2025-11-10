"""
read_all_sensors.py

Continuously read all ACS712 channels configured in pinmap.json and print their readings.
Default behavior: run until Ctrl+C, printing each slot's "IDLE read" line each interval.

Usage examples:
  # continuous every 0.5s
  python read_all_sensors.py --interval 0.5

  # sample 100 times then exit
  python read_all_sensors.py --samples 100 --interval 0.25

This script only reads current sensors and does NOT toggle any relays or locks.
"""
import json
import os
import time
import argparse
from hardware_gpio import HardwareGPIO

HERE = os.path.dirname(__file__)
PINMAP = os.path.join(HERE, 'pinmap.json')


def load_pinmap(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print('Failed to load pinmap.json:', e)
        return {}


def main():
    parser = argparse.ArgumentParser(description='Continuously read all ACS712 channels and print values.')
    parser.add_argument('--interval', type=float, default=0.5, help='Seconds between samples (default 0.5)')
    parser.add_argument('--samples', type=int, default=0, help='Number of samples to take; 0 => run until Ctrl+C')
    parser.add_argument('--pinmap', type=str, default=PINMAP, help='Path to pinmap.json')
    parser.add_argument('--no-power', action='store_true', help='Do not toggle power relays; only read sensors')
    parser.add_argument('--power-settle', type=float, default=0.5, help='Seconds to wait after powering relays before first read')
    args = parser.parse_args()

    pinmap = load_pinmap(args.pinmap)
    ch_map = pinmap.get('acs712_channels') or {}
    if not ch_map:
        print('No acs712_channels found in pinmap.json. Exiting.')
        return

    slots = sorted(ch_map.keys())
    hw = HardwareGPIO(pinmap=pinmap, mode='auto')
    try:
        hw.setup()
    except Exception as e:
        print('Warning: hw.setup() failed or running in sim mode:', e)
    # Determine which slots have power relays mapped
    power_map = pinmap.get('power_relay') or {}
    slots_with_power = [s for s in slots if s in power_map]

    print('Reading sensors for slots:', ', '.join(slots))
    if not args.no_power and slots_with_power:
        print('Powering ON relays for slots:', ', '.join(slots_with_power))
        for s in slots_with_power:
            try:
                hw.relay_on(s)
            except Exception as e:
                print(f'Failed to power {s}:', e)
        # allow sensors to settle after powering
        time.sleep(args.power_settle)
    elif not slots_with_power:
        print('No power_relay entries found for slots; running read-only.')
    count = 0
    try:
        while True:
            for slot in slots:
                try:
                    cur = hw.read_current(slot)
                    raw = cur.get('raw')
                    volts = cur.get('volts')
                    amps = cur.get('amps')
                    # match desired format
                    print(f"{slot} IDLE read: raw={raw} volts={(volts or 0):.3f} V amps={(amps or 0):.2f} A")
                except Exception as e:
                    print(f"{slot} IDLE read: error: {e}")
            count += 1
            if args.samples and count >= args.samples:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('\nInterrupted by user.')
    finally:
        # Turn off relays we powered earlier (do not touch locks)
        if not args.no_power and slots_with_power:
            print('Powering OFF relays for slots:', ', '.join(slots_with_power))
            for s in slots_with_power:
                try:
                    hw.relay_off(s)
                except Exception:
                    pass
        try:
            hw.cleanup()
        except Exception:
            pass

if __name__ == '__main__':
    main()
