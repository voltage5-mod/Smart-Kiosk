"""
utils/events.py

Canonical Event dataclass used across the Smart Kiosk system.

- Provides a deterministic id function used for deduplication.
- Provides (de)serialization helpers.
- Includes a small factory 'from_serial_parsed' to help turn parsed Arduino lines
  (dicts) into Event objects.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional
import time
import json
import hashlib

def now_ts() -> float:
    """Consistent timestamp provider (seconds since epoch)."""
    return time.time()

def make_event_id(name: str, args: Dict[str, Any], ts: Optional[float] = None) -> str:
    """
    Deterministic event id for idempotency.
    We include name + sorted args JSON + optional ts (if provided) as input.
    Returns a short hex (16 chars) from sha256.
    """
    payload = {"name": name, "args": args}
    if ts is not None:
        payload["ts"] = float(ts)
    # sort keys to ensure deterministic JSON
    j = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    h = hashlib.sha256(j.encode("utf-8")).hexdigest()
    return h[:16]

@dataclass
class Event:
    """
    Canonical event structure.

    Fields:
      - source: where the event came from (e.g., "arduino", "ui", "system", "hardware")
      - name: short event name (e.g., "COIN", "SLOT", "MODE")
      - args: dictionary of additional typed arguments
      - ts: float timestamp (seconds since epoch)
      - id: optional deterministic id (if not provided it's generated on demand)
    """
    source: str
    name: str
    args: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=now_ts)
    id: Optional[str] = field(default=None)

    def __post_init__(self):
        # Ensure args is a dict
        if self.args is None:
            self.args = {}
        # If id missing, generate one deterministically (without ts) so duplicates with same payload match
        if self.id is None:
            # include ts in id generation only if explicitly present and desired by caller;
            # here we omit ts so that identical name+args map to same id (helps dedupe).
            self.id = make_event_id(self.name, self.args)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to plain dict (safe for JSON)."""
        d = asdict(self)
        return d

    def to_json(self) -> str:
        """JSON string (compact, sorted keys)."""
        return json.dumps(self.to_dict(), sort_keys=True, default=str, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        """Construct Event from a dict (validates minimal fields)."""
        src = data.get("source", "unknown")
        name = data.get("name", "UNKNOWN")
        args = data.get("args", {}) or {}
        ts = data.get("ts", now_ts())
        eid = data.get("id")
        return cls(source=src, name=name, args=args, ts=float(ts), id=eid)

    @classmethod
    def from_json(cls, s: str) -> "Event":
        """Construct Event from a JSON string."""
        d = json.loads(s)
        return cls.from_dict(d)

    @classmethod
    def from_serial_parsed(cls, parsed: Dict[str, Any], default_source: str = "arduino") -> "Event":
        """
        Helper factory used by the Arduino parser.
        `parsed` is the dictionary produced from parsing a serial line:
          - If it contains keys 'event' or 'name', those will be used for name.
          - If single-key dict, use that key as name and its value as {"value": ...}.
          - If multiple keys, name -> "ARDUINO" and args -> parsed
        """
        if not isinstance(parsed, dict):
            # fallback: treat as single token
            return cls(source=default_source, name=str(parsed), args={})

        parsed = dict(parsed)  # shallow copy to avoid mutation
        if "event" in parsed:
            name = str(parsed.pop("event"))
            args = parsed
        elif "name" in parsed:
            name = str(parsed.pop("name"))
            args = parsed
        elif len(parsed) == 1:
            k, v = next(iter(parsed.items()))
            name = str(k)
            args = {"value": v}
        else:
            name = "ARDUINO"
            args = parsed

        return cls(source=default_source, name=name, args=args, ts=now_ts())

    def short(self) -> str:
        """Compact human-friendly one-line representation."""
        return f"[{self.source}] {self.name} {self.args} (id={self.id})"

# Backwards-compat helper used by older modules expecting make_event_id function
def make_id_for_event(ev: Event) -> str:
    return make_event_id(ev.name, ev.args, ev.ts)
