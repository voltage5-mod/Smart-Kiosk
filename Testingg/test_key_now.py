# test_new_key_now.py
import firebase_admin
from firebase_admin import credentials, db

print("=== TESTING NEWLY DOWNLOADED KEY ===")

try:
    cred = credentials.Certificate("firebase-key.json")
    app = firebase_admin.initialize_app(cred, {
        "databaseURL": "https://kiosk-testing-22bf4-default-rtdb.firebaseio.com/"
    })
    
    print("SUCCESS: Firebase initialized with new key!")
    
    # Quick test
    ref = db.reference("new_key_test")
    ref.set({"status": "working", "test": "new_key"})
    print("SUCCESS: Database write working!")
    
    data = ref.get()
    print("SUCCESS: Database read working!")
    print("Data:", data)
    
    ref.delete()
    print("🎯 FIREBASE JWT ISSUE FIXED!")
    
except Exception as e:
    print(f"ERROR: {e}")
    print("The file might still be corrupted from manual operations")