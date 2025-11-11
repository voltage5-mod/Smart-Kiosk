#!/usr/bin/env python3
"""
Comprehensive integration test for Kiosk system:
  - Charging subsystem: TM1637 displays, power/lock relays, ACS712 current sensors
  - Water subsystem: MCP23017 expander (pump, solenoid, flow sensor, tank levels)
  - Coin acceptor and ultrasonic sensor on Pi GPIO
"""

import time
import json
import os
import sys

# Load pinmap
BASE = os.path.dirname(__file__)
PINMAP_PATH = os.path.join(BASE, 'pinmap.json')
try:
    with open(PINMAP_PATH, 'r') as f:
        PINMAP = json.load(f)
except Exception as e:
    print(f"ERROR: Failed to load pinmap.json: {e}")
    sys.exit(1)

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("WARNING: RPi.GPIO not available; using simulation mode")
    GPIO = None

try:
    import board
    import busio
    import adafruit_mcp23017
    I2C_AVAILABLE = True
except ImportError:
    print("WARNING: Adafruit MCP23017 library not available; skipping I2C tests")
    I2C_AVAILABLE = False

try:
    from spidev import SpiDev
    SPI_AVAILABLE = True
except ImportError:
    print("WARNING: spidev not available; skipping ACS712 tests")
    SPI_AVAILABLE = False


