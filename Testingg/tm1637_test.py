"""
TM1637 test utility

- Tries to use the external `tm1637` package (if installed) to show 02:00.
- Falls back to the internal `TM1637Display` from `hardware_gpio.py` and cycles digits to help diagnose wiring/segment mapping.

Run on the Pi where the display is connected.
Usage:
  python3 tm1637_test.py [--relay-active-high]

If the external library is missing, install with:
  python3 -m pip install tm1637

"""
import time
import json
import os
import argparse

BASE = os.path.dirname(__file__)
PINMAP = os.path.join(BASE, 'pinmap.json')

from hardware_gpio import HardwareGPIO, TM1637Display

parser = argparse.ArgumentParser()
parser.add_argument('--relay-active-high', action='store_true')
args = parser.parse_args()

with open(PINMAP, 'r', encoding='utf-8') as f:
    pinmap = json.load(f)

hw = HardwareGPIO(pinmap=pinmap, mode='auto', relay_active_high=bool(args.relay_active_high))
print('Hardware mode:', hw.mode)

# Initialize display using hw helper (prefers external lib if installed)
print('Initializing TM1637 via hardware_gpio.tm1637_init()...')
disp = None
try:
    hw.setup()
    disp = hw.tm1637_init()
except Exception as e:
    print('tm1637 init error:', e)

if disp:
    print('Display initialized. Trying to show 02:00 for 5s...')
    try:
        # many wrappers expose show_time or numbers/show
        if hasattr(disp, 'show_time'):
            disp.show_time(120)
        elif hasattr(disp, 'numbers'):
            # numbers(minutes, seconds)
            disp.numbers(2, 0)
        elif hasattr(disp, 'show'):
            disp.show('02:00')
        else:
            print('Display wrapper present but lacks show_time/numbers/show')
    except Exception as e:
        print('Display write failed:', e)
    time.sleep(5)

    print('\nNow cycling digits 0..9 on each of the 4 positions (2s each) to help identify wiring)')
    try:
        # If wrapper exposes 'd' (the underlying lib instance), try using numbers/show
        underlying = getattr(disp, 'd', None)
        if underlying is not None and hasattr(underlying, 'numbers'):
            for pos in range(4):
                for d in range(10):
                    # construct mm:ss to show digit d in the target position
                    # Simple approach: show values that place the digit at each position
                    if pos == 0:
                        mm = d
                        ss = 0
                    elif pos == 1:
                        mm = 10 + d if d < 6 else 10 + (d % 6)
                        ss = 0
                    elif pos == 2:
                        mm = 0
                        ss = d * 10
                    else:
                        mm = 0
                        ss = d
                    try:
                        underlying.numbers(mm, ss)
                    except Exception:
                        try:
                            underlying.show(f"{mm:02d}{ss:02d}")
                        except Exception:
                            pass
                    time.sleep(0.2)
        else:
            # fallback: use internal display object (TM1637Display) or the disp wrapper's show_time
            if isinstance(disp, TM1637Display):
                for pos in range(4):
                    for d in range(10):
                        # craft seconds that put d into the right position
                        if pos == 0:
                            secs = d * 60
                        elif pos == 1:
                            secs = (d * 10) * 60  # big jump, may wrap, but helpful
                        elif pos == 2:
                            secs = d * 10
                        else:
                            secs = d
                        disp.show_time(secs)
                        time.sleep(0.2)
            else:
                # as a bare fallback, try calling show_time on wrapper
                for d in range(10):
                    try:
                        if hasattr(disp, 'show_time'):
                            disp.show_time(d * 60)
                    except Exception:
                        pass
                    time.sleep(0.2)
    except Exception as e:
        print('Cycling error:', e)

    print('\nTest complete. Showing 02:00 for final verification...')
    try:
        if hasattr(disp, 'show_time'):
            disp.show_time(120)
        elif hasattr(disp, 'numbers'):
            disp.numbers(2, 0)
        elif hasattr(disp, 'show'):
            disp.show('02:00')
    except Exception as e:
        print('Final write failed:', e)
    time.sleep(3)
else:
    # no display wrapper returned; try direct TM1637Display with pins from pinmap
    print('No display wrapper available. Falling back to direct TM1637Display from hardware_gpio.')
    clk = pinmap.get('tm1637', {}).get('clk')
    dio = pinmap.get('tm1637', {}).get('dio', {}).get('slot1')
    if clk is None or dio is None:
        print('tm1637 pins not found in pinmap.json. Please check pinmap.')
    else:
        print(f'Using TM1637Display(clk={clk}, dio={dio})')
        d = TM1637Display(clk_pin=clk, dio_pin=dio, gpio=hw.gpio, mode=hw.mode)
        print('Showing 02:00 for 5s...')
        d.show_time(120)
        time.sleep(5)
        print('Cycling digits 0..9 (2s each)')
        for pos in range(4):
            for digit in range(10):
                # reuse show_time with crafted seconds to place digits approximately
                if pos == 0:
                    secs = digit * 60
                elif pos == 1:
                    secs = (digit % 6) * 60 + 0
                elif pos == 2:
                    secs = digit * 10
                else:
                    secs = digit
                d.show_time(secs)
                time.sleep(0.2)
        print('Final show 02:00')
        d.show_time(120)
        time.sleep(3)

print('All done. Cleanup and exit.')
try:
    hw.cleanup()
except Exception:
    pass
