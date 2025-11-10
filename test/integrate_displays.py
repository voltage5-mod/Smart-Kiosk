#!/usr/bin/env python3
"""
Integration Helper: Add TM1637 Display Support to FULL_UI.py
=============================================================

This script shows code snippets to add display support to your charging/water sessions.

Usage:
1. Copy snippets from below into FULL_UI.py
2. Or run: python3 integrate_displays.py --apply

See documentation below for details.
"""

SESSIONMANAGER_INIT_ADDON = """
    def __init__(self, controller):
        self.controller = controller
        self.sessions = {}  # slot -> session dict
        
        # Initialize TM1637 displays for all slots
        self.displays = {}
        self.hw = getattr(controller, 'hw', None)
        if self.hw:
            for slot in ['slot1', 'slot2', 'slot3', 'slot4']:
                try:
                    disp = self.hw.tm1637_init(slot)
                    if disp:
                        self.displays[slot] = disp
                        print(f'[SessionManager] Display initialized for {slot}')
                except Exception as e:
                    print(f'[SessionManager] Failed to init display for {slot}: {e}')
"""

TICK_METHOD_ADDON = """
    def _tick(self, slot):
        sess = self.sessions.get(slot)
        if not sess:
            self._schedule_tick(slot, delay=1000)
            return
        if not sess.get('is_charging'):
            self._schedule_tick(slot, delay=1000)
            return
        
        sess['remaining'] = max(0, sess['remaining'] - 1)
        
        # === NEW: Update TM1637 display ===
        try:
            disp = self.displays.get(slot)
            if disp:
                disp.show_time(sess['remaining'])
        except Exception as e:
            print(f'[SessionManager {slot}] Display update error: {e}')
        
        # persist to DB periodically
        try:
            if sess['remaining'] % CHARGE_DB_WRITE_INTERVAL == 0:
                user_ref = users_ref.child(sess['uid'])
                user_ref.update({'charge_balance': sess['remaining']})
        except Exception as e:
            print(f'[SessionManager {slot}] DB write error: {e}')
        
        # refresh UI
        try:
            self.controller.refresh_all_user_info()
        except Exception:
            pass
        
        # check if time is up
        if sess['remaining'] <= 0:
            self.end_session(slot, reason='time_up')
            return
        
        # reschedule tick
        self._schedule_tick(slot)
"""

END_SESSION_ADDON = """
    def end_session(self, slot, reason='manual'):
        sess = self.sessions.get(slot)
        if not sess:
            return
        
        uid = sess['uid']
        
        # Cancel scheduled jobs
        for job_type in ['tick_job', 'poll_job', 'monitor_job']:
            job = sess.get(job_type)
            if job:
                try:
                    self.controller.after_cancel(job)
                except Exception:
                    pass
        
        # Power off and unlock
        if self.hw:
            try:
                self.hw.relay_off(slot)
                self.hw.lock_slot(slot, lock=False)
            except Exception:
                pass
        
        # === NEW: Clear display ===
        try:
            disp = self.displays.get(slot)
            if disp:
                disp.show_time(0)  # Shows 00:00
        except Exception as e:
            print(f'[SessionManager {slot}] Display clear error: {e}')
        
        # Update DB
        try:
            user_ref = users_ref.child(uid)
            user_ref.update({
                'occupied_slot': 'none',
                'charging_status': 'idle',
            })
            slot_ref = slots_ref.child(slot)
            slot_ref.update({
                'status': 'inactive',
                'current_user': 'none',
            })
        except Exception as e:
            print(f'[SessionManager {slot}] DB update error: {e}')
        
        # Audit log
        try:
            append_audit_log(
                root='audit_log',
                actor=uid,
                action='charging_finished',
                meta={'slot': slot, 'reason': reason, 'final_balance': sess['remaining']}
            )
        except Exception:
            pass
        
        # Clean up session
        try:
            del self.sessions[slot]
        except Exception:
            pass
        
        # Refresh UI
        try:
            self.controller.refresh_all_user_info()
        except Exception:
            pass
"""

WATER_TICK_ADDON = """
# === For WaterScreen water dispensing ===
# In _water_tick_member() or _water_tick_nonmember():

def _water_tick_member(self):
    '''Member using stored water_balance.'''
    if not self.water_running:
        self.after_cancel(self.water_tick_job)
        return
    
    self.water_remaining = max(0, self.water_remaining - 1)
    
    # === Update display if water session uses a slot ===
    # Note: Water dispensing typically doesn't use a charge slot,
    # but if it does, update the display:
    # try:
    #     if self.controller.hw:
    #         disp = self.controller.hw.tm1637_init('slot_water')  # or another display
    #         if disp:
    #             disp.show_time(self.water_remaining)
    # except Exception:
    #     pass
    
    # Update UI label
    self.time_label.config(text=f'Time Remaining: {self.water_remaining}s')
    
    # Deduct from DB periodically
    try:
        if self.water_remaining % WATER_DB_WRITE_INTERVAL == 0:
            user_ref = users_ref.child(self.controller.active_uid)
            user_ref.update({'water_balance': self.water_remaining})
    except Exception:
        pass
    
    if self.water_remaining <= 0:
        self.stop_session()
        return
    
    self.water_tick_job = self.after(1000, self._water_tick_member)
"""

