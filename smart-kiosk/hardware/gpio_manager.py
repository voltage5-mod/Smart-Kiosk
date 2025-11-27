"""
hardware/gpio_manager.py
------------------------

Centralized GPIO + ADC + ACS712 Hardware Layer

This updated version fully integrates:
- MCP3008 ADC (SPI)
- ACS712Reader (your EXACT old current conversion behavior)
- Relay + Lock control
- TM1637 display support
- MockGPIOManager for testing

The read_acs() function now returns ONLY RMS amps, matching your
previous system 100% so plug/unplug thresholds behave identically.
"""

from __future__ import annotations
import logging
import threading
from typing import Any, Dict, Optional

# Conditional RPi import
try:
    import RPi.GPIO as GPIO  # type: ignore
    HAS_RPI = True
except Exception:
    GPIO = None  # type: ignore
    HAS_RPI = False

from config import PINMAP, SLOTS, RELAY_DEFAULTS, TM1637_MAP, ADC_MAP

# NEW imports for ADC integration
from hardware.adc_mcp3008 import MCP3008, ACS712Reader

_LOGGER = logging.getLogger("[GPIO_MANAGER]")

# Allow mock mode using environment variable
import os
FORCE_MOCK = os.getenv("SMART_KIOSK_HW_MOCK", "0") == "1"


class GPIOManager:
    """
    Real hardware manager that wraps:
    - GPIO relays/locks
    - TM1637 display drivers
    - MCP3008 ADC + ACS712 current readings

    IMPORTANT:
    read_acs(slot_id) returns EXACT SAME amps (RMS) as previous system.
    """

    def __init__(self, pinmap: Optional[Dict[str, Any]] = None):
        self._pinmap = pinmap or PINMAP
        self._initialized = False
        self._lock = threading.RLock()
        self._rpimode = HAS_RPI

        # NEW — ACS712 reader
        self._adc: Optional[ACS712Reader] = None

        # TM1637 displays
        self._tm_drivers: Dict[int, Any] = {}

    # -------------------------------------------------------------
    # INIT / CLEANUP
    # -------------------------------------------------------------
    def init(self, suppress_warnings: bool = True) -> None:
        with self._lock:
            if self._initialized:
                return

            if not self._rpimode:
                _LOGGER.warning("GPIOManager initialized in MOCK/NON-RPI mode")
                self._initialized = True
                self._init_adc()
                return

            GPIO.setmode(GPIO.BOARD)
            if suppress_warnings:
                GPIO.setwarnings(False)

            # RELAYS + LOCKS initialization
            slots = self._pinmap.get("slots", {})
            for sid, conf in slots.items():
                power = conf.get("power_relay")
                lock = conf.get("lock_relay")

                if power is not None:
                    GPIO.setup(int(power), GPIO.OUT, initial=GPIO.LOW)
                if lock is not None:
                    GPIO.setup(int(lock), GPIO.OUT, initial=GPIO.HIGH)  # locked default

            # Initialize ADC + ACS712
            self._init_adc()

            self._initialized = True
            _LOGGER.info("GPIOManager INIT complete")

    def cleanup(self) -> None:
        with self._lock:
            if not self._rpimode:
                self._initialized = False
                return
            try:
                GPIO.cleanup()
            finally:
                self._initialized = False
                _LOGGER.info("GPIOManager cleanup complete")

    # -------------------------------------------------------------
    # ADC + ACS712 SETUP
    # -------------------------------------------------------------
    def _init_adc(self):
        try:
            adc_chip = MCP3008()
            acs = ACS712Reader(PINMAP)
            acs.attach_adc(adc_chip)
            self._adc = acs
            _LOGGER.info("ACS712 + MCP3008 initialized successfully")
        except Exception:
            _LOGGER.exception("Failed to initialize ACS712/MCP3008")
            self._adc = None

    def attach_adc(self, adc_instance: ACS712Reader):
        """Override ADC reader (used for testing/mocking)."""
        self._adc = adc_instance
        _LOGGER.info("Custom ADC attached: %s", type(adc_instance).__name__)

    # -------------------------------------------------------------
    # RELAY CONTROL
    # -------------------------------------------------------------
    def set_relay_power(self, slot_id: int, on: bool) -> None:
        with self._lock:
            slot = self._slot(slot_id)
            pin = slot.get("power_relay")

            if pin is None:
                _LOGGER.error("No power_relay for slot %s", slot_id)
                return

            if not self._rpimode:
                _LOGGER.debug("MOCK: Power relay slot=%s on=%s", slot_id, on)
                return

            GPIO.output(int(pin), GPIO.HIGH if on else GPIO.LOW)
            _LOGGER.debug("Power relay slot=%s → %s", slot_id, "ON" if on else "OFF")

    def set_relay_lock(self, slot_id: int, locked: bool) -> None:
        with self._lock:
            slot = self._slot(slot_id)
            pin = slot.get("lock_relay")

            if pin is None:
                _LOGGER.error("No lock_relay for slot %s", slot_id)
                return

            if not self._rpimode:
                _LOGGER.debug("MOCK: Lock relay slot=%s locked=%s", slot_id, locked)
                return

            GPIO.output(int(pin), GPIO.HIGH if locked else GPIO.LOW)
            _LOGGER.debug("Lock relay slot=%s → %s", slot_id, "LOCKED" if locked else "UNLOCKED")

    # -------------------------------------------------------------
    # CURRENT READING (ACS712 + MCP3008)
    # -------------------------------------------------------------
    def read_acs(self, slot_id: int) -> float:
        """
        Returns EXACT RMS amps used by your original threshold code.
        (Identical behavior to your old system.)
        """
        with self._lock:
            if not self._adc:
                _LOGGER.error("read_acs() called with no ADC attached")
                return 0.0

            slot_key = str(slot_id)

            try:
                amps = self._adc.read_current(slot_key)
                _LOGGER.debug("read_acs: slot=%s → %.4f A", slot_key, amps)
                return float(amps)
            except Exception:
                _LOGGER.exception("ACS712 read failed for slot=%s", slot_key)
                return 0.0

    # -------------------------------------------------------------
    # TM1637 DISPLAY SUPPORT
    # -------------------------------------------------------------
    def attach_tm1637(self, tm_id: int, driver):
        self._tm_drivers[int(tm_id)] = driver
        _LOGGER.info("TM1637 driver attached for id %s", tm_id)

    def tm1637_set_time(self, tm_id: int, seconds: int):
        driver = self._tm_drivers.get(int(tm_id))
        if not driver:
            return

        mm = seconds // 60
        ss = seconds % 60
        display = f"{mm:02d}{ss:02d}"

        try:
            if hasattr(driver, "show"):
                driver.show(display)
            elif hasattr(driver, "write"):
                driver.write(display)
            _LOGGER.debug("TM1637[%s] → %s", tm_id, display)
        except Exception:
            _LOGGER.exception("Failed to update TM1637")

    # -------------------------------------------------------------
    # HELPERS
    # -------------------------------------------------------------
    def _slot(self, slot_id: int) -> Dict[str, Any]:
        return SLOTS.get(str(slot_id), {})

    def slot_ids(self):
        return list(SLOTS.keys())

    def __repr__(self):
        return f"<GPIOManager rpimode={self._rpimode} initialized={self._initialized}>"


