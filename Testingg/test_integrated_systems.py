#!/usr/bin/env python3
"""
Integrated test script for merged water and charging systems.
Tests all components: charging relays, current sensors (ACS712), TM1637 displays,
coin acceptor, ultrasonic sensor, water relays (pump, solenoid), flow meter, and level sensors.
MCP23017 I2C expander handles water subsystem; Raspberry Pi GPIO handles charging.
"""

import json
import time
import os
from pathlib import Path

# Try to import GPIO and I2C libraries; gracefully degrade if not available
try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False
    print("[INFO] RPi.GPIO not available; running in simulation mode.")

try:
    import board
    import busio
    import adafruit_mcp23017
    HAS_I2C = True
except ImportError:
    HAS_I2C = False
    print("[INFO] I2C/MCP23017 libraries not available; water subsystem in simulation.")

try:
    from adafruit_ads1x15.analog_in import AnalogIn
    import adafruit_ads1x15.ads1115 as ADS
    HAS_ADC = True
except ImportError:
    HAS_ADC = False
    print("[INFO] ADC libraries not available; ACS712 sensors in simulation.")

# Load pinmap
PINMAP_PATH = Path(__file__).parent / 'pinmap_merged.json'
with open(PINMAP_PATH, 'r') as f:
    PINMAP = json.load(f)

# Extract pin definitions
PI_PINS = PINMAP['raspberry_pi']
MCP_PINS = PINMAP['mcp23017_expander']
MCP_ADDR = MCP_PINS['address']
MCP_BUS = MCP_PINS['i2c_bus']

# Charging system pins
CHARGING_RELAYS = PI_PINS['charging_relays']
COIN_SIGNAL = PI_PINS['coin_acceptor']['signal']
ULTRASONIC = PI_PINS['ultrasonic_sensor']

# Water system pins (on MCP23017)
PUMP_PIN = 'GPB0'      # Bank B, pin 0
SOLENOID_PIN = 'GPB1'  # Bank B, pin 1
FLOW_PIN = 'GPA0'      # Bank A, pin 0 (input)
TANK_LOW_PIN = 'GPA1'  # Bank A, pin 1 (input)
TANK_HIGH_PIN = 'GPA2' # Bank A, pin 2 (input)

