"""
staged_power_sequence.py

Power slots cumulatively in stages and monitor the current sensors while doing so.
Sequence (default timings):
 - Stage 1: turn ON slot1, sample for 3 seconds
 - Stage 2: turn ON slot2 (so slot1+slot2), sample for 3 seconds
 - Stage 3: turn ON slot3 (so slot1+slot2+slot3), sample for 3 seconds
 - Stage 4: turn ON slot4 (so slot1+slot2+slot3+slot4), sample for 4 seconds

By default relays are left ON at the end; use --power-off to switch them off when done.
This script only toggles the power relays (no locks) and prints IDLE read lines in the exact format you requested.
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


def monitor_slots(hw, slots, interval, duration):
    """Monitor given slots for 'duration' seconds, printing IDLE lines every 'interval' seconds."""
    end = time.time() + duration
    while time.time() < end:
        for s in slots:
            try:
                cur = hw.read_current(s)
                raw = cur.get('raw')
                volts = cur.get('volts')
                amps = cur.get('amps')
                print(f"IDLE read: raw={raw} volts={(volts or 0):.3f} V amps={(amps or 0):.2f} A")
            except Exception as e:
                print(f"IDLE read: error: {e}")
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description='Staged cumulative power-on test for slots')
    parser.add_argument('--pinmap', type=str, default=PINMAP)
    parser.add_argument('--interval', type=float, default=0.5, help='Sample interval in seconds')
    parser.add_argument('--s1', type=float, default=3.0, help='Stage1 hold seconds (slot1 only)')
    parser.add_argument('--s2', type=float, default=3.0, help='Stage2 hold seconds (slot1+2)')
    parser.add_argument('--s3', type=float, default=3.0, help='Stage3 hold seconds (slot1+2+3)')
    parser.add_argument('--s4', type=float, default=4.0, help='Stage4 hold seconds (all slots)')
    parser.add_argument('--power-off', action='store_true', help='Power OFF relays at end')
    args = parser.parse_args()

    pinmap = load_pinmap(args.pinmap)
    ch_map = pinmap.get('acs712_channels') or {}
    if not ch_map:
        print('No acs712_channels in pinmap.json. Exiting.')
        return

    # build slot list in order (slot1..slot4) based on pinmap keys
    slots = []
    for i in range(1, 9):
        key = f'slot{i}'
        if key in ch_map:
            slots.append(key)
    # default to first 4 if available
    slots = slots[:4]
    print('Slots in sequence:', slots)
    if not slots:
        print('No slots configured. Exiting.')
        return

    hw = HardwareGPIO(pinmap=pinmap, mode='auto')
    try:
        hw.setup()
    except Exception as e:
        print('Warning: hw.setup() failed or running in sim mode:', e)

    # Ensure initial state: all relays OFF
    power_map = pinmap.get('power_relay') or {}
    powered = []
    try:
        for s in slots:
            # start with all off
            try:
                hw.relay_off(s)
            except Exception:
                pass
        # Stage 1: slot1 ON
        print('\n=== Stage 1: Powering ON', slots[0], 'only ===')
        hw.relay_on(slots[0])
        powered = [slots[0]]
        monitor_slots(hw, powered, args.interval, args.s1)

        # Stage 2: add slot2
        if len(slots) > 1:
            print('\n=== Stage 2: Powering ON', slots[1], '(now', ' + '.join(powered + [slots[1]]) ,') ===')
            hw.relay_on(slots[1])
            powered.append(slots[1])
            monitor_slots(hw, powered, args.interval, args.s2)

        # Stage 3: add slot3
        if len(slots) > 2:
            print('\n=== Stage 3: Powering ON', slots[2], '(now', ' + '.join(powered + [slots[2]]) ,') ===')
            hw.relay_on(slots[2])
            powered.append(slots[2])
            monitor_slots(hw, powered, args.interval, args.s3)

        # Stage 4: add slot4
        if len(slots) > 3:
            print('\n=== Stage 4: Powering ON', slots[3], '(now', ' + '.join(powered + [slots[3]]) ,') ===')
            hw.relay_on(slots[3])
            powered.append(slots[3])
            monitor_slots(hw, powered, args.interval, args.s4)

        print('\nSequence complete.')
    finally:
        if args.power_off:
            print('Powering OFF all slots')
            for s in powered:
                try:
                    hw.relay_off(s)
                except Exception:
                    pass
        else:
            print('Leaving relays powered ON for powered slots:', powered)
        try:
            hw.cleanup()
        except Exception:
            pass

if __name__ == '__main__':
    main()