class TestIntegration:
    def __init__(self):
        self.mode = "sim" if GPIO is None else "real"
        self.gpio = GPIO
        self.i2c = None
        self.mcp = None
        self.spi = None
        print(f"\n{'='*60}")
        print(f"KIOSK INTEGRATION TEST ({self.mode.upper()} mode)")
        print(f"{'='*60}\n")

    def setup_gpio(self):
        """Initialize Raspberry Pi GPIO."""
        if self.mode == "sim":
            print("[GPIO] Simulation mode; no GPIO setup needed")
            return
        try:
            self.gpio.setmode(self.gpio.BCM)
            self.gpio.setwarnings(False)
            print("[GPIO] GPIO initialized in BCM mode")
        except Exception as e:
            print(f"[GPIO ERROR] {e}")

    def setup_i2c_mcp23017(self):
        """Initialize MCP23017 expander on I2C bus."""
        if not I2C_AVAILABLE:
            print("[I2C] Adafruit MCP23017 library not available; skipping")
            return
        try:
            i2c = busio.I2C(board.SCL, board.SDA)
            self.mcp = adafruit_mcp23017.MCP23017(i2c, address=0x20)
            print("[I2C] MCP23017 initialized at address 0x20")
            # configure water subsystem pins
            mcp_config = PINMAP.get('mcp23017_expander', {})
            gpa_pins = mcp_config.get('gpa', {})
            gpb_pins = mcp_config.get('gpb', {})
            # setup GPA as inputs (flow, tank levels)
            for pin_name, pin_info in gpa_pins.items():
                if pin_info.get('direction') == 'input':
                    pin_num = int(pin_name[3:])  # GPA0 -> 0
                    self.mcp.get_pin(pin_num).direction = adafruit_mcp23017.Direction.INPUT
                    print(f"  [MCP] {pin_name} configured as INPUT")
            # setup GPB as outputs (pump, solenoid)
            for pin_name, pin_info in gpb_pins.items():
                if pin_info.get('direction') == 'output':
                    pin_num = int(pin_name[3:]) + 8  # GPB0 -> 8
                    self.mcp.get_pin(pin_num).direction = adafruit_mcp23017.Direction.OUTPUT
                    self.mcp.get_pin(pin_num).value = False
                    print(f"  [MCP] {pin_name} configured as OUTPUT")
        except Exception as e:
            print(f"[I2C ERROR] {e}")

    def setup_spi_adc(self):
        """Initialize SPI for MCP3008 ADC (ACS712 current sensors)."""
        if not SPI_AVAILABLE:
            print("[SPI] spidev not available; skipping ACS712 tests")
            return
        try:
            self.spi = SpiDev()
            self.spi.open(0, 0)
            self.spi.max_speed_hz = 1350000
            print("[SPI] MCP3008 ADC initialized (SPI bus 0, CE0)")
        except Exception as e:
            print(f"[SPI ERROR] {e}")

    def test_tm1637_displays(self):
        """Test TM1637 7-segment displays for all 4 slots."""
        print("\n[TM1637 TEST]")
        tm1637_map = PINMAP.get('raspberry_pi', {}).get('tm1637_displays', {})
        for slot_name, pins in tm1637_map.items():
            clk = pins.get('clk')
            dio = pins.get('dio')
            print(f"  {slot_name}: CLK={clk}, DIO={dio}")
            if self.mode == "real":
                try:
                    # setup pins as outputs
                    self.gpio.setup(clk, self.gpio.OUT)
                    self.gpio.setup(dio, self.gpio.OUT)
                    # brief pulse to test connectivity
                    self.gpio.output(clk, self.gpio.HIGH)
                    self.gpio.output(dio, self.gpio.HIGH)
                    time.sleep(0.1)
                    self.gpio.output(clk, self.gpio.LOW)
                    self.gpio.output(dio, self.gpio.LOW)
                    print(f"    [OK] {slot_name} GPIO pins respond")
                except Exception as e:
                    print(f"    [ERR] {slot_name} error: {e}")
            else:
                print(f"    [SIM] {slot_name} display test (no hardware)")

    def test_charging_relays(self):
        """Test power and lock relays for all 4 slots."""
        print("\n[RELAY TEST]")
        relay_map = PINMAP.get('raspberry_pi', {}).get('charging_relays', {})
        for relay_name, pin in relay_map.items():
            print(f"  {relay_name}: GPIO {pin}")
            if self.mode == "real":
                try:
                    self.gpio.setup(pin, self.gpio.OUT)
                    # pulse relay: ON 0.5s, OFF 0.5s
                    self.gpio.output(pin, self.gpio.HIGH)
                    time.sleep(0.5)
                    self.gpio.output(pin, self.gpio.LOW)
                    time.sleep(0.5)
                    print(f"    [OK] {relay_name} relay pulsed")
                except Exception as e:
                    print(f"    [ERR] {relay_name} error: {e}")
            else:
                print(f"    [SIM] {relay_name} relay pulse (no hardware)")

    def test_acs712_current_sensors(self):
        """Test ACS712 current sensors via MCP3008 ADC."""
        print("\n[ACS712 CURRENT SENSOR TEST]")
        if not self.spi:
            print("  [SPI] SPI/ADC not available; skipping")
            return
        
        acs_channels = PINMAP.get('raspberry_pi', {}).get('acs712_adc_channels', {})
        for slot_name, channel in acs_channels.items():
            print(f"  {slot_name}: ADC channel {channel}")
            try:
                # read ADC raw value
                msg = [0x01, (0x08 + channel) << 4, 0x00]
                reply = self.spi.xfer2(msg)
                adc_raw = ((reply[1] & 0x03) << 8) + reply[2]
                # convert to voltage (10-bit, ref 3.3V)
                adc_volts = (adc_raw / 1023.0) * 3.3
                # estimate amps (ACS712 5A: 185 mV/A, 2.5V @ 0A)
                amps = (adc_volts - 2.5) / 0.185
                print(f"    ADC={adc_raw:3d}  Volts={adc_volts:.2f}V  Amps≈{amps:.2f}A")
            except Exception as e:
                print(f"    [ERR] {slot_name} error: {e}")

    def test_coin_acceptor(self):
        """Test coin acceptor pulse input on Pi GPIO."""
        print("\n[COIN ACCEPTOR TEST]")
        coin_pin = PINMAP.get('raspberry_pi', {}).get('coin_acceptor', {}).get('signal')
        if not coin_pin:
            print("  [CONFIG] Coin acceptor pin not found in pinmap")
            return
        print(f"  Coin signal pin: GPIO {coin_pin}")
        if self.mode == "real":
            try:
                self.gpio.setup(coin_pin, self.gpio.IN, pull_up_down=self.gpio.PUD_DOWN)
                print(f"    [OK] GPIO {coin_pin} configured as INPUT (pulled DOWN)")
                print(f"    Monitor this pin for coin pulses (normally LOW, HIGH on coin)")
            except Exception as e:
                print(f"    [ERR] Error: {e}")
        else:
            print(f"    [SIM] Coin acceptor test (no hardware)")

    def test_ultrasonic_sensor(self):
        """Test ultrasonic distance sensor (trig/echo)."""
        print("\n[ULTRASONIC SENSOR TEST]")
        ultrasonic = PINMAP.get('raspberry_pi', {}).get('ultrasonic_sensor', {})
        trig_pin = ultrasonic.get('trig')
        echo_pin = ultrasonic.get('echo')
        if not trig_pin or not echo_pin:
            print("  [CONFIG] Ultrasonic pins not found in pinmap")
            return
        print(f"  Trigger pin: GPIO {trig_pin}, Echo pin: GPIO {echo_pin}")
        if self.mode == "real":
            try:
                self.gpio.setup(trig_pin, self.gpio.OUT)
                self.gpio.setup(echo_pin, self.gpio.IN)
                # trigger a pulse
                self.gpio.output(trig_pin, self.gpio.LOW)
                time.sleep(0.00001)
                self.gpio.output(trig_pin, self.gpio.HIGH)
                time.sleep(0.00001)
                self.gpio.output(trig_pin, self.gpio.LOW)
                # measure echo pulse width (timeout 1s)
                timeout = time.time() + 1.0
                pulse_start = None
                while time.time() < timeout:
                    if self.gpio.input(echo_pin) == self.gpio.HIGH:
                        pulse_start = time.time()
                        break
                if pulse_start:
                    while time.time() < timeout:
                        if self.gpio.input(echo_pin) == self.gpio.LOW:
                            pulse_end = time.time()
                            pulse_width = pulse_end - pulse_start
                            # distance in cm = (pulse_width * 34300) / 2
                            distance = (pulse_width * 34300) / 2
                            print(f"    [OK] Ultrasonic pulse width: {pulse_width*1e6:.0f} µs")
                            print(f"    Estimated distance: {distance:.1f} cm")
                            break
                else:
                    print(f"    [ERR] No echo pulse received (sensor may not be connected)")
            except Exception as e:
                print(f"    [ERR] Error: {e}")
        else:
            print(f"    [SIM] Ultrasonic sensor test (no hardware)")

    def test_mcp23017_water_outputs(self):
        """Test MCP23017 water subsystem outputs (pump, solenoid)."""
        print("\n[MCP23017 WATER OUTPUTS TEST]")
        if not self.mcp:
            print("  [I2C] MCP23017 not available; skipping")
            return
        mcp_config = PINMAP.get('mcp23017_expander', {})
        gpb_pins = mcp_config.get('gpb', {})
        for pin_name, pin_info in gpb_pins.items():
            if pin_info.get('direction') == 'output':
                func = pin_info.get('function')
                pin_num = int(pin_name[3:]) + 8  # GPB0 -> 8
                print(f"  {pin_name} ({func}): pin {pin_num}")
                try:
                    pin = self.mcp.get_pin(pin_num)
                    # pulse the relay
                    pin.value = True
                    time.sleep(0.5)
                    pin.value = False
                    print(f"    [OK] {pin_name} pulsed (0.5s ON, then OFF)")
                except Exception as e:
                    print(f"    [ERR] {pin_name} error: {e}")

    def test_mcp23017_water_inputs(self):
        """Test MCP23017 water subsystem inputs (flow sensor, tank levels)."""
        print("\n[MCP23017 WATER INPUTS TEST]")
        if not self.mcp:
            print("  [I2C] MCP23017 not available; skipping")
            return
        mcp_config = PINMAP.get('mcp23017_expander', {})
        gpa_pins = mcp_config.get('gpa', {})
        print("  Reading input pins (flow, tank levels)...")
        for pin_name, pin_info in gpa_pins.items():
            if pin_info.get('direction') == 'input':
                func = pin_info.get('function')
                pin_num = int(pin_name[3:])  # GPA0 -> 0
                try:
                    pin = self.mcp.get_pin(pin_num)
                    state = pin.value
                    print(f"    {pin_name} ({func}): {state} (HIGH={state}, LOW=not {state})")
                except Exception as e:
                    print(f"    [ERR] {pin_name} error: {e}")

    def cleanup(self):
        """Cleanup GPIO and SPI."""
        print("\n[CLEANUP]")
        if self.spi:
            try:
                self.spi.close()
                print("  [SPI] Closed")
            except Exception:
                pass
        if self.mode == "real" and self.gpio:
            try:
                self.gpio.cleanup()
                print("  [GPIO] Cleaned up")
            except Exception:
                pass
        print("\n[INTEGRATION TEST COMPLETE]\n")


def main():
    tester = TestIntegration()
    
    try:
        # Setup
        tester.setup_gpio()
        tester.setup_i2c_mcp23017()
        tester.setup_spi_adc()
        
        # Run tests
        tester.test_tm1637_displays()
        tester.test_charging_relays()
        tester.test_acs712_current_sensors()
        tester.test_coin_acceptor()
        tester.test_ultrasonic_sensor()
        tester.test_mcp23017_water_outputs()
        tester.test_mcp23017_water_inputs()
        
    except KeyboardInterrupt:
        print("\n[INTERRUPT] User stopped test")
    except Exception as e:
        print(f"\n[ERROR] Unexpected exception: {e}")
        import traceback
        traceback.print_exc()
    finally:
        tester.cleanup()


if __name__ == '__main__':
    main()