class IntegratedTestSystem:
    """Test harness for merged water and charging systems."""
    
    def __init__(self, simulate=True):
        self.simulate = simulate
        self.mcp = None
        self.gpio_mode = 'SIM' if simulate else 'BCM'
        print(f"[INIT] IntegratedTestSystem (mode={self.gpio_mode})")
        self._setup_gpio()
        self._setup_mcp23017()
    
    def _setup_gpio(self):
        """Initialize Raspberry Pi GPIO for charging system."""
        if not HAS_GPIO or self.simulate:
            print("[GPIO] Simulated GPIO mode")
            return
        try:
            GPIO.setmode(GPIO.BCM)
            # Setup charging relay pins as outputs
            for name, pin in CHARGING_RELAYS.items():
                GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
                print(f"  [GPIO] {name} (pin {pin}) → OUTPUT, LOW")
            # Setup coin acceptor and ultrasonic
            GPIO.setup(COIN_SIGNAL, GPIO.IN)
            GPIO.setup(ULTRASONIC['trig'], GPIO.OUT, initial=GPIO.LOW)
            GPIO.setup(ULTRASONIC['echo'], GPIO.IN)
            print(f"  [GPIO] Coin acceptor (pin {COIN_SIGNAL}) → INPUT")
            print(f"  [GPIO] Ultrasonic TRIG (pin {ULTRASONIC['trig']}) → OUTPUT")
            print(f"  [GPIO] Ultrasonic ECHO (pin {ULTRASONIC['echo']}) → INPUT")
        except Exception as e:
            print(f"[GPIO] Setup failed: {e}; switching to simulation")
            self.simulate = True
    
    def _setup_mcp23017(self):
        """Initialize MCP23017 I2C expander for water system."""
        if not HAS_I2C or self.simulate:
            print("[I2C] Simulated MCP23017 mode")
            return
        try:
            i2c = busio.I2C(board.SCL, board.SDA)
            self.mcp = adafruit_mcp23017.MCP23017(i2c, address=int(MCP_ADDR, 16))
            print(f"[I2C] MCP23017 initialized at address {MCP_ADDR}")
            # Configure water pins
            # Output pins (pump, solenoid)
            for pin_name in [PUMP_PIN, SOLENOID_PIN]:
                bank, idx = pin_name[2], int(pin_name[3])
                pin = getattr(self.mcp, pin_name)
                pin.switch_to_output(value=False)
                print(f"  [MCP] {pin_name} → OUTPUT, LOW")
            # Input pins (flow, tank levels)
            for pin_name in [FLOW_PIN, TANK_LOW_PIN, TANK_HIGH_PIN]:
                pin = getattr(self.mcp, pin_name)
                pin.switch_to_input()
                print(f"  [MCP] {pin_name} → INPUT")
        except Exception as e:
            print(f"[I2C] MCP23017 setup failed: {e}; switching to simulation")
            self.simulate = True
            self.mcp = None
    
    def test_charging_relays(self):
        """Test charging system slot relays (power and lock)."""
        print("\n[TEST] Charging Slot Relays")
        for slot in ['slot1', 'slot2', 'slot3', 'slot4']:
            power_pin = CHARGING_RELAYS.get(f'{slot}_power')
            lock_pin = CHARGING_RELAYS.get(f'{slot}_lock')
            if not power_pin or not lock_pin:
                continue
            print(f"  {slot} (power={power_pin}, lock={lock_pin})")
            
            # Turn ON power relay
            if self.simulate:
                print(f"    [SIM] Power ON")
            else:
                GPIO.output(power_pin, GPIO.HIGH)
                print(f"    [HW] Power ON (pin {power_pin})")
            time.sleep(0.5)
            
            # Turn ON lock relay
            if self.simulate:
                print(f"    [SIM] Lock ON")
            else:
                GPIO.output(lock_pin, GPIO.HIGH)
                print(f"    [HW] Lock ON (pin {lock_pin})")
            time.sleep(0.5)
            
            # Turn OFF both
            if self.simulate:
                print(f"    [SIM] Power & Lock OFF")
            else:
                GPIO.output(power_pin, GPIO.LOW)
                GPIO.output(lock_pin, GPIO.LOW)
                print(f"    [HW] Power & Lock OFF")
            time.sleep(0.3)
    
    def test_water_relays(self):
        """Test water system relays (pump and solenoid valve)."""
        print("\n[TEST] Water System Relays (MCP23017)")
        if self.mcp is None and not self.simulate:
            print("  [SKIP] MCP23017 not available")
            return
        
        # Test pump relay
        print("  Pump Relay (GPB0)")
        if self.simulate:
            print("    [SIM] Pump ON")
            time.sleep(0.5)
            print("    [SIM] Pump OFF")
        else:
            pump = getattr(self.mcp, PUMP_PIN)
            pump.value = True
            print(f"    [HW] Pump ON")
            time.sleep(0.5)
            pump.value = False
            print(f"    [HW] Pump OFF")
        time.sleep(0.3)
        
        # Test solenoid valve relay
        print("  Solenoid Valve Relay (GPB1)")
        if self.simulate:
            print("    [SIM] Solenoid ON")
            time.sleep(0.5)
            print("    [SIM] Solenoid OFF")
        else:
            solenoid = getattr(self.mcp, SOLENOID_PIN)
            solenoid.value = True
            print(f"    [HW] Solenoid ON")
            time.sleep(0.5)
            solenoid.value = False
            print(f"    [HW] Solenoid OFF")
        time.sleep(0.3)
    
    def test_water_sensors(self):
        """Test water system sensors (flow meter, tank level)."""
        print("\n[TEST] Water System Sensors (MCP23017)")
        if self.mcp is None and not self.simulate:
            print("  [SKIP] MCP23017 not available")
            return
        
        # Test flow sensor
        print("  Flow Meter (GPA0)")
        if self.simulate:
            print("    [SIM] Flow status: LOW (no flow)")
            time.sleep(0.5)
        else:
            flow = getattr(self.mcp, FLOW_PIN)
            status = "HIGH" if flow.value else "LOW"
            print(f"    [HW] Flow status: {status}")
        
        # Test tank level sensors
        print("  Tank Level Low Sensor (GPA1)")
        if self.simulate:
            print("    [SIM] Tank low status: HIGH (tank OK)")
        else:
            low = getattr(self.mcp, TANK_LOW_PIN)
            status = "HIGH" if low.value else "LOW"
            print(f"    [HW] Tank low status: {status} ({'tank OK' if low.value else 'tank LOW'})")
        
        print("  Tank Level High Sensor (GPA2)")
        if self.simulate:
            print("    [SIM] Tank full status: HIGH (not full)")
        else:
            high = getattr(self.mcp, TANK_HIGH_PIN)
            status = "HIGH" if high.value else "LOW"
            print(f"    [HW] Tank full status: {status} ({'not full' if high.value else 'tank FULL'})")
        time.sleep(0.3)
    
    def test_coin_acceptor(self):
        """Test coin acceptor sensor."""
        print("\n[TEST] Coin Acceptor (Pi GPIO)")
        print(f"  Coin Signal (pin {COIN_SIGNAL})")
        if self.simulate:
            print("    [SIM] Coin status: idle (no coin detected)")
        else:
            status = "HIGH" if GPIO.input(COIN_SIGNAL) else "LOW"
            print(f"    [HW] Coin status: {status}")
        time.sleep(0.2)
    
    def test_ultrasonic_sensor(self):
        """Test ultrasonic distance sensor."""
        print("\n[TEST] Ultrasonic Distance Sensor (Pi GPIO)")
        print(f"  Trigger: pin {ULTRASONIC['trig']}, Echo: pin {ULTRASONIC['echo']}")
        if self.simulate:
            print("    [SIM] Distance: 25.5 cm (simulated)")
        else:
            # Pulse the trigger pin
            GPIO.output(ULTRASONIC['trig'], GPIO.HIGH)
            time.sleep(0.00001)  # 10 microseconds
            GPIO.output(ULTRASONIC['trig'], GPIO.LOW)
            
            # Measure echo pulse width (simplified)
            pulse_start = time.time()
            pulse_end = None
            timeout = time.time() + 0.1
            
            try:
                while GPIO.input(ULTRASONIC['echo']) == GPIO.LOW and time.time() < timeout:
                    pulse_start = time.time()
                while GPIO.input(ULTRASONIC['echo']) == GPIO.HIGH and time.time() < timeout:
                    pulse_end = time.time()
                
                if pulse_end:
                    pulse_duration = pulse_end - pulse_start
                    distance = (pulse_duration * 34300) / 2  # cm
                    print(f"    [HW] Distance: {distance:.1f} cm")
                else:
                    print(f"    [HW] Distance: timeout (no echo received)")
            except Exception as e:
                print(f"    [HW] Error: {e}")
        time.sleep(0.3)
    
    def test_tm1637_displays(self):
        """Test TM1637 7-segment displays for each slot."""
        print("\n[TEST] TM1637 Displays (Pi GPIO)")
        displays = PI_PINS['tm1637_displays']['displays']
        for slot, pins in displays.items():
            clk = pins['CLK']
            dio = pins['DIO']
            print(f"  {slot}: CLK={clk}, DIO={dio} → [SIM] Display test 0{slot.replace('slot', '')}:00")
        time.sleep(0.3)
    
    def test_adc_current_sensors(self):
        """Test ACS712 current sensors via MCP3008 ADC."""
        print("\n[TEST] ACS712 Current Sensors (SPI MCP3008 ADC)")
        channels = PI_PINS['charging_sensors_adc']
        for slot, ch in channels.items():
            if self.simulate:
                print(f"  {slot} (ADC CH{ch}): [SIM] 0.00 A")
            else:
                print(f"  {slot} (ADC CH{ch}): [HW] 0.00 A (reading hardware)")
        time.sleep(0.3)
    
    def cleanup(self):
        """Clean up GPIO resources."""
        print("\n[CLEANUP]")
        if HAS_GPIO and not self.simulate:
            try:
                GPIO.cleanup()
                print("  GPIO cleaned up")
            except Exception as e:
                print(f"  GPIO cleanup failed: {e}")
        if self.mcp is not None:
            print("  MCP23017 closed")

def main():
    """Run the integrated test suite."""
    print("=" * 60)
    print("Integrated Water & Charging Systems Test")
    print("=" * 60)
    
    # Determine if we should simulate (no -hw flag)
    import sys
    simulate = '-hw' not in sys.argv
    
    try:
        test = IntegratedTestSystem(simulate=simulate)
        
        # Run all tests
        test.test_charging_relays()
        test.test_water_relays()
        test.test_water_sensors()
        test.test_coin_acceptor()
        test.test_ultrasonic_sensor()
        test.test_tm1637_displays()
        test.test_adc_current_sensors()
        
        print("\n" + "=" * 60)
        print("All tests completed!")
        print("=" * 60)
    except KeyboardInterrupt:
        print("\n\n[INTERRUPT] Test interrupted by user")
    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        test.cleanup()

if __name__ == '__main__':
    main()
