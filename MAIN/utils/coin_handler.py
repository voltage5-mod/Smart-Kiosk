import time
from config import Config

class CoinHandler:
    def __init__(self, hardware, session):
        self.hardware = hardware
        self.session = session
        self.last_pulse_time = 0
        self.pulse_buffer = []
        self.pulse_timeout = 0.5  # seconds
        
        # Coin pulse patterns
        self.COIN_PULSE_MAP = {
            1: [1],      # 1 peso: 1 pulse
            5: [1, 1, 1, 1, 1],  # 5 pesos: 5 pulses
            10: [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]  # 10 pesos: 10 pulses
        }
    
    def handle_coin_pulse(self):
        """Handle coin acceptor pulse"""
        current_time = time.time()
        
        # Detect coin value based on pulse pattern
        if current_time - self.last_pulse_time > self.pulse_timeout:
            # New coin sequence
            coin_value = self.identify_coin(self.pulse_buffer)
            if coin_value:
                self.process_coin(coin_value)
            self.pulse_buffer = []
        
        self.pulse_buffer.append(current_time)
        self.last_pulse_time = current_time
    
    def identify_coin(self, pulse_times):
        """Identify coin value from pulse pattern"""
        if len(pulse_times) == 1:
            return 1  # 1 peso
        elif len(pulse_times) == 5:
            return 5  # 5 pesos
        elif len(pulse_times) == 10:
            return 10  # 10 pesos
        return None
    
    def process_coin(self, coin_value):
        """Process coin insertion"""
        peso_value = Config.COIN_VALUES.get(coin_value, 0)
        
        if peso_value > 0:
            # Add to session
            self.session.add_coin(peso_value)
            
            # Update Firebase if needed
            if self.session.current_user and hasattr(self, 'firebase'):
                # Add transaction record
                pass
            
            print(f"Coin inserted: P{peso_value:.2f}")
    
    def simulate_coin(self, coin_value):
        """Simulate coin insertion (for testing)"""
        peso_value = Config.COIN_VALUES.get(coin_value, 0)
        if peso_value > 0:
            self.session.add_coin(peso_value)
            return peso_value
        return 0