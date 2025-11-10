#!/usr/bin/env python3
"""
Comprehensive TM1637 Multi-Slot Display Test
=============================================

Tests all 4 TM1637 displays simultaneously with independent countdown timers.
Each slot gets its own display showing MM:SS format.

Features:
 - Per-slot independent countdown timers
 - Real-time display updates for all 4 slots
 - Brightness control via hardware
 - Graceful shutdown and cleanup
 - Detailed console logging of all operations

Requirements:
 - Run on Raspberry Pi with RPi.GPIO and spidev installed
 - All 4 TM1637 displays must be connected to GPIO pins per pinmap.json
 - Use --real flag to activate real hardware (default is simulation)

Usage:
  python3 test_tm1637_all_slots.py --real                 # Real hardware
  python3 test_tm1637_all_slots.py                        # Simulation only
  python3 test_tm1637_all_slots.py --real --duration 30   # 30 seconds per slot
  python3 test_tm1637_all_slots.py --real --brightness 3  # Brightness level 1-7
"""

import time
import threading
import json
import os
import sys
import argparse
from typing import Dict, Optional
from collections import defaultdict

# Add project root to path for imports
BASE = os.path.dirname(__file__)
sys.path.insert(0, BASE)

try:
    from hardware_gpio import HardwareGPIO
except ImportError as e:
    print(f"ERROR: Could not import hardware_gpio: {e}")
    sys.exit(1)


