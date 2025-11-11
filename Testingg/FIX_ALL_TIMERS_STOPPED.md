# Fix for "All Timers Stopped" Issue

## Problem
When User A logs out after starting charging on slot1, then User B logs in and starts charging on slot2, all timers stop immediately after starting.

## Root Cause
The `refresh()` method was using **global session state** (`self.charging_uid`, `self._session_valid`, `self._tick_job`, etc.) to manage single-session charging. When User B logged in and `refresh()` was called, it detected a user change and **globally invalidated the session**, setting `_session_valid = False`. This killed all timer callbacks even for new sessions.

## Solution Implemented
1. **Removed global session invalidation from `refresh()`**: The new `refresh()` method is now a **display-only** method that:
   - Updates the slot label
   - Updates user info
   - Displays the active slot's remaining time (if a per-slot session exists for that slot)
   - Does NOT manage session lifecycle or cancel timers

2. **Per-slot session isolation via closures**: Each session's callbacks are defined as closures within `start_charging()` that capture `slot` and `session_id`. This ensures:
   - Session callbacks only touch their own slot's state in `self._sessions[slot]`
   - Each callback validates session ownership before operating: `if not s or s.get('session_id') != session_id: return`
   - Old callbacks from previous sessions silently exit on validation failure

3. **Removed obsolete global state checks**: Deleted references to:
   - `self._session_valid` (no longer used)
   - `self._current_session_id` (no longer used)
   - These were artifacts from the earlier single-session approach

## Key Changes to `refresh()`

**Before (problematic):**
```python
def refresh(self):
    uid = self.controller.active_uid
    slot = self.controller.active_slot or "none"
    
    # CRITICAL: If the active user has changed (different from the session owner),
    # IMMEDIATELY INVALIDATE THE SESSION to stop all background timers
    if uid and self.charging_uid and uid != self.charging_uid:
        self._session_valid = False  # ← KILLED ALL TIMERS GLOBALLY!
        # ... cancel all global jobs ...
```

**After (fixed):**
```python
def refresh(self):
    """Refresh display only. Per-slot sessions manage their own state via closures."""
    uid = self.controller.active_uid
    slot = self.controller.active_slot or "none"
    
    # Just update display; don't manage session state
    display_text = f"Charging Slot {slot[4:] if slot and slot.startswith('slot') else slot}"
    self.slot_lbl.config(text=display_text)
    # ... show per-slot session's remaining if active ...
```

## How Multi-User Charging Now Works

**Scenario: User A on slot1, then User B on slot2**

1. **User A starts charging on slot1:**
   - `start_charging()` creates `self._sessions['slot1']` with `session_id=1`
   - Closures capture `slot='slot1'` and `session_id=1`
   - `_charging_tick_slot()` scheduled and begins ticking

2. **User A logs out, User B logs in:**
   - `refresh()` is called (now does nothing disruptive)
   - Old `self._sessions['slot1']` remains untouched (still ticking if no explicit stop)

3. **User B starts charging on slot2:**
   - `start_charging()` creates `self._sessions['slot2']` with `session_id=2` (incremented)
   - Closures capture `slot='slot2'` and `session_id=2` (different!)
   - `_charging_tick_slot()` for slot2 scheduled and begins ticking
   - Both slot1 and slot2 tick independently

4. **Both sessions run concurrently** with full isolation

## Testing the Fix

**Manual Test Script:**
```python
# 1. User A logs in, selects slot1, clicks "Start Charging"
#    → Watch terminal output for: [START_CHG] Slot=slot1, Balance=60s, HW=True
#    → Should see: [TICK slot1] user_a: 59s, 58s, 57s, ... (counting down)

# 2. User A logs out

# 3. User B logs in, selects slot2, clicks "Start Charging"
#    → Watch terminal output for: [START_CHG] Slot=slot2, Balance=45s, HW=True
#    → Should see: [TICK slot2] user_b: 44s, 43s, 42s, ... (counting down independently)

# 4. Verify both slots' times appear on their respective TM1637 displays
#    → TM1 (slot1) shows ~55s
#    → TM3 (slot2) shows ~40s (assuming ~5s elapsed)
```

**Expected Output (Debug Logs):**
```
[START_CHG] User=user_a, Slot=slot1, Balance=60s, HW=True
[START_CHG] Creating new session for slot1 with session_id=1
[START_CHG] slot1 scheduled unlock → poll flow with wait_job=4567
...time passes...
[TICK slot1] user_a: 59s
[TICK slot1] user_a: 58s
...
[START_CHG] User=user_b, Slot=slot2, Balance=45s, HW=True
[START_CHG] Creating new session for slot2 with session_id=2
[START_CHG] slot2 scheduled unlock → poll flow with wait_job=4890
...both ticking simultaneously...
[TICK slot1] user_a: 45s
[TICK slot2] user_b: 35s
[TICK slot1] user_a: 44s
[TICK slot2] user_b: 34s
```

## Remaining Legacy Code

The file still contains old methods like `_charging_tick()`, `_poll_for_charging_start()`, etc. These are **NOT called** by the new per-slot code. They are kept for backward compatibility. Future cleanup could remove these entirely if they're never called.

## Verification Commands

```bash
# Compile check
python -m py_compile UI-HD.py

# Run the kiosk app and test multi-user scenario
python UI-HD.py
```

Monitor the terminal output for the debug messages listed above to confirm both sessions are ticking independently.
