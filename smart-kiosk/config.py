# config.py
"""
Load and validate pinmap.json and provide global config constants.
Place this file in the project root (same level as main.py and pinmap.json).
"""

from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_PINMAP_FILENAME = "pinmap.json"
ENV_PINMAP_PATH = "SMART_KIOSK_PINMAP"

class ConfigError(Exception):
    pass

def _load_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"Pinmap file not found at {path!s}") from e
    except json.JSONDecodeError as e:
        raise ConfigError(f"Pinmap file {path!s} is not valid JSON: {e}") from e

def validate_pinmap(pinmap: Dict[str, Any]) -> None:
    """
    Minimal validation for pinmap.json expected structure.
    Expand this as the pinmap schema grows.
    Expected top-level keys:
      - slots: dict mapping slot_id (string or int) -> slot config
      - relays: dict with 'power' and 'lock' subkeys (optional)
      - tm1637: list/dict of tm1637 instances (optional)
      - adc: mapping for ADC channels (optional)
    Raise ConfigError on validation failure.
    """
    if not isinstance(pinmap, dict):
        raise ConfigError("pinmap.json root must be an object")

    # slots
    if "slots" not in pinmap:
        raise ConfigError("pinmap.json missing required top-level key: 'slots'")

    if not isinstance(pinmap["slots"], dict):
        raise ConfigError("'slots' must be an object mapping slot ids to config")

    for slot_id, slot in pinmap["slots"].items():
        if not isinstance(slot, dict):
            raise ConfigError(f"slot '{slot_id}' must be an object")
        # recommended keys per slot: power_relay, lock_relay, acs_channel, tm1637_id (optional)
        recommended = ("power_relay", "lock_relay", "acs_channel")
        for rk in recommended:
            if rk not in slot:
                raise ConfigError(f"slot '{slot_id}' missing recommended key '{rk}'")

    # relays object (optional)
    if "relays" in pinmap and not isinstance(pinmap["relays"], dict):
        raise ConfigError("'relays' must be an object if present")

    # tm1637 may be list or dict
    if "tm1637" in pinmap and not isinstance(pinmap["tm1637"], (dict, list)):
        raise ConfigError("'tm1637' must be an object or array if present")

    # adc mapping optional
    if "adc" in pinmap and not isinstance(pinmap["adc"], dict):
        raise ConfigError("'adc' must be an object if present")

def find_pinmap_path() -> Path:
    """
    Determine path to pinmap.json, using environment override or default location.
    """
    env = os.getenv(ENV_PINMAP_PATH)
    if env:
        return Path(env).expanduser().resolve()
    # assume pinmap.json at project root (same dir as this file)
    candidate = Path(__file__).resolve().parent / DEFAULT_PINMAP_FILENAME
    return candidate

# Load on import (fail-fast)
try:
    PINMAP_PATH = find_pinmap_path()
    PINMAP: Dict[str, Any] = _load_json(PINMAP_PATH)
    validate_pinmap(PINMAP)
except Exception as e:
    # re-raise as ConfigError for clearer catch by callers
    raise ConfigError(f"Failed loading pinmap: {e}") from e

# Convenience helpers / constants
SLOTS = PINMAP.get("slots", {})
RELAY_DEFAULTS = PINMAP.get("relays", {})
TM1637_MAP = PINMAP.get("tm1637", {})
ADC_MAP = PINMAP.get("adc", {})

def get_slot_config(slot_id: str) -> Optional[Dict[str, Any]]:
    return SLOTS.get(str(slot_id)) or SLOTS.get(int(slot_id)) if isinstance(slot_id, (int,)) else SLOTS.get(str(slot_id))

def reload_pinmap(path: Optional[str] = None) -> None:
    """
    Reload pinmap at runtime (useful for tests). Path can be provided as override.
    """
    global PINMAP, SLOTS, RELAY_DEFAULTS, TM1637_MAP, ADC_MAP, PINMAP_PATH
    path_p = Path(path).expanduser().resolve() if path else find_pinmap_path()
    PINMAP_PATH = path_p
    PINMAP = _load_json(PINMAP_PATH)
    validate_pinmap(PINMAP)
    SLOTS = PINMAP.get("slots", {})
    RELAY_DEFAULTS = PINMAP.get("relays", {})
    TM1637_MAP = PINMAP.get("tm1637", {})
    ADC_MAP = PINMAP.get("adc", {})

# Lightweight debug print if running standalone
if __name__ == "__main__":
    print("Loaded pinmap from:", PINMAP_PATH)
    print("Slots:", list(SLOTS.keys()))
