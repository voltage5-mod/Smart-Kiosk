"""
ACS712 Current Sensor Helper
Compatible with MCP3008 + GPIO Manager
Preserves OLD behavior + NEW smoothing (RMS/median/EMA)
"""

import time
import math
import statistics
from collections import deque


class ACS712:
    """
    Handles:
      - ADC sampling
      - Zero calibration
      - RMS smoothing
      - Backwards-compatible reading format
    """

    def __init__(self, adc_reader, vref=3.3, sensitivity=0.185, rms_window=10, ema_alpha=0.2):
        """
        adc_reader: function(channel) -> raw ADC value (0–1023)
        """
        self.adc_reader = adc_reader
        self.vref = vref
        self.sensitivity = sensitivity
        self._baseline = {}          # per-slot zero current volts
        self._recent = {}            # sliding window of recent amps
        self._ema = {}               # exponential moving averages
        self._rms_window = rms_window
        self._ema_alpha = ema_alpha

    # ---------------------------------------------------------
    # ZERO CALIBRATION
    # ---------------------------------------------------------
    def calibrate_zero(self, slot, samples=30, delay=0.05):
        """Measure baseline / noise floor with nothing connected."""
        vals = []
        for _ in range(samples):
            adc = self.adc_reader(slot)
            volts = (adc / 1023.0) * self.vref
            vals.append(volts)
            time.sleep(delay)

        baseline = sum(vals) / len(vals)
        self._baseline[slot] = baseline
        return {"slot": slot, "baseline": baseline}

    # ---------------------------------------------------------
    # MAIN READ FUNCTION (Backwards compatible)
    # ---------------------------------------------------------
    def read(self, slot):
        """
        Returns:
        {
          'raw': ADC,
          'volts': V,
          'amps': RMS (for backward compatibility),
          'amps_raw': instant current,
          'amps_ema': smoothed,
          'amps_med': median
        }
        """
        adc = self.adc_reader(slot)
        volts = (adc / 1023.0) * self.vref

        baseline = self._baseline.get(slot, self.vref / 2)
        amps_raw = (volts - baseline) / self.sensitivity

        # Sliding buffer
        if slot not in self._recent:
            self._recent[slot] = deque(maxlen=self._rms_window)
        self._recent[slot].append(amps_raw)

        # RMS + Median smoothing
        rms = math.sqrt(sum(a*a for a in self._recent[slot]) / len(self._recent[slot]))
        try:
            med = statistics.median(self._recent[slot])
        except:
            med = rms

        # EMA smoothing
        prev = self._ema.get(slot)
        ema = amps_raw if prev is None else (self._ema_alpha * amps_raw + (1 - self._ema_alpha) * prev)
        self._ema[slot] = ema

        return {
            "raw": adc,
            "volts": volts,
            "amps": rms,       # ensures OLD code keeps working
            "amps_raw": amps_raw,
            "amps_ema": ema,
            "amps_med": med,
        }
