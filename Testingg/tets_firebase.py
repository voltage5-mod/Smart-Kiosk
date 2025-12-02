#!/usr/bin/env python3
import json
import os
import firebase_admin
from firebase_admin import credentials, db

# Test 1: Check if key file exists
KEY_FILE = "firebase-key.json"
print(f"1. Checking key file: {KEY_FILE}")
if not os.path.exists(KEY_FILE):
    print(f"   ERROR: File not found!")
    exit(1)
print("   ✓ File exists")

# Test 2: Check if JSON is valid
print(f"2. Checking JSON validity...")
try:
    with open(KEY_FILE, 'r') as f:
        key_data = json.load(f)
    print("   ✓ JSON is valid")
    
    # Check required fields
    required = ['type', 'project_id', 'private_key_id', 'private_key', 
                'client_email', 'client_id']
    missing = []
    for field in required:
        if field not in key_data:
            missing.append(field)
    
    if missing:
        print(f"   ERROR: Missing fields: {missing}")
    else:
        print(f"   ✓ All required fields present")
        
except json.JSONDecodeError as e:
    print(f"   ERROR: Invalid JSON: {e}")
    exit(1)

# Test 3: Initialize Firebase
print(f"3. Testing Firebase initialization...")
try:
    cred = credentials.Certificate(KEY_FILE)
    app = firebase_admin.initialize_app(cred, {
        "databaseURL": "https://kiosk-testing-22bf4-default-rtdb.firebaseio.com/"
    })
    print("   ✓ Firebase initialized")
    
    # Test 4: Test database connection
    print(f"4. Testing database connection...")
    try:
        ref = db.reference("/")
        data = ref.get()
        if data is None:
            print("   ✓ Connected to database (no data at root)")
        else:
            print(f"   ✓ Connected to database (found {len(data)} keys at root)")
            
        # Clean up
        firebase_admin.delete_app(app)
        print("   ✓ Cleanup successful")
        
    except Exception as e:
        print(f"   ERROR: Database connection failed: {e}")
        
except Exception as e:
    print(f"   ERROR: Firebase initialization failed: {e}")
    print("\nTroubleshooting tips:")
    print("1. Check if the private key has actual newlines (not \\n)")
    print("2. Check if the service account has database permissions")
    print("3. Check if your IP is whitelisted in Firebase")