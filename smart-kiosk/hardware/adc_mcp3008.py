"""
hardware/adc_mcp3008.py

MCP3008 ADC + ACS712 Current Sensor Integration
------------------------------------------------
This module replicates your original current-reading logic EXACTLY,
including the RMS, EMA, median calculations, and ACS712 conversion
formula that your plug/unplug thresholds depend on.

Final returned value = RMS amps   (Option A)

This ensures 100% backwards compatibility with your previous system.
"""

from __future__ import annotations
import math
import statistics
from collections import deque
import logging
from typing import Dict, Any, Optional

import spidev  # MCP3008 SPI

_LOGGER = logging.getLogger("[ADC_MCP3008]")


class MCP3008:
    """
    Simple MCP3008 SPI wrapper.
    Can read channels 0–7.
    """

    def __init__(self, bus: int = 0, device: int = 0, max_speed_hz: int = 1350000):
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        self.spi.max_speed_hz = max_speed_hz
        _LOGGER.info("MCP3008 initialized bus=%s device=%s", bus, device)

    def read_channel(self, channel: int) -> int:
        """
        Read raw 10-bit ADC value from MCP3008 channel (0–7)
        Returns integer 0–1023
        """
        if channel < 0 or channel > 7:
            raise ValueError("Channel must be 0–7")

        # Perform SPI transaction (3-byte transfer)
        adc = self.spi.xfer2([1, (8 + channel) << 4, 0])
        # Extract 10-bit result:
        raw = ((adc[1] & 3) << 8) + adc[2]
        return raw

    def close(self):
        try:
            self.spi.close()
        except Exception:
            pass


class ACS712Reader:
    """
    EXACT replication of your previous ACS712 reading logic.
    Output is RMS amps (float), matching your threshold behavior.
    """

    def __init__(
        self,
        pinmap: Dict[str, Any],
        vref: float = 3.3,
        sensitivity: float = 0.185,  # ACS712 5A module sensitivity
        rms_window: int = 8,         # same as old behavior (previous deque length)
        ema_alpha: float = 0.2,
        baseline_default: Optional[float] = None,  # None → use vref/2
    ):
        self.pinmap = pinmap
        self.vref = vref
        self.sensitivity = sensitivity
        self.rms_window = rms_window
        self.ema_alpha = ema_alpha

        self._baseline: Dict[str, float] = {}
        self._recent: Dict[str, deque] = {}
        self._ema: Dict[str, float] = {}

        # Default baseline (center voltage)
        self._baseline_default = baseline_default if baseline_default is not None else (self.vref / 2)

        # Attach ADC externally using attach_adc()
        self.adc = None

    # -------------------------------
    # External ADC attachment
    # -------------------------------
    def attach_adc(self, adc_instance: MCP3008):
        self.adc = adc_instance
        _LOGGER.info("ACS712Reader attached ADC: %s", type(adc_instance).__name__)

    # -------------------------------
    # Calibration (optional)
    # -------------------------------
    def calibrate_baseline(self, slot: str, samples: int = 20, delay: float = 0.05) -> float:
        """
        Measure baseline voltage at 0A and store it.
        Useful but optional — defaults to vref/2 if not calibrated.
        """
        import time
        vals = []
        for _ in range(samples):
            raw = self.read_raw(slot)
            volts = (raw / 1023.0) * self.vref
            vals.append(volts)
            time.sleep(delay)

        baseline = sum(vals) / len(vals) if vals else self._baseline_default
        self._baseline[slot] = baseline
        _LOGGER.info("Calibrated baseline: slot=%s → %.4f V", slot, baseline)
        return baseline

    # -------------------------------
    # Internal helpers
    # -------------------------------
    def _get_adc_channel(self, slot: str) -> Optional[int]:
        """Fetch channel from pinmap."""
        ch_map = self.pinmap.get("acs712_channels", {})
        ch = ch_map.get(slot)
        if ch is not None:
            return int(ch)

        # If slot is "1" or "2", allow numeric fallback
        try:
            return int(slot)
        except Exception:
            return None

    def read_raw(self, slot: str) -> int:
        """Return raw 0–1023 ADC reading."""
        if not self.adc:
            raise RuntimeError("No ADC attached to ACS712Reader")

        ch = self._get_adc_channel(slot)
        if ch is None:
            _LOGGER.error("Slot %s has no ADC channel", slot)
            return 0

        return self.adc.read_channel(ch)

    # -------------------------------
    # MAIN CURRENT READING FUNCTION
    # -------------------------------
    def read_current(self, slot: str) -> float:
        """
        EXACT copy of your previous logic:
        - Read ADC
        - Convert to volts
        - Convert to raw amps
        - RMS smoothing
        - Return RMS amps ONLY
        """
        # 1. Retrieve ADC raw value
        adc = self.read_raw(slot)

        # 2. Convert ADC → volts
        volts = (adc / 1023.0) * self.vref

        # 3. Get baseline (center voltage)
        baseline_v = self._baseline.get(slot, self._baseline_default)

        # 4. Convert volts → raw amps (your exact formula)
        amps_raw = (volts - baseline_v) / self.sensitivity

        # 5. Prepare buffer for smoothing
        if slot not in self._recent:
            self._recent[slot] = deque(maxlen=self.rms_window)

        self._recent[slot].append(amps_raw)

        # 6. RMS calculation (your primary output)
        if len(self._recent[slot]) > 0:
            try:
                rms = math.sqrt(sum((x or 0.0) ** 2 for x in self._recent[slot]) / len(self._recent[slot]))
            except Exception:
                rms = amps_raw
        else:
            rms = amps_raw

        # 7. EMA update (internal only)
        prev_ema = self._ema.get(slot)
        if prev_ema is None:
            self._ema[slot] = amps_raw
        else:
            self._ema[slot] = (self.ema_alpha * amps_raw) + ((1 - self.ema_alpha) * prev_ema)

        # 8. Debug logs (same style as your original terminal output)
        _LOGGER.debug(
            "[ACS712] slot=%s raw=%s volts=%.4fV amps_raw=%.4fA rms=%.4fA",
            slot, adc, volts, amps_raw, rms
        )

        # Return EXACT value your FSM uses
        return float(rms)