class MultiSlotTimerTest:
    """Manage 4 independent slot timer displays."""
    
    def __init__(self, hw: HardwareGPIO, durations: Dict[str, int], brightness: int = 2):
        """
        Initialize timer test.
        
        Args:
            hw: HardwareGPIO instance
            durations: Dict[slot_name] -> seconds (e.g. {'slot1': 30, 'slot2': 30, ...})
            brightness: Display brightness level 0-7
        """
        self.hw = hw
        self.durations = durations
        self.brightness = brightness
        
        # Per-slot state
        self.slots = {
            'slot1': {'remaining': durations.get('slot1', 30), 'running': False, 'timer': None},
            'slot2': {'remaining': durations.get('slot2', 30), 'running': False, 'timer': None},
            'slot3': {'remaining': durations.get('slot3', 30), 'running': False, 'timer': None},
            'slot4': {'remaining': durations.get('slot4', 30), 'running': False, 'timer': None},
        }
        
        # Thread safety
        self._locks = {slot: threading.Lock() for slot in self.slots}
        
        # Display instances per slot (lazy initialized)
        self.displays = {}
        
        # Statistics
        self.stats = defaultdict(lambda: {'ticks': 0, 'started': None, 'ended': None})

    def setup_displays(self):
        """Initialize TM1637 displays for all slots."""
        print("\n" + "=" * 60)
        print("INITIALIZING TM1637 DISPLAYS")
        print("=" * 60)
        
        for slot in self.slots:
            try:
                dio_map = self.hw.pinmap.get('tm1637', {}).get('dio', {})
                clk = self.hw.pinmap.get('tm1637', {}).get('clk')
                dio = dio_map.get(slot)
                
                if clk is None or dio is None:
                    print(f"⚠️  {slot}: Missing pinmap configuration (clk={clk}, dio={dio})")
                    self.displays[slot] = None
                    continue
                
                print(f"\n📍 {slot}:")
                print(f"   CLK pin: {clk}")
                print(f"   DIO pin: {dio}")
                
                # Try to use installed tm1637 library if available
                display_obj = self._init_display_for_slot(slot, clk, dio)
                self.displays[slot] = display_obj
                
                if display_obj:
                    # Test initial display
                    self._safe_display_update(slot, 0)
                    print(f"   ✅ Display initialized successfully")
                else:
                    print(f"   ⚠️  Display initialization failed")
                    
            except Exception as e:
                print(f"❌ {slot}: Error during initialization - {e}")
                self.displays[slot] = None

    def _init_display_for_slot(self, slot: str, clk: int, dio: int):
        """Initialize display for a specific slot."""
        try:
            # Try external tm1637 library first
            import tm1637 as _tm_lib
            disp = _tm_lib.TM1637(clk=clk, dio=dio)
            
            # Wrap to provide consistent interface
            class _DisplayWrapper:
                def __init__(self, d, brightness):
                    self.d = d
                    self._brightness = brightness
                    self._last_display = None
                
                def show_time(self, seconds: int):
                    if seconds == self._last_display:
                        return  # Avoid redundant writes
                    
                    mm = seconds // 60
                    ss = seconds % 60
                    
                    try:
                        if hasattr(self.d, 'numbers'):
                            self.d.numbers(mm, ss)
                        elif hasattr(self.d, 'show'):
                            self.d.show(f"{mm:02d}:{ss:02d}")
                        else:
                            return False
                        self._last_display = seconds
                        return True
                    except Exception as e:
                        print(f"       Display error: {e}")
                        return False
                
                def set_brightness(self, level: int):
                    try:
                        level = int(level) & 0x07
                        if hasattr(self.d, 'brightness'):
                            self.d.brightness(level)
                        elif hasattr(self.d, 'set_brightness'):
                            self.d.set_brightness(level)
                        elif hasattr(self.d, 'setLight'):
                            self.d.setLight(level)
                        self._brightness = level
                    except Exception as e:
                        print(f"       Brightness error: {e}")
            
            wrapper = _DisplayWrapper(disp, self.brightness)
            wrapper.set_brightness(self.brightness)
            return wrapper
            
        except ImportError:
            # Fall back to hardware_gpio TM1637Display
            print(f"   (Using built-in TM1637Display driver)")
            try:
                from hardware_gpio import TM1637Display
                disp = TM1637Display(
                    clk_pin=clk,
                    dio_pin=dio,
                    gpio=self.hw.gpio,
                    mode=self.hw.mode
                )
                disp.set_brightness(self.brightness)
                return disp
            except Exception as e:
                print(f"       Fallback failed: {e}")
                return None

    def _safe_display_update(self, slot: str, seconds: int):
        """Safely update display for a slot (thread-safe)."""
        try:
            with self._locks[slot]:
                disp = self.displays.get(slot)
                if disp:
                    return disp.show_time(seconds)
        except Exception as e:
            print(f"⚠️  {slot}: Display update error - {e}")
        return False

    def start_all_timers(self):
        """Start countdown timers for all slots."""
        print("\n" + "=" * 60)
        print("STARTING ALL TIMERS")
        print("=" * 60)
        
        for slot in self.slots:
            self.start_timer(slot)
            time.sleep(0.1)  # Stagger slightly to avoid contention

    def start_timer(self, slot: str):
        """Start countdown timer for a single slot."""
        with self._locks[slot]:
            if self.slots[slot]['running']:
                print(f"⚠️  {slot}: Already running")
                return
            
            self.slots[slot]['running'] = True
            self.stats[slot]['started'] = time.time()
            print(f"▶️  {slot}: Timer started ({self.slots[slot]['remaining']}s)")
            
            # Schedule first tick
            self._schedule_tick(slot)

    def _schedule_tick(self, slot: str):
        """Schedule next timer tick."""
        with self._locks[slot]:
            if not self.slots[slot]['running']:
                return
            
            timer = threading.Timer(1.0, self._tick, args=[slot])
            self.slots[slot]['timer'] = timer
            timer.daemon = True
            timer.start()

    def _tick(self, slot: str):
        """Execute single timer tick for a slot."""
        with self._locks[slot]:
            if not self.slots[slot]['running']:
                return
            
            remaining = self.slots[slot]['remaining']
            remaining = max(0, remaining - 1)
            self.slots[slot]['remaining'] = remaining
            self.stats[slot]['ticks'] += 1
            
            # Update display
            self._safe_display_update(slot, remaining)
            
            # Console output
            if remaining % 5 == 0 or remaining <= 3:
                status = '⏱️ ' if remaining > 0 else '✅'
                print(f"{status} {slot}: {remaining:02d}s")
            
            # Check if done
            if remaining <= 0:
                self.slots[slot]['running'] = False
                self.stats[slot]['ended'] = time.time()
                print(f"⏹️  {slot}: Countdown complete")
                return
            
            # Schedule next tick
            self._schedule_tick(slot)

    def stop_all_timers(self):
        """Stop all running timers."""
        print("\n" + "=" * 60)
        print("STOPPING ALL TIMERS")
        print("=" * 60)
        
        for slot in self.slots:
            self.stop_timer(slot)

    def stop_timer(self, slot: str):
        """Stop a single timer."""
        with self._locks[slot]:
            if not self.slots[slot]['running']:
                return
            
            self.slots[slot]['running'] = False
            timer = self.slots[slot]['timer']
            if timer:
                timer.cancel()
            
            print(f"⏸️  {slot}: Timer stopped (remaining: {self.slots[slot]['remaining']}s)")

    def print_summary(self):
        """Print test summary."""
        print("\n" + "=" * 60)
        print("TEST SUMMARY")
        print("=" * 60)
        
        for slot in sorted(self.slots.keys()):
            stat = self.stats[slot]
            ticks = stat['ticks']
            duration = None
            if stat['started'] and stat['ended']:
                duration = stat['ended'] - stat['started']
            
            print(f"\n{slot}:")
            print(f"  Ticks executed: {ticks}")
            if duration:
                print(f"  Duration: {duration:.1f}s")
            print(f"  Final remaining: {self.slots[slot]['remaining']}s")

    def wait_for_completion(self, timeout: Optional[float] = None):
        """Wait for all timers to complete."""
        start = time.time()
        while True:
            all_done = all(not self.slots[s]['running'] for s in self.slots)
            if all_done:
                break
            
            if timeout and (time.time() - start) > timeout:
                print(f"\n⚠️  Timeout reached ({timeout}s). Stopping remaining timers...")
                self.stop_all_timers()
                break
            
            time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(
        description="Test all 4 TM1637 displays with independent countdown timers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 test_tm1637_all_slots.py --real                    # Real hardware, default 30s/slot
  python3 test_tm1637_all_slots.py --real --duration 60      # 60 seconds per slot
  python3 test_tm1637_all_slots.py --real --brightness 5     # Brightness level 5 (0-7)
  python3 test_tm1637_all_slots.py                           # Simulation mode (no hardware)
        """
    )
    
    parser.add_argument(
        '--real',
        action='store_true',
        help='Use real Raspberry Pi GPIO (required for actual hardware test)'
    )
    parser.add_argument(
        '--duration',
        type=int,
        default=30,
        help='Countdown duration per slot in seconds (default: 30)'
    )
    parser.add_argument(
        '--brightness',
        type=int,
        default=2,
        choices=range(0, 8),
        help='Display brightness level 0-7 (default: 2)'
    )
    parser.add_argument(
        '--skip-display-test',
        action='store_true',
        help='Skip per-slot display verification'
    )
    
    args = parser.parse_args()
    
    # Load pinmap
    pinmap_path = os.path.join(BASE, 'pinmap.json')
    try:
        with open(pinmap_path, 'r', encoding='utf-8') as f:
            pinmap = json.load(f)
    except Exception as e:
        print(f"ERROR: Could not load pinmap.json: {e}")
        sys.exit(1)
    
    # Initialize hardware
    mode = 'real' if args.real else 'sim'
    print(f"\n🔧 Hardware mode: {mode.upper()}")
    
    try:
        hw = HardwareGPIO(pinmap=pinmap, mode=mode)
        hw.setup()
    except Exception as e:
        print(f"ERROR: Failed to initialize hardware: {e}")
        if args.real:
            print("\nMake sure you're running on a Raspberry Pi with RPi.GPIO and spidev installed:")
            print("  pip install RPi.GPIO spidev tm1637")
        sys.exit(1)
    
    # Create and run test
    print(f"\n⚙️  Configuration:")
    print(f"   Duration per slot: {args.duration}s")
    print(f"   Display brightness: {args.brightness}")
    
    durations = {slot: args.duration for slot in ['slot1', 'slot2', 'slot3', 'slot4']}
    test = MultiSlotTimerTest(hw, durations, brightness=args.brightness)
    
    try:
        test.setup_displays()
        
        if not args.skip_display_test:
            print("\n" + "=" * 60)
            print("DISPLAY TEST: Each display will show 00:05 for 2 seconds")
            print("=" * 60)
            for slot in ['slot1', 'slot2', 'slot3', 'slot4']:
                print(f"\n🔍 Testing {slot}...")
                test._safe_display_update(slot, 5)
                time.sleep(2)
                test._safe_display_update(slot, 0)
        
        # Start all timers
        test.start_all_timers()
        
        # Wait for completion or user interrupt
        print(f"\n⏳ Waiting for all timers to complete (max {args.duration + 10}s)...")
        test.wait_for_completion(timeout=args.duration + 10)
        
        # Print summary
        test.print_summary()
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        test.stop_all_timers()
        test.print_summary()
    
    except Exception as e:
        print(f"\n❌ Error during test: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        print("\n" + "=" * 60)
        print("CLEANING UP")
        print("=" * 60)
        
        # Stop all timers
        try:
            test.stop_all_timers()
        except Exception:
            pass
        
        # Cleanup hardware
        try:
            hw.cleanup()
            print("✅ Hardware cleanup complete")
        except Exception as e:
            print(f"⚠️  Cleanup error: {e}")
        
        print("\n✨ Test finished.\n")


if __name__ == '__main__':
    main()