INTEGRATION_STEPS = """
# ============================================================
# STEP-BY-STEP INTEGRATION GUIDE
# ============================================================

## Step 1: Verify hardware_gpio.py is Updated

The new tm1637_init(slot) method should support:
  - tm1637_init('slot1') -> returns display object for slot1
  - tm1637_init('slot2') -> returns display object for slot2
  - etc.

Verify in hardware_gpio.py around line 343:

    def tm1637_init(self, slot: str = 'slot1'):
        # ... (supports per-slot displays)


## Step 2: Update SessionManager.__init__()

In FULL_UI.py, find the SessionManager class and add display initialization:

Location: Around line XXX

OLD CODE:
    def __init__(self, controller):
        self.controller = controller
        self.sessions = {}

NEW CODE:
    def __init__(self, controller):
        self.controller = controller
        self.sessions = {}
        
        # Initialize TM1637 displays
        self.displays = {}
        self.hw = getattr(controller, 'hw', None)
        if self.hw:
            for slot in ['slot1', 'slot2', 'slot3', 'slot4']:
                try:
                    disp = self.hw.tm1637_init(slot)
                    if disp:
                        self.displays[slot] = disp
                except Exception as e:
                    print(f'Failed to init display for {slot}: {e}')


## Step 3: Update _tick() Method

In SessionManager._tick(slot), add display update:

Location: Around line XXX (inside _tick method body)

ADD THIS CODE (right after sess['remaining'] = max(0, ...) line):

    # Update TM1637 display
    try:
        disp = self.displays.get(slot)
        if disp:
            disp.show_time(sess['remaining'])
    except Exception as e:
        print(f'Display update error: {e}')


## Step 4: Update end_session() Method

In SessionManager.end_session(slot, reason), clear the display:

Location: Around line XXX (near start of end_session)

ADD THIS CODE (before updating DB):

    # Clear display
    try:
        disp = self.displays.get(slot)
        if disp:
            disp.show_time(0)  # Shows 00:00
    except Exception as e:
        print(f'Display clear error: {e}')


## Step 5: Test Integration

Run the full UI with hardware:

    python3 FULL_UI.py --debug

Then:
1. Scan a UID
2. Select Charging
3. Pick a slot
4. Insert a coin (simulated)
5. Start charging
6. Watch the display count down
7. Verify all 4 slot displays work independently


## Step 6: Verify in Production

Before final deployment:
  [ ] All 4 displays show countdowns
  [ ] Displays update every second (no lag)
  [ ] Displays clear (00:00) when session ends
  [ ] No memory leaks during long sessions
  [ ] UI remains responsive while displays update
"""

TROUBLESHOOTING = """
# ============================================================
# TROUBLESHOOTING DISPLAY INTEGRATION
# ============================================================

## Display Not Showing During Charging

1. Check if tm1637_init() is called in SessionManager.__init__()
2. Verify self.displays[slot] is not None
3. Look for errors in console output
4. Test with: python3 test_tm1637_all_slots.py --real

## Display Shows But Doesn't Update

1. Verify disp.show_time() is called in _tick()
2. Check if _tick() is being called (add print statements)
3. Ensure charging_status is 'charging' (not stuck in 'pending')

## Display Freezes or Updates Slowly

1. Check for threading issues (Tk operations on wrong thread)
2. Reduce display update frequency (every N seconds instead of every second)
3. Profile with: python3 -m cProfile -s cumtime FULL_UI.py

## GPIO Conflict

1. Verify pinmap.json pins match your wiring
2. Run: gpio readall (on Pi)
3. Check for other services using GPIO
4. Restart the UI: python3 FULL_UI.py

## Multiple Displays Interfering

1. Each slot has independent DIO pin (pinmap.json)
2. All share CLK pin (GPIO 5)
3. If interference occurs, check:
   - SPI clock speed (hardware_gpio.py line ~120)
   - Display library compatibility (try: pip install tm1637)
   - Voltage levels (check RPi 3.3V supply)
"""

if __name__ == '__main__':
    print(__doc__)
    print("\n" + "="*60)
    print("CODE SNIPPETS FOR INTEGRATION")
    print("="*60)
    
    print("\n" + "-"*60)
    print("1. SessionManager.__init__() - Add Display Init")
    print("-"*60)
    print(SESSIONMANAGER_INIT_ADDON)
    
    print("\n" + "-"*60)
    print("2. SessionManager._tick() - Add Display Update")
    print("-"*60)
    print(TICK_METHOD_ADDON)
    
    print("\n" + "-"*60)
    print("3. SessionManager.end_session() - Clear Display")
    print("-"*60)
    print(END_SESSION_ADDON)
    
    print("\n" + "-"*60)
    print("4. WaterScreen._water_tick_*() - Optional Water Display")
    print("-"*60)
    print(WATER_TICK_ADDON)
    
    print("\n" + "="*60)
    print("STEP-BY-STEP INTEGRATION")
    print("="*60)
    print(INTEGRATION_STEPS)
    
    print("\n" + "="*60)
    print("TROUBLESHOOTING")
    print("="*60)
    print(TROUBLESHOOTING)
