"""
services/billing_service.py

Billing service for Smart Kiosk.

Responsibilities:
- Convert coin events into credit for water sessions (ml) and record them.
- For charging, deduct user balance transactionally (via firebase_helpers.deduct_charge_balance_transactionally)
  when needed (e.g., on start of charging or periodically during charging).
- Provide helper APIs that SessionManager and Services can call:
    - handle_coin_event(event)         # coin credited to session
    - charge_user_seconds(uid, secs)   # attempt to deduct user balance
    - record_payment(...)              # record external/top-up payments
- Enqueue DB payloads into db_queue for async persistence via FirebaseWorker.
- Use an injected firebase_helpers module (so we can mock or use the real db wrapper).

Design notes:
- This service does NOT directly write to Firebase; it enqueues records onto db_queue and uses
  firebase_helpers for transactional balance deduction.
- Returns True/False for operations that need immediate success/failure semantics (e.g., deduct).
"""

from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional, Callable
import queue

from utils.events import Event

_LOGGER = logging.getLogger("[BILLING_SERVICE]")


@dataclass
class BillingConfig:
    ml_per_coin: int = 500  # default ml credited per coin for water
    # For charging: seconds_per_unit, price model is external; we operate on seconds deduction
    default_seconds_per_coin: Optional[int] = None  # if you use coin->seconds mapping


