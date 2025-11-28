# test_jwt_auth.py
import firebase_admin
from firebase_admin import credentials, auth
import json

print("=== TESTING FIREBASE AUTHENTICATION ===")

try:
    # Load and verify the service account key
    with open("firebase-key.json", "r") as f:
        key_data = json.load(f)
    print("✅ Service account key loaded")
    
    # Initialize Firebase Auth only (no database)
    cred = credentials.Certificate("firebase-key.json")
    app = firebase_admin.initialize_app(cred)
    print("✅ Firebase App initialized")
    
    # Test authentication by creating a custom token
    custom_token = auth.create_custom_token("test-user-123")
    print("✅ JWT Authentication SUCCESSFUL!")
    print("Custom token created (first 50 chars):", custom_token.decode()[:50] + "...")
    
except Exception as e:
    print(f"❌ JWT Authentication FAILED: {e}")
    print(f"Error details: {type(e).__name__}")