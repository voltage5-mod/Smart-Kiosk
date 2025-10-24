"""
Interactive test for slot1 hardware.
Usage: run this on the Raspberry Pi that has the relays & MCP3008 wired.
It will:
 - Toggle power relay for slot1
 - Toggle lock relay for slot1
 - Read MCP3008 channel 0 (ACS712) while you plug/unplug a device

Run:
  python test_slot1.py

Be present and ready to cut power if something behaves unexpectedly.
"""
import time
import json
import os
import argparse
from hardware_gpio import HardwareGPIO

BASE = os.path.dirname(__file__)
PINMAP = os.path.join(BASE, 'pinmap.json')

with open(PINMAP, 'r', encoding='utf-8') as f:
    pinmap = json.load(f)

# CLI: allow overriding relay active polarity if your relay module is active-HIGH
parser = argparse.ArgumentParser(description='Test slot1 hardware (relays + ACS712 + TM1637)')
parser.add_argument('--relay-active-high', action='store_true', help='Set if your relay module is activated by GPIO.HIGH (default: active-low)')
args = parser.parse_args()

hw = HardwareGPIO(pinmap=pinmap, mode='auto', relay_active_high=bool(args.relay_active_high))
try:
    hw.setup()
    print('Hardware mode:', hw.mode)
    # Show configured pins
    print('Slot1 power relay pin:', pinmap.get('power_relay', {}).get('slot1'))
    print('Slot1 lock relay pin:', pinmap.get('lock_relay', {}).get('slot1'))
    print('ACS712 channel for slot1:', pinmap.get('acs712_channels', {}).get('slot1'))

    input('Make sure wiring is correct and press Enter to BEGIN calibration... (ensure no phone is connected)')

    # Calibrate baseline for slot1 (no-load). This improves current detection accuracy.
    cal = hw.calibrate_zero('slot1', samples=30, delay=0.05)
    print('Calibration result:', cal)

    print('\n-- POWER RELAY ON briefly to verify relay control (3s)')
    hw.relay_on('slot1')
    time.sleep(3)
    hw.relay_off('slot1')

    input('\nNow test solenoid lock: press Enter to LOCK (activate)')
    hw.lock_slot('slot1', lock=True)
    print('Locked (3s)')
    time.sleep(3)
    hw.lock_slot('slot1', lock=False)
    print('Unlocked')

    # initialize TM1637 display (if available)
    tm = hw.tm1637_init()

    input('\nReady to monitor charging. Press Enter, then plug the device into slot1.\nThe script will wait for a charging event then start a 120s countdown on the TM1637. Press Ctrl+C to stop early.')

    # Ensure the slot power is ON for the charging test
    print('\nEnabling slot1 power relay for the charging test...')
    hw.relay_on('slot1')
    print('Slot1 power should now be ON. If your relay module is active-HIGH, re-run this script with --relay-active-high if the behavior is reversed.')

    # Wait for charging to start
    print('Waiting for charging start (threshold 0.3 A)...')
    try:
        while True:
            cur = hw.read_current('slot1')
            amps = cur.get('amps', 0)
            print(f'IDLE read: raw={cur.get("raw")} volts={cur.get("volts"):.3f} V amps={amps:.2f} A')
            if amps >= 0.3:
                print('Charging detected!')
                break
            time.sleep(1)

        # start countdown and display on TM1637
        duration = 120
        remaining = duration
        while remaining > 0:
            # update display
            if tm:
                try:
                    tm.show_time(remaining)
                except Exception:
                    pass
            cur = hw.read_current('slot1')
            amps = cur.get('amps', 0)
            print(f'TIMER {remaining}s - current {amps:.2f} A')
            # detect unplug: if current falls below threshold for 3 seconds -> stop
            if amps < 0.2:
                print('Possible unplug detected (low current). Waiting to confirm...')
                if hw.wait_for_unplug('slot1', threshold_amps=0.2, grace_seconds=3):
                    print('Unplug confirmed. Stopping timer.')
                    break
            time.sleep(1)
            remaining -= 1
    except KeyboardInterrupt:
        print('Test interrupted by user.')

    print('\nTest complete. Restoring relays OFF and cleaning up.')
finally:
    try:
        hw.relay_off('slot1')
    except Exception:
        pass
    try:
        hw.lock_slot('slot1', lock=False)
    except Exception:
        pass
    hw.cleanup()
    print('Cleanup done.')
