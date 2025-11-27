"""
db/firebase_helpers.py

Lightweight Firebase helper utilities and a generic process_payload dispatcher
for use by db/firebase_worker.FirebaseWorker.

Original helper functions retained:
 - append_audit_log(db_ref, actor, action, meta)
 - deduct_charge_balance_transactionally(users_ref, uid, seconds_to_deduct)

New additions:
 - process_payload(payload) -> bool
    Generic dispatcher mapping op -> firebase writes.
    Returns True on success, False on transient failure.

Idempotency:
 - If payload contains an 'id' key, we attempt to record processing under /idempotency/{id}
   and skip processing if already present. This prevents duplicate DB writes from retries.
"""

from __future__ import annotations
import logging
import time
import json
from typing import Any, Dict, Optional

# firebase imports
try:
    from firebase_admin import db
    HAS_FIREBASE = True
except Exception:
    db = None  # type: ignore
    HAS_FIREBASE = False

_LOGGER = logging.getLogger("db.firebase_helpers")

# If Firebase isn't initialized, use an in-memory fallback store to support UI testing.
_local_store: Dict[str, Dict[str, Any]] = {
    "users": {},
    "slots": {},
    "registration_requests": {},
    "subscription_requests": {},
    "audit_log": {}
}

# Expose users_ref / slots_ref proxies for code that expects a db.Reference-like API.
# If firebase present, these are actual db.reference objects. Otherwise None; callers can use provided read/write helpers.
users_ref = None
slots_ref = None
if HAS_FIREBASE:
    try:
        users_ref = db.reference("/users")
        slots_ref = db.reference("/slots")
    except Exception:
        users_ref = None
        slots_ref = None


def append_audit_log(root: str = '/audit_log', actor: str = 'system', action: str = 'unknown', meta: Optional[Dict[str, Any]] = None) -> bool:
    try:
        ts = int(time.time() * 1000)
        rec = {
            'ts': ts,
            'actor': actor,
            'action': action,
            'meta': meta or {}
        }
        if HAS_FIREBASE and db is not None:
            ref = db.reference(root)
            ref.push(rec)
        else:
            # store in in-memory fallback (append list)
            _local_store.setdefault('audit_log', {})
            # use ts as key for readability
            _local_store['audit_log'][str(ts)] = rec
        _LOGGER.debug("append_audit_log: %s %s", actor, action)
        return True
    except Exception:
        _LOGGER.exception("append_audit_log failed")
        return False


def deduct_charge_balance_transactionally(users_root, uid: str, seconds_to_deduct: int) -> Optional[int]:
    try:
        if not HAS_FIREBASE or users_root is None:
            _LOGGER.warning("deduct_charge_balance_transactionally: firebase not available")
            # Fallback: modify in-memory user if exists
            u = _local_store['users'].get(uid)
            if not u:
                return None
            bal = int(u.get('charge_balance', 0) or 0)
            new = max(0, bal - int(seconds_to_deduct))
            u['charge_balance'] = new
            u['last_balance_update_ts'] = int(time.time() * 1000)
            _local_store['users'][uid] = u
            return new

        user_ref = users_root.child(uid)

        def txn(current):
            if current is None:
                return None
            balance = current.get('charge_balance', 0)
            try:
                balance = int(balance)
            except Exception:
                balance = 0
            new_balance = max(0, balance - int(seconds_to_deduct))
            current['charge_balance'] = new_balance
            current['last_balance_update_ts'] = int(time.time() * 1000)
            return current

        result = user_ref.transaction(txn)
        if result is None:
            return None
        return result.get('charge_balance')
    except Exception:
        _LOGGER.exception("deduct_charge_balance_transactionally failed")
        return None


# -------------------------
# Simple read/write helpers for Users and Slots
# -------------------------
def user_exists(uid: str) -> bool:
    try:
        if HAS_FIREBASE and users_ref is not None:
            return users_ref.child(uid).get() is not None
        else:
            return uid in _local_store['users']
    except Exception:
        _LOGGER.exception("user_exists failed for %s", uid)
        return False


def create_nonmember(uid: str):
    try:
        data = {
            "type": "nonmember",
            "name": "Guest",
            "student_id": "",
            "water_balance": None,
            "charge_balance": 0,
            "occupied_slot": "none",
            "charging_status": "idle",
            "slot_status": {}
        }
        if HAS_FIREBASE and users_ref is not None:
            users_ref.child(uid).set(data)
        else:
            _local_store['users'][uid] = data
        return True
    except Exception:
        _LOGGER.exception("create_nonmember failed for %s", uid)
        return False


def read_user(uid: str) -> Optional[Dict[str, Any]]:
    try:
        if HAS_FIREBASE and users_ref is not None:
            return users_ref.child(uid).get()
        else:
            return _local_store['users'].get(uid)
    except Exception:
        _LOGGER.exception("read_user failed for %s", uid)
        return None


