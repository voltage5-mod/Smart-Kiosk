#!/usr/bin/env python3
"""
Test all 4 TM1637 displays independently.

This script initializes each slot's display individually and cycles through
different times to verify wiring and GPIO pin mapping.

Usage:
  python3 test_all_displays.py --real          # Real Pi hardware
  python3 test_all_displays.py                 # Simulation mode (default)
"""
import time
import json
import os
import sys
import argparse

BASE = os.path.dirname(__file__)
PINMAP_PATH = os.path.join(BASE, 'pinmap.json')

# Load pinmap
try:
    with open(PINMAP_PATH, 'r', encoding='utf-8') as f:
        PINMAP = json.load(f)
except Exception as e:
    print(f"ERROR: Could not load pinmap.json: {e}")
    sys.exit(1)

# Import hardware
try:
    from hardware_gpio import HardwareGPIO
except Exception as e:
    print(f"ERROR: Could not import hardware_gpio: {e}")
    sys.exit(1)


def test_all_displays():
    """Test all 4 TM1637 displays with per-slot initialization."""
    parser = argparse.ArgumentParser(description='Test all 4 TM1637 displays')
    parser.add_argument('--real', action='store_true', help='Use real RPi GPIO')
    args = parser.parse_args()

    mode = 'real' if args.real else 'sim'
    print(f"\n{'='*60}")
    print(f"Testing TM1637 Displays - Mode: {mode.upper()}")
    print(f"{'='*60}\n")

    # Initialize hardware
    try:
        hw = HardwareGPIO(pinmap=PINMAP, mode=mode)
        hw.setup()
        print("[INIT] Hardware initialized successfully\n")
    except Exception as e:
        print(f"[ERROR] Failed to initialize hardware: {e}")
        sys.exit(1)

    # Initialize displays for all 4 slots
    displays = {}
    print("[DISPLAYS] Initializing per-slot displays...")
    for slot in ['slot1', 'slot2', 'slot3', 'slot4']:
        try:
            if hasattr(hw, 'tm1637_init_slot'):
                disp = hw.tm1637_init_slot(slot)
                print(f"  ✓ {slot}: initialized")
                displays[slot] = disp
            else:
                print(f"  ✗ {slot}: hw.tm1637_init_slot not available")
                displays[slot] = None
        except Exception as e:
            print(f"  ✗ {slot}: {e}")
            displays[slot] = None

    if not any(displays.values()):
        print("\n[ERROR] No displays initialized. Check pinmap and wiring.")
        sys.exit(1)

    print("\n[TEST 1] Display each time: 00:05, 00:10, 00:15, 00:20 (2s each)")
    print(f"{'='*60}")
    for test_seconds in [5, 10, 15, 20]:
        print(f"\nShowing {test_seconds:02d} seconds on all displays...")
        for slot, disp in displays.items():
            if disp:
                try:
                    disp.show_time(test_seconds)
                    print(f"  {slot}: 00:{test_seconds:02d}")
                except Exception as e:
                    print(f"  {slot}: ERROR - {e}")
        time.sleep(2)

    print("\n[TEST 2] Countdown from 20s for each slot independently")
    print(f"{'='*60}")
    for slot, disp in displays.items():
        if disp:
            print(f"\nCountdown for {slot}: 20s → 00s")
            try:
                for secs in range(20, -1, -1):
                    disp.show_time(secs)
                    if secs % 5 == 0 or secs <= 3:
                        print(f"  {slot}: 00:{secs:02d}")
                    time.sleep(0.2)
            except Exception as e:
                print(f"  {slot}: ERROR - {e}")

    print("\n[TEST 3] Brightness control (if available)")
    print(f"{'='*60}")
    for slot, disp in displays.items():
        if disp:
            print(f"\n{slot}: Testing brightness levels 0→7→0")
            try:
                disp.show_time(12)  # Show 00:12
                for level in [0, 1, 2, 3, 4, 5, 6, 7, 3, 1]:
                    if hasattr(disp, 'set_brightness'):
                        disp.set_brightness(level)
                        print(f"  brightness={level}")
                    time.sleep(0.3)
            except Exception as e:
                print(f"  ERROR: {e}")

    print("\n[TEST 4] Final verification: Show 02:00 on all displays")
    print(f"{'='*60}")
    for slot, disp in displays.items():
        if disp:
            try:
                disp.show_time(120)  # 2 minutes
                print(f"  {slot}: 02:00")
            except Exception as e:
                print(f"  {slot}: ERROR - {e}")
    time.sleep(2)

    # Cleanup
    try:
        hw.cleanup()
        print("\n[CLEANUP] Hardware cleaned up successfully\n")
    except Exception as e:
        print(f"\n[CLEANUP] Warning: {e}\n")

    print(f"{'='*60}")
    print("All tests complete!")
    print(f"{'='*60}\n")


if __name__ == '__main__':
    test_all_displays()
