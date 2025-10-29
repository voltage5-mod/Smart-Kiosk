"""
Lightweight Firebase helper utilities for the kiosk project.
Provides:
 - append_audit_log(db_ref, actor, action, meta)
 - deduct_charge_balance_transactionally(users_ref, uid, seconds_to_deduct)

This file is safe to import into `FULL_UI.py` later and demonstrates small, low-risk improvements
(atomic balance deduction and an audit log for important actions).

Requires: firebase_admin initialized in the importing script.

"""
from firebase_admin import db
import time


def append_audit_log(root='/audit_log', actor='system', action='unknown', meta=None):
    """Append an audit record under `root` in the Realtime Database.

    Example record:
      audit_log/{ts}/{pushId} = { ts, actor, action, meta }
    """
    try:
        ts = int(time.time() * 1000)
        rec = {
            'ts': ts,
            'actor': actor,
            'action': action,
            'meta': meta or {}
        }
        ref = db.reference(root)
        ref.push(rec)
        return True
    except Exception as e:
        print('append_audit_log failed:', e)
        return False


def deduct_charge_balance_transactionally(users_root, uid, seconds_to_deduct):
    """Safely deduct seconds from users/<uid>/charge_balance using a transaction.

    Returns the new balance on success, or None on failure.
    """
    try:
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
            # update minimal fields
            current['charge_balance'] = new_balance
            current['last_balance_update_ts'] = int(time.time() * 1000)
            return current

        result = user_ref.transaction(txn)
        if result is None:
            return None
        return result.get('charge_balance')
    except Exception as e:
        print('deduct_charge_balance_transactionally failed:', e)
        return None
