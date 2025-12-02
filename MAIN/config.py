# Configuration constants
class Config:
    # Pricing
    WATER_PRICE_PER_LITER = 5.00  # ₱5 per liter
    CHARGING_RATE = 5.00  # ₱5 per 30 minutes
    
    # Hardware thresholds
    CURRENT_THRESHOLD = 0.3  # Amps threshold for device detection
    CUP_DISTANCE_THRESHOLD = 10  # cm
    
    # Timeouts
    UNLOCK_DURATION = 5  # seconds
    AUTO_RETURN_TIMEOUT = 30  # seconds
    SESSION_TIMEOUT = 300  # 5 minutes
    
    # Flow sensor
    PULSES_PER_LITER = 450
    
    # Coin values
    COIN_VALUES = {
        1: 1.00,   # 1 peso
        5: 5.00,   # 5 pesos
        10: 10.00  # 10 pesos
    }