class BillingService:
    """
    BillingService is instantiated with:
      - db_queue: queue.Queue used to enqueue DB payloads (required)
      - firebase_helpers_module: module or object exposing:
            - deduct_charge_balance_transactionally(users_root, uid, seconds_to_deduct) -> new_balance|None
            - append_audit_log(...)  (optional, used for audit)
        If firebase_helpers_module is None, transactional deduction will be disabled (returns False).
      - users_root_ref: firebase db.Reference for '/users' or similar (only required if using deduct fn)
      - config: BillingConfig
      - time_fn: injectable time function for tests
    """

    def __init__(
        self,
        db_queue: "queue.Queue[Dict[str, Any]]",
        firebase_helpers_module: Optional[Any] = None,
        users_root_ref: Optional[Any] = None,
        config: Optional[BillingConfig] = None,
        time_fn: Callable[[], float] = time.time,
    ):
        if not isinstance(db_queue, queue.Queue):
            raise ValueError("db_queue must be a queue.Queue instance")
        self.db_queue = db_queue
        self.fh = firebase_helpers_module
        self.users_root_ref = users_root_ref
        self.config = config or BillingConfig()
        self.time = time_fn

    # ------------------------
    # Public API for SessionManager / Services
    # ------------------------
    def handle_coin_event(self, ev: Event) -> bool:
        """
        Process a COIN event that either contains session_id or uid.
        - For water sessions: credit ml to the session (enqueue coin payload).
        - For user-account payments: if event has uid and indicates top-up, record accordingly.

        Expected fields in ev.args:
          - value (int) or amount
          - session_id (optional) -> credit that session
          - uid (optional) -> credit user account or treat as payment actor
          - actor (optional) -> who performed action for audit

        Returns True if processed OK (enqueued / credited), False otherwise.
        """
        args = ev.args or {}
        try:
            val = args.get("value") or args.get("amount") or args.get("val")
            if val is None:
                _LOGGER.warning("handle_coin_event: missing value in event %s", ev)
                return False
            try:
                coin_val = int(val)
            except Exception:
                _LOGGER.warning("handle_coin_event: non-integer coin value %r", val)
                return False

            sid = args.get("session_id")
            uid = args.get("uid")
            actor = args.get("actor") or "system"

            # Water credit (session-bound)
            if sid:
                # Enqueue a coin payload for DB worker (SessionManager also handles local state)
                payload = {"op": "coin", "session_id": sid, "value": coin_val, "actor": actor}
                self._enqueue_db(payload)
                _LOGGER.info("Billing: credited session %s with coin=%s (actor=%s)", sid, coin_val, actor)
                return True

            # If belongs to a user (account top-up / virtual credits)
            if uid:
                # We don't implement a full wallet top-up here; enqueue an audit/payment event
                payload = {"op": "user_payment", "uid": uid, "value": coin_val, "actor": actor}
                self._enqueue_db(payload)
                _LOGGER.info("Billing: recorded user payment uid=%s value=%s", uid, coin_val)
                return True

            # No session_id or uid: enqueue global coin record
            payload = {"op": "coin", "session_id": None, "value": coin_val, "actor": actor}
            self._enqueue_db(payload)
            _LOGGER.info("Billing: recorded global coin=%s", coin_val)
            return True

        except Exception:
            _LOGGER.exception("handle_coin_event failed for event %s", ev)
            return False

    def charge_user_seconds(self, uid: str, seconds: int, idempotency_id: Optional[str] = None) -> bool:
        """
        Attempt to deduct `seconds` from user's charge balance transactionally.
        Uses firebase_helpers_module.deduct_charge_balance_transactionally(users_root_ref, uid, seconds)

        Returns True on success (deduction applied), False on transient failure.
        If firebase_helpers_module is not provided, returns False.
        If idempotency_id provided, include it in audit/log payload.
        """
        if not self.fh or not hasattr(self.fh, "deduct_charge_balance_transactionally") or not self.users_root_ref:
            _LOGGER.warning("charge_user_seconds: firebase_helpers not available; cannot deduct")
            return False

        try:
            new_bal = self.fh.deduct_charge_balance_transactionally(self.users_root_ref, uid, int(seconds))
            if new_bal is None:
                _LOGGER.warning("charge_user_seconds: deduction failed (insufficient or error) for uid=%s", uid)
                return False

            # Record audit and enqueue DB event for bookkeeping
            audit_meta = {"uid": uid, "seconds_deducted": int(seconds), "new_balance": new_bal}
            if idempotency_id:
                audit_meta["id"] = idempotency_id
            # prefer using fh.append_audit_log if available
            try:
                if hasattr(self.fh, "append_audit_log"):
                    self.fh.append_audit_log(actor=uid, action="deduct_charge", meta=audit_meta)
                else:
                    # fallback: enqueue as DB record for sessions/events
                    self._enqueue_db({"op": "deduct_charge", "uid": uid, "seconds": int(seconds), "new_balance": new_bal, "id": idempotency_id})
            except Exception:
                _LOGGER.exception("Failed to record audit for charge deduction uid=%s", uid)

            _LOGGER.info("charge_user_seconds: deducted %s seconds from uid=%s (new_balance=%s)", seconds, uid, new_bal)
            return True
        except Exception:
            _LOGGER.exception("charge_user_seconds: exception while deducting uid=%s", uid)
            return False

    def record_payment(self, uid: str, amount: int, method: Optional[str] = None) -> bool:
        """
        Record a user payment/top-up; enqueues a DB payload and optionally call append_audit_log.
        """
        try:
            payload = {"op": "user_payment", "uid": uid, "amount": int(amount), "method": method, "ts": int(self.time() * 1000)}
            self._enqueue_db(payload)
            if self.fh and hasattr(self.fh, "append_audit_log"):
                try:
                    self.fh.append_audit_log(actor=uid, action="user_payment", meta={"amount": amount, "method": method})
                except Exception:
                    _LOGGER.exception("append_audit_log failed for user_payment uid=%s", uid)
            _LOGGER.info("record_payment: recorded payment uid=%s amount=%s", uid, amount)
            return True
        except Exception:
            _LOGGER.exception("record_payment failed for uid=%s", uid)
            return False

    # ------------------------
    # Helpers
    # ------------------------
    def _enqueue_db(self, payload: Dict[str, Any]) -> None:
        """Enqueue a payload for FirebaseWorker (non-blocking)."""
        try:
            self.db_queue.put_nowait(payload)
        except queue.Full:
            _LOGGER.warning("db_queue full; dropping billing payload %s", payload)

