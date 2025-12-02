import time
from datetime import datetime

class SessionManager:
    def __init__(self):
        self.current_user = None
        self.session_start = None
        self.coins_inserted = 0.00
        self.current_screen = None
        self.slot_in_use = None
    
    def login(self, user_data):
        """Start a new session for user"""
        self.current_user = user_data
        self.session_start = datetime.now()
        self.coins_inserted = 0.00
        self.slot_in_use = None
        
        # Reset water balance for guests
        if self.current_user['type'] == 'guest':
            self.current_user['water_balance'] = 0.00
        
        return True
    
    def logout(self):
        """End current session"""
        # Save session data if needed
        self.current_user = None
        self.session_start = None
        self.coins_inserted = 0.00
        self.slot_in_use = None
        return True
    
    def add_coin(self, value):
        """Add coin to session"""
        self.coins_inserted += value
        
        # Distribute coins based on user type
        if self.current_user:
            if self.current_user['type'] == 'guest':
                # Guest: only add to charge balance
                self.current_user['charge_balance'] += value
            else:
                # Member: add to both balances
                self.current_user['charge_balance'] += value
                self.current_user['water_balance'] += value
        
        return self.coins_inserted
    
    def get_balance_display(self):
        """Get formatted balance display"""
        if not self.current_user:
            return "P0.00 | P0.00"
        
        charge = self.current_user.get('charge_balance', 0.00)
        water = self.current_user.get('water_balance', 0.00)
        
        return f"P{charge:.2f} | P{water:.2f}"
    
    def occupy_slot(self, slot):
        """Mark a slot as occupied"""
        self.slot_in_use = slot
        if self.current_user:
            self.current_user['occupied_slot'] = slot
            self.current_user['charging_status'] = 'charging'
    
    def release_slot(self):
        """Release occupied slot"""
        if self.current_user:
            self.current_user['occupied_slot'] = None
            self.current_user['charging_status'] = 'idle'
        self.slot_in_use = None
    
    def is_guest(self):
        """Check if current user is guest"""
        return self.current_user and self.current_user['type'] == 'guest'
    
    def is_member(self):
        """Check if current user is member"""
        return self.current_user and self.current_user['type'] == 'member'