"""
Firebase helper utilities for the water and charging vendo system.
Compatible with the expected database structure.
"""

import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

class FirebaseManager:
    def __init__(self, credential_path="firebase_key.json"):
        try:
            cred = credentials.Certificate(credential_path)
            firebase_admin.initialize_app(cred)
            self.db = firestore.client()
            print("Firebase initialized successfully")
        except Exception as e:
            print(f"Firebase initialization failed: {e}")
            self.db = None
    
    # --- User Management ---
    
    def get_user_by_rfid(self, rfid_uid):
        """Get user by RFID UID - matches UI expectations"""
        if not self.db:
            return self.get_mock_user(rfid_uid)
        
        try:
            users_ref = self.db.collection('users')
            query = users_ref.where('rfid_uid', '==', rfid_uid).limit(1)
            results = query.get()
            
            if results:
                user_data = results[0].to_dict()
                user_data['id'] = results[0].id
                
                # Ensure all expected fields exist
                user_data = self.ensure_user_fields(user_data)
                return user_data
            
            return None
            
        except Exception as e:
            print(f"Firebase get_user_by_rfid error: {e}")
            return None
    
    def ensure_user_fields(self, user_data):
        """Ensure user data has all expected fields"""
        defaults = {
            'name': 'Unknown',
            'type': 'guest',
            'student_id': '',
            'charge_balance': 0.00,
            'water_balance': 0.00,
            'temp_water_time': 0,
            'occupied_slot': None,
            'charging_status': 'idle',
            'rfid_uid': '',
            'created_at': datetime.now(),
            'last_used': datetime.now()
        }
        
        for key, default_value in defaults.items():
            if key not in user_data:
                user_data[key] = default_value
        
        return user_data
    
    def create_guest_user(self, rfid_uid, student_id=""):
        """Create a guest user account"""
        if not self.db:
            return self.create_mock_guest(rfid_uid, student_id)
        
        try:
            user_data = {
                'rfid_uid': rfid_uid,
                'name': f'Guest_{rfid_uid[-4:]}',
                'type': 'guest',
                'student_id': student_id,
                'charge_balance': 0.00,
                'water_balance': 0.00,  # Reset for guests
                'temp_water_time': 0,
                'occupied_slot': None,
                'charging_status': 'idle',
                'created_at': datetime.now(),
                'last_used': datetime.now()
            }
            
            # Check if already exists
            existing = self.get_user_by_rfid(rfid_uid)
            if existing:
                return existing
            
            doc_ref = self.db.collection('users').document()
            doc_ref.set(user_data)
            user_data['id'] = doc_ref.id
            
            return user_data
            
        except Exception as e:
            print(f"Create guest user error: {e}")
            return None
    
    def register_member(self, rfid_uid, name, student_id, initial_balance=0.00):
        """Register a new member user"""
        if not self.db:
            return self.create_mock_member(rfid_uid, name, student_id, initial_balance)
        
        try:
            # Check if RFID already exists
            existing = self.get_user_by_rfid(rfid_uid)
            if existing:
                return None
            
            user_data = {
                'rfid_uid': rfid_uid,
                'name': name,
                'type': 'member',
                'student_id': student_id,
                'charge_balance': float(initial_balance),
                'water_balance': float(initial_balance),  # Both balances for members
                'temp_water_time': 0,
                'occupied_slot': None,
                'charging_status': 'idle',
                'created_at': datetime.now(),
                'last_used': datetime.now()
            }
            
            doc_ref = self.db.collection('users').document()
            doc_ref.set(user_data)
            user_data['id'] = doc_ref.id
            
            return user_data
            
        except Exception as e:
            print(f"Register member error: {e}")
            return None
    
    # --- Balance Management ---
    
    def update_user_balance(self, user_id, charge_balance=None, water_balance=None):
        """Update user balance - matches UI expectations"""
        if not self.db:
            return True
        
        try:
            user_ref = self.db.collection('users').document(user_id)
            update_data = {
                'last_used': datetime.now()
            }
            
            if charge_balance is not None:
                update_data['charge_balance'] = float(charge_balance)
            
            if water_balance is not None:
                update_data['water_balance'] = float(water_balance)
            
            user_ref.update(update_data)
            return True
            
        except Exception as e:
            print(f"Update user balance error: {e}")
            return False
    
    def update_balance(self, user_id, new_balance):
        """Legacy method for backward compatibility"""
        return self.update_user_balance(user_id, charge_balance=new_balance)
    
    # --- Charging Slot Management ---
    
    def update_charging_status(self, user_id, slot=None, status='idle'):
        """Update user's charging status and occupied slot"""
        if not self.db:
            return True
        
        try:
            user_ref = self.db.collection('users').document(user_id)
            update_data = {
                'charging_status': status,
                'occupied_slot': slot,
                'last_used': datetime.now()
            }
            
            user_ref.update(update_data)
            
            # Also update slot collection
            if slot:
                self.update_slot_status(slot, user_id, status)
            
            return True
            
        except Exception as e:
            print(f"Update charging status error: {e}")
            return False
    
    def update_slot_status(self, slot, user_id, status):
        """Update slot status in slots collection"""
        try:
            slot_ref = self.db.collection('slots').document(slot)
            slot_data = {
                'user_id': user_id,
                'status': 'occupied' if status == 'charging' else 'available',
                'last_updated': datetime.now()
            }
            slot_ref.set(slot_data, merge=True)
        except Exception as e:
            print(f"Update slot status error: {e}")
    
    # --- Transaction Logging ---
    
    def add_coin_transaction(self, user_id, coin_value, balance_type='charge'):
        """Add coin transaction record"""
        if not self.db:
            return True
        
        try:
            tx_ref = self.db.collection('transactions')
            tx_ref.add({
                'user_id': user_id,
                'type': 'coin_insert',
                'coin_value': float(coin_value),
                'balance_type': balance_type,
                'timestamp': datetime.now(),
                'description': f'Inserted {coin_value} coin'
            })
            return True
        except Exception as e:
            print(f"Add coin transaction error: {e}")
            return False
    
    def add_water_transaction(self, user_id, amount, liters):
        """Add water dispensing transaction"""
        if not self.db:
            return True
        
        try:
            tx_ref = self.db.collection('transactions')
            tx_ref.add({
                'user_id': user_id,
                'type': 'water_dispense',
                'amount': float(amount),
                'liters': float(liters),
                'timestamp': datetime.now(),
                'description': f'Dispensed {liters}L for {amount}'
            })
            return True
        except Exception as e:
            print(f"Add water transaction error: {e}")
            return False
    
    def add_charging_transaction(self, user_id, amount, minutes, slot):
        """Add charging transaction"""
        if not self.db:
            return True
        
        try:
            tx_ref = self.db.collection('transactions')
            tx_ref.add({
                'user_id': user_id,
                'type': 'charging',
                'amount': float(amount),
                'minutes': float(minutes),
                'slot': slot,
                'timestamp': datetime.now(),
                'description': f'Charged for {minutes}min on {slot}'
            })
            return True
        except Exception as e:
            print(f"Add charging transaction error: {e}")
            return False
    
    # --- Database Setup ---
    
    def setup_database(self):
        """Initialize database structure if needed"""
        if not self.db:
            return False
        
        try:
            # Ensure slots collection exists
            slots = ['slot1', 'slot2', 'slot3', 'slot4']
            for slot in slots:
                slot_ref = self.db.collection('slots').document(slot)
                if not slot_ref.get().exists:
                    slot_ref.set({
                        'status': 'available',
                        'user_id': None,
                        'last_updated': datetime.now()
                    })
            
            print("Database setup completed")
            return True
            
        except Exception as e:
            print(f"Database setup error: {e}")
            return False
    
    # --- Mock Data for Testing ---
    
    def get_mock_user(self, rfid_uid):
        """Get mock user data for testing"""
        mock_users = {
            "123456789": {
                'id': 'user001',
                'rfid_uid': '123456789',
                'name': 'John Doe',
                'type': 'member',
                'student_id': '2023001',
                'charge_balance': 50.00,
                'water_balance': 25.00,
                'temp_water_time': 0,
                'occupied_slot': None,
                'charging_status': 'idle'
            },
            "987654321": {
                'id': 'user002',
                'rfid_uid': '987654321',
                'name': 'Jane Smith',
                'type': 'guest',
                'student_id': '',
                'charge_balance': 10.00,
                'water_balance': 0.00,
                'temp_water_time': 0,
                'occupied_slot': None,
                'charging_status': 'idle'
            }
        }
        return mock_users.get(rfid_uid)
    
    def create_mock_guest(self, rfid_uid, student_id=""):
        """Create mock guest user"""
        return {
            'id': f'guest_{rfid_uid}',
            'rfid_uid': rfid_uid,
            'name': f'Guest_{rfid_uid[-4:]}',
            'type': 'guest',
            'student_id': student_id,
            'charge_balance': 0.00,
            'water_balance': 0.00,
            'temp_water_time': 0,
            'occupied_slot': None,
            'charging_status': 'idle'
        }
    
    def create_mock_member(self, rfid_uid, name, student_id, initial_balance=0.00):
        """Create mock member user"""
        return {
            'id': f'member_{rfid_uid}',
            'rfid_uid': rfid_uid,
            'name': name,
            'type': 'member',
            'student_id': student_id,
            'charge_balance': float(initial_balance),
            'water_balance': float(initial_balance),
            'temp_water_time': 0,
            'occupied_slot': None,
            'charging_status': 'idle'
        }
    
    # --- Helper Methods for UI Compatibility ---
    
    def get_user(self, rfid_uid):
        """Alias for get_user_by_rfid for UI compatibility"""
        return self.get_user_by_rfid(rfid_uid)
    
    def deduct_balance(self, user_id, amount):
        """Legacy method - deduct from charge balance"""
        try:
            user_ref = self.db.collection('users').document(user_id)
            user = user_ref.get()
            if user.exists:
                current = user.to_dict().get('charge_balance', 0)
                new_balance = max(0, current - amount)
                user_ref.update({'charge_balance': new_balance})
                return new_balance
        except Exception as e:
            print(f"Deduct balance error: {e}")
        return None

# Singleton instance for easy access
firebase_manager = None

def get_firebase_manager():
    """Get or create Firebase manager instance"""
    global firebase_manager
    if firebase_manager is None:
        firebase_manager = FirebaseManager()
    return firebase_manager

# For backward compatibility with existing UI code
def get_user_by_rfid(rfid_uid):
    """Standalone function for UI compatibility"""
    manager = get_firebase_manager()
    return manager.get_user_by_rfid(rfid_uid)

def update_user_balance(user_id, charge_balance=None, water_balance=None):
    """Standalone function for UI compatibility"""
    manager = get_firebase_manager()
    return manager.update_user_balance(user_id, charge_balance, water_balance)

def update_charging_status(user_id, slot=None, status='idle'):
    """Standalone function for UI compatibility"""
    manager = get_firebase_manager()
    return manager.update_charging_status(user_id, slot, status)