# -------------------------------------------------------------
# MOCK HARDWARE MANAGER
# -------------------------------------------------------------
class MockGPIOManager(GPIOManager):
    def __init__(self, pinmap: Optional[Dict[str, Any]] = None):
        super().__init__(pinmap)
        self._states = {
            "power": {},
            "lock": {},
            "acs": {},
        }
        # Attach a dummy ADC reader
        mock_adc = ACS712Reader(PINMAP)
        self.attach_adc(mock_adc)

    def init(self, suppress_warnings: bool = True):
        self._initialized = True
        _LOGGER.info("MockGPIOManager initialized")

    def cleanup(self):
        self._initialized = False

    # Override for mock states
    def set_relay_power(self, slot_id: int, on: bool):
        self._states["power"][slot_id] = on

    def set_relay_lock(self, slot_id: int, locked: bool):
        self._states["lock"][slot_id] = locked

    def read_acs(self, slot_id: int):
        return float(self._states["acs"].get(slot_id, 0.0))

    def set_mock_current(self, slot_id: int, amps: float):
        self._states["acs"][slot_id] = float(amps)


# -------------------------------------------------------------
# SINGLETON FACTORY
# -------------------------------------------------------------
_manager_singleton: Optional[GPIOManager] = None


def get_gpio_manager() -> GPIOManager:
    global _manager_singleton

    if _manager_singleton:
        return _manager_singleton

    if FORCE_MOCK or not HAS_RPI:
        _LOGGER.warning("Using MockGPIOManager (FORCE_MOCK=%s, RPi=%s)", FORCE_MOCK, HAS_RPI)
        _manager_singleton = MockGPIOManager()
    else:
        _manager_singleton = GPIOManager()

    return _manager_singleton