def write_user(uid: str, data: Dict[str, Any]):
    try:
        if HAS_FIREBASE and users_ref is not None:
            users_ref.child(uid).update(data)
        else:
            # merge/update in-memory
            cur = _local_store['users'].get(uid, {})
            cur.update(data)
            _local_store['users'][uid] = cur
        return True
    except Exception:
        _LOGGER.exception("write_user failed for %s", uid)
        return False


def read_slot(slot: str) -> Optional[Dict[str, Any]]:
    try:
        if HAS_FIREBASE and slots_ref is not None:
            return slots_ref.child(slot).get()
        else:
            return _local_store['slots'].get(slot)
    except Exception:
        _LOGGER.exception("read_slot failed for %s", slot)
        return None


def write_slot(slot: str, data: Dict[str, Any]):
    try:
        if HAS_FIREBASE and slots_ref is not None:
            slots_ref.child(slot).update(data)
        else:
            cur = _local_store['slots'].get(slot, {})
            cur.update(data)
            _local_store['slots'][slot] = cur
        return True
    except Exception:
        _LOGGER.exception("write_slot failed for %s", slot)
        return False


# -------------------------
# process_payload and other original helper functions follow...
# The rest of your previous process_payload/dedicated functions are preserved below.
# (Paste or keep the existing implementations from your earlier file.)
#
# For brevity in this message I won't repeat the large process_payload
# implementation (you already have it). Ensure process_payload is still present
# in this file (unchanged).
# -------------------------
# If your previous file included process_payload, append it here unchanged.

# -------------------------
# High-level process_payload
# -------------------------
def _mark_idempotent(id_key: str) -> bool:
    """
    Try to create /idempotency/{id_key} with timestamp if it doesn't exist.
    Returns True if we successfully reserved (i.e., this payload was NOT processed before).
    Returns False if already present (so caller should skip processing).
    This uses a set-if-not-exists approach (set with a shallow check).
    """
    if not HAS_FIREBASE:
        # If no firebase, don't attempt idempotency; caller will handle retries or dead-lettering.
        return True
    try:
        ref = db.reference(f"/idempotency/{id_key}")
        # Read once; if present, skip. This is simple and safe but not perfectly atomic.
        current = ref.get()
        if current is not None:
            _LOGGER.debug("Idempotency: payload id=%s already processed", id_key)
            return False
        # write marker
        ref.set({"ts": int(time.time() * 1000)})
        return True
    except Exception:
        # On any error, return True so we don't block processing; worker may retry on failure.
        _LOGGER.exception("Idempotency check failed for id=%s", id_key)
        return True


def _try_ref(path: str):
    """Convenience: return a db.reference or raise if unavailable."""
    if not HAS_FIREBASE:
        raise RuntimeError("firebase_admin.db not available (not initialized?)")
    return db.reference(path)


