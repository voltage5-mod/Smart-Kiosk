import firebase_admin
from firebase_admin import credentials, db
import os

# Auto-detect firebase-key.json location
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_PATH = os.path.join(BASE_DIR, "firebase-key.json")

print("[INFO] Using key path:", KEY_PATH)

try:
    cred = credentials.Certificate(KEY_PATH)
    firebase_admin.initialize_app(cred, {
        "databaseURL": "https://kiosk-testing-22bf4-default-rtdb.asia-southeast1.firebasedatabase.app"
    })
    print("[SUCCESS] Firebase initialized!")

    ref = db.reference("/test_connection")
    ref.set({"status": "ok"})
    print("[SUCCESS] Database write test completed!")

except Exception as e:
    print("[ERROR]", e)
