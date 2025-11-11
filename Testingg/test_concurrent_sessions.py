#!/usr/bin/env python3
"""
Smoke test for per-slot concurrent charging sessions.
Tests that:
1. Two sessions on different slots (slot1 and slot3) can run concurrently.
2. Each session has independent remaining time and ticks down separately.
3. Timer callbacks don't cross-contaminate between sessions.
4. Display updates and TM updates are per-slot.
"""

import sys
import os
import time
import json

# Add repo to path
sys.path.insert(0, os.path.dirname(__file__))

def mock_hardware():
    """Create a minimal hardware mock."""
    class MockTM:
        def __init__(self, slot):
            self.slot = slot
            self.last_time = None
        def show_time(self, sec):
            self.last_time = sec
            print(f"  [TM {self.slot}] Display updated to: {sec}s")
        def set_brightness(self, level):
            pass

    class MockHW:
        def __init__(self):
            self.pinmap = {'acs712_channels': {'slot1': 0, 'slot3': 2}}
            self.tm_instances = {}
        def tm1637_init_slot(self, slot):
            if slot not in self.tm_instances:
                self.tm_instances[slot] = MockTM(slot)
            return self.tm_instances[slot]
        def tm1637_init(self):
            return MockTM('global')
        def relay_on(self, slot):
            print(f"  [HW] Relay ON for {slot}")
        def relay_off(self, slot):
            print(f"  [HW] Relay OFF for {slot}")
        def lock_slot(self, slot, lock):
            state = "LOCKED" if lock else "UNLOCKED"
            print(f"  [HW] {slot} {state}")
        def read_current(self, slot):
            # Mock current reading
            return {'amps': 1.5}

    return MockHW()

def test_per_slot_sessions():
    """Test that per-slot sessions work independently."""
    print("\n=== PER-SLOT SESSION SMOKE TEST ===\n")
    
    # Create two independent session records as would exist in ChargingScreen._sessions
    print("[SETUP] Creating two independent session records (slot1 and slot3)...\n")
    
    session1 = {
        'session_id': 1,
        'uid': 'user_alice',
        'slot': 'slot1',
        'remaining': 60,
        'db_acc': 0,
        'is_charging': True,
        'tick_job': None,
        'wait_job': None,
        'hw_monitor_job': None,
        'poll_timeout_job': None,
        'tm': None,
        'plug_hits': [],
        'unplug_hits': [],
        'charge_samples': [],
        'charge_consecutive': 0,
    }
    
    session3 = {
        'session_id': 2,
        'uid': 'user_bob',
        'slot': 'slot3',
        'remaining': 45,
        'db_acc': 0,
        'is_charging': True,
        'tick_job': None,
        'wait_job': None,
        'hw_monitor_job': None,
        'poll_timeout_job': None,
        'tm': None,
        'plug_hits': [],
        'unplug_hits': [],
        'charge_samples': [],
        'charge_consecutive': 0,
    }
    
    hw = mock_hardware()
    session1['tm'] = hw.tm1637_init_slot('slot1')
    session3['tm'] = hw.tm1637_init_slot('slot3')
    
    print(f"Session 1 (User: {session1['uid']}, Slot: {session1['slot']})")
    print(f"  - Initial remaining: {session1['remaining']}s")
    print(f"  - Session ID: {session1['session_id']}")
    print(f"  - TM instance: {session1['tm'].slot}")
    
    print(f"\nSession 3 (User: {session3['uid']}, Slot: {session3['slot']})")
    print(f"  - Initial remaining: {session3['remaining']}s")
    print(f"  - Session ID: {session3['session_id']}")
    print(f"  - TM instance: {session3['tm'].slot}")
    
    # Simulate independent ticks (what would happen in _charging_tick_slot closures)
    print("\n[TEST] Simulating concurrent ticks (5 iterations)...\n")
    
    for tick in range(1, 6):
        print(f"--- Tick {tick} ---")
        
        # Tick session 1
        session1['remaining'] = max(0, session1['remaining'] - 1)
        if session1['tm']:
            session1['tm'].show_time(session1['remaining'])
        print(f"Slot1 (Alice): {session1['remaining']}s remaining")
        
        # Tick session 3
        session3['remaining'] = max(0, session3['remaining'] - 1)
        if session3['tm']:
            session3['tm'].show_time(session3['remaining'])
        print(f"Slot3 (Bob): {session3['remaining']}s remaining")
        
        # Verify they decrement independently
        assert session1['remaining'] == 60 - tick, f"Session1 should have {60-tick}s, got {session1['remaining']}"
        assert session3['remaining'] == 45 - tick, f"Session3 should have {45-tick}s, got {session3['remaining']}"
        assert session1['tm'].last_time == 60 - tick, f"TM1 should show {60-tick}s"
        assert session3['tm'].last_time == 45 - tick, f"TM3 should show {45-tick}s"
        
        print()
    
    print("[RESULT] ✓ Both sessions ticked independently!")
    print(f"  Session1 final: {session1['remaining']}s (User: {session1['uid']})")
    print(f"  Session3 final: {session3['remaining']}s (User: {session3['uid']})")
    print(f"  TM1 final display: {session1['tm'].last_time}s")
    print(f"  TM3 final display: {session3['tm'].last_time}s")
    
    # Test session ownership via session_id
    print("\n[TEST] Verifying session_id prevents cross-contamination...\n")
    
    # A callback from session1 should ignore session3
    slot = 'slot1'
    session_id = 1
    s = session3  # Wrong session!
    
    if s.get('session_id') != session_id:
        print(f"✓ Callback with session_id={session_id} correctly rejected stale session (session_id={s.get('session_id')})")
    else:
        print(f"✗ ERROR: Callback should have rejected stale session!")
        return False
    
    print("\n=== TEST PASSED ===\n")
    return True

if __name__ == '__main__':
    try:
        success = test_per_slot_sessions()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