def process_payload(payload: Dict[str, Any]) -> bool:
    """
    Generic dispatcher to persist payloads to Firebase Realtime DB.

    Expected payload pattern:
      {"op": "session_start", "session": { ... }, "id": "<optional-id>"}
      {"op": "coin", "session_id":"...", "value": 5, "id": "..."}
      {"op": "dispense_increment", "session_id":"...", "ml": 50}
      {"op": "slot_charging_start", "slot": 1, "session_id":"...", "amps": 1.23}
    Returns True on success, False on transient failure (so worker can retry).
    """
    if not isinstance(payload, dict):
        _LOGGER.error("process_payload called with non-dict: %r", payload)
        return True  # treat as handled to avoid retry loops

    op = payload.get("op")
    if not op:
        _LOGGER.error("process_payload missing 'op' field: %r", payload)
        return True

    # Optional idempotency guard: if payload includes 'id', skip if already processed.
    pid = payload.get("id")
    if pid:
        ok = _mark_idempotent(pid)
        if not ok:
            _LOGGER.info("Skipping already-processed payload id=%s op=%s", pid, op)
            return True

    try:
        # Dispatch table
        if op == "session_start":
            sess = payload.get("session") or {}
            sid = sess.get("session_id") or sess.get("id")
            if not sid:
                _LOGGER.error("session_start missing session.session_id")
                return True
            ref = _try_ref(f"/sessions/{sid}")
            # set minimal fields (don't overwrite entire session if exists)
            ref.update({
                "uid": sess.get("uid"),
                "service": sess.get("service"),
                "slot": sess.get("slot"),
                "start_time": int(sess.get("start_time", time.time())),
                "status": "active"
            })
            append_audit_log(actor=sess.get("uid") or "system", action="session_start", meta={"session_id": sid})
            return True

        if op in ("session_end", "water_session_end"):
            sess = payload.get("session") or {}
            sid = sess.get("session_id") or payload.get("session_id")
            if not sid:
                _LOGGER.error("session_end missing session_id")
                return True
            ref = _try_ref(f"/sessions/{sid}")
            updates = {
                "end_time": int(sess.get("end_time", time.time())),
                "status": sess.get("status", "completed")
            }
            ref.update(updates)
            append_audit_log(actor=sess.get("uid") or "system", action="session_end", meta={"session_id": sid})
            return True

        if op == "coin":
            sid = payload.get("session_id")
            val = int(payload.get("value", 0))
            # record coin event and attach to session if present
            coin_ref = _try_ref("/coins").push({
                "ts": int(time.time() * 1000),
                "session_id": sid,
                "value": val
            })
            # Update session credit if session exists
            if sid:
                sess_ref = _try_ref(f"/sessions/{sid}/coins")
                sess_ref.push({"ts": int(time.time() * 1000), "value": val})
            append_audit_log(actor=payload.get("actor", "system"), action="coin", meta={"session_id": sid, "value": val})
            return True

        if op in ("dispense_increment", "dispense_report"):
            sid = payload.get("session_id")
            ml = int(payload.get("ml", payload.get("amount", 0)))
            if not sid:
                _LOGGER.error("dispense_increment missing session_id")
                return True
            sess_ref = _try_ref(f"/sessions/{sid}")
            # increment dispensed_ml atomically using transaction pattern
            def txn_inc(current):
                if current is None:
                    current = {}
                disp = int(current.get("dispensed_ml", 0))
                disp += int(ml)
                current["dispensed_ml"] = disp
                current["last_dispense_ts"] = int(time.time() * 1000)
                return current
            try:
                sess_ref.transaction(lambda cur: txn_inc(cur))
            except Exception:
                # fallback: non-transactional update (best-effort)
                try:
                    current = sess_ref.get() or {}
                    disp = int(current.get("dispensed_ml", 0)) + int(ml)
                    sess_ref.update({"dispensed_ml": disp, "last_dispense_ts": int(time.time() * 1000)})
                except Exception:
                    _LOGGER.exception("Failed to update dispense_increment for session %s", sid)
                    return False
            append_audit_log(action="dispense_increment", meta={"session_id": sid, "ml": ml})
            return True

        if op in ("start_dispense", "stop_dispense"):
            sid = payload.get("session_id")
            target_ml = payload.get("target_ml")
            if not sid:
                _LOGGER.error("%s missing session_id", op)
                return True
            # append under session events
            ev_ref = _try_ref(f"/sessions/{sid}/events").push({
                "op": op,
                "ts": int(time.time() * 1000),
                "target_ml": target_ml
            })
            append_audit_log(action=op, meta={"session_id": sid, "target_ml": target_ml})
            return True

        # Slot-related ops from ChargingService
        if op.startswith("slot_"):
            slot = payload.get("slot")
            sid = payload.get("session_id")
            amps = payload.get("amps")
            if slot is None:
                _LOGGER.error("slot op requires 'slot' key: %s", payload)
                return True
            slot_ref = _try_ref(f"/slots/{slot}")
            # update last event and basic metadata
            slot_ref.update({
                "last_op": op,
                "last_ts": int(time.time() * 1000),
                "last_session": sid,
                "last_amps": amps
            })
            # append event
            slot_ref.child("events").push({
                "op": op,
                "ts": int(time.time() * 1000),
                "session_id": sid,
                "amps": amps
            })
            append_audit_log(action=op, meta={"slot": slot, "session_id": sid, "amps": amps})
            return True

        if op in ("water_session_summary",):
            sid = payload.get("session_id")
            summary = payload.get("summary")
            if not sid or not summary:
                _LOGGER.error("water_session_summary missing session_id/summary")
                return True
            # write summary under /session_summaries/{sid}
            _try_ref(f"/session_summaries/{sid}").set({
                "summary": summary,
                "ts": int(time.time() * 1000)
            })
            append_audit_log(action="water_session_summary", meta={"session_id": sid})
            return True

        # User balance operations
        if op == "deduct_user_charge_seconds":
            users_root = _try_ref("/users")
            uid = payload.get("uid")
            seconds = int(payload.get("seconds", 0))
            if not uid:
                _LOGGER.error("deduct_user_charge_seconds missing uid")
                return True
            new_bal = deduct_charge_balance_transactionally(users_root, uid, seconds)
            if new_bal is None:
                _LOGGER.warning("deduct_user_charge_seconds failed for uid=%s", uid)
                return False
            append_audit_log(action="deduct_charge", actor=uid, meta={"seconds": seconds, "new_balance": new_bal})
            return True

        # Default fallback: append raw payload to /events for audit/diagnostics
        try:
            _try_ref("/events").push({
                "op": op,
                "payload": payload,
                "ts": int(time.time() * 1000)
            })
            _LOGGER.debug("process_payload fallback stored op=%s", op)
            return True
        except Exception:
            _LOGGER.exception("Failed to store fallback event for op=%s", op)
            return False

    except Exception:
        _LOGGER.exception("process_payload raised exception for op=%s", op)
        return False
