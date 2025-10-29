"""
tm1637_probe.py

Direct probe for the installed `tm1637` Python package.

What it does:
- Loads pins from `pinmap.json`.
- Imports the installed `tm1637` module and prints available attributes.
- Instantiates the TM1637 with the configured pins and attempts several write methods:
  - .numbers(mm, ss)
  - .show with a 4-char string
  - .show with a list of 4 raw segment bytes (uses common 7-seg map)
  - toggles brightness if available
- Cycles digits so you can see which segments move (helps identify CLK/DIO swap or segment mapping).

Run on the Pi where the display is connected. Example:
  python3 tm1637_probe.py

If the installed library API differs, the script will print what methods exist and what failed.
"""
import time
import json
import os
import sys

BASE = os.path.dirname(__file__)
PINMAP = os.path.join(BASE, 'pinmap.json')

try:
    with open(PINMAP, 'r', encoding='utf-8') as f:
        pinmap = json.load(f)
except Exception as e:
    print('Could not open pinmap.json:', e)
    sys.exit(1)

clk = pinmap.get('tm1637', {}).get('clk')
dio = pinmap.get('tm1637', {}).get('dio', {}).get('slot1')
if clk is None or dio is None:
    print('tm1637 pins not set in pinmap.json. Please check tm1637.clk and tm1637.dio.slot1')
    sys.exit(1)

print('Using TM1637 pins: clk=', clk, 'dio=', dio)

try:
    import tm1637 as tm_lib
except Exception as e:
    print('Failed to import installed tm1637 package:', e)
    print('If you installed into a venv, run this script from the activated venv.')
    sys.exit(1)

print('tm1637 module imported:', tm_lib)
print('Top-level attributes:', [a for a in dir(tm_lib) if not a.startswith('_')])

if not hasattr(tm_lib, 'TM1637'):
    print('tm1637 package does not expose TM1637 class. Inspecting module...')
    print(dir(tm_lib))
    sys.exit(1)

try:
    disp = tm_lib.TM1637(clk=clk, dio=dio)
except Exception as e:
    print('Failed to instantiate TM1637(clk, dio):', e)
    print('Some libraries expect pin numbers as (dio, clk) order or keyword args. Trying alternative constructors...')
    try:
        disp = tm_lib.TM1637(dio=dio, clk=clk)
    except Exception as e2:
        try:
            disp = tm_lib.TM1637(dio, clk)
        except Exception as e3:
            print('All attempts failed. Exceptions:')
            print('1:', e)
            print('2:', e2)
            print('3:', e3)
            sys.exit(1)

print('TM1637 instance created:', disp)
print('Instance attributes/methods:', [a for a in dir(disp) if not a.startswith('_')])

# common 7-seg mapping (same as hardware_gpio.TM1637Display.SEGMENTS)
SEG = {
    '0': 0x3f, '1': 0x06, '2': 0x5b, '3': 0x4f,
    '4': 0x66, '5': 0x6d, '6': 0x7d, '7': 0x07,
    '8': 0x7f, '9': 0x6f, ' ': 0x00, '-': 0x40
}

def try_numbers():
    if hasattr(disp, 'numbers'):
        try:
            print('Calling disp.numbers(2, 0) -> should show 02:00')
            disp.numbers(2, 0)
            time.sleep(3)
            return True
        except Exception as e:
            print('disp.numbers failed:', e)
    else:
        print('disp.numbers not found')
    return False

def try_show_string():
    # some implementations accept a 4-character string or a list of bytes
    if hasattr(disp, 'show'):
        tests = ['02:00', '02:00', '0200', ' 2:0']
        for t in tests:
            try:
                print('Trying disp.show(%r)' % (t,))
                disp.show(t)
                time.sleep(2)
                return True
            except Exception as e:
                print('disp.show(%r) failed: %s' % (t, e))
    else:
        print('disp.show not found')
    return False

def try_show_raw():
    # Try writing raw segment bytes (list of 4 ints). We'll try setting the colon bit on the second digit (0x80)
    if hasattr(disp, 'show'):
        try:
            raw = [SEG['0'], SEG['2'] | 0x80, SEG['0'], SEG['0']]
            print('Trying disp.show(raw_bytes) ->', raw)
            disp.show(raw)
            time.sleep(2)
            return True
        except Exception as e:
            print('disp.show(raw) failed:', e)
    return False

def try_brightness():
    if hasattr(disp, 'brightness'):
        for b in [0, 1, 2, 3]:
            try:
                print('Setting brightness to', b)
                disp.brightness(b)
                time.sleep(0.5)
            except Exception as e:
                print('brightness() failed:', e)
                return False
        return True
    else:
        print('disp.brightness not available')
    return False

print('\n--- API Probe: attempting common methods ---')
ok = try_numbers()
if not ok:
    ok = try_show_string()
if not ok:
    ok = try_show_raw()

print('\n--- Brightness test ---')
try_brightness()

print('\n--- Digit cycling diagnostic (0..9 on each position) ---')
try:
    for pos in range(4):
        for d in range(10):
            if ok and hasattr(disp, 'numbers') and pos < 2:
                # place digit in minutes using numbers(mm, ss)
                mm = d if pos == 0 else (10 + (d % 6))
                ss = 0
                try:
                    disp.numbers(mm, ss)
                except Exception:
                    pass
            else:
                try:
                    # craft raw bytes to put digit d in the chosen position
                    raw = [SEG[' ']] * 4
                    raw[pos] = SEG[str(d)]
                    # set colon on second digit for readability
                    raw[1] = raw[1] | 0x80
                    if hasattr(disp, 'show'):
                        disp.show(raw)
                    time.sleep(0.18)
                except Exception as e:
                    # ignore individual failures
                    pass
    print('\nCycling complete. Final attempt: show 02:00 (raw)')
    try:
        disp.show([SEG['0'], SEG['2'] | 0x80, SEG['0'], SEG['0']])
    except Exception:
        pass
    time.sleep(2)
except KeyboardInterrupt:
    print('Interrupted by user')

print('Probe complete. If display looked garbled, try swapping CLK/DIO wires and re-run.')
print('Also ensure GND/VCC are solid and match Pi 3.3V.')

print('Cleaning up (if library exposes a cleanup)')
for fn in ('clear', 'reset', 'write', 'cleanup'):
    if hasattr(disp, fn):
        try:
            getattr(disp, fn)()
        except Exception:
            pass

print('Done')
