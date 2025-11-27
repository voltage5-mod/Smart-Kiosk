# main.py
# -------------------------
# Initialize Firebase FIRST
# -------------------------
import firebase_admin
from firebase_admin import credentials, db

try:
    cred = credentials.Certificate("firebase-key.json")
    firebase_admin.initialize_app(cred, {
        "databaseURL": "https://kiosk-testing-22bf4-default-rtdb.firebaseio.com"
    })

    print("Firebase initialized.")
except Exception as e:
    print("FIREBASE INIT ERROR:", e)

# -------------------------
# Import rest of app
# -------------------------
import logging
from kiosk_app import KioskApp

# Expose DB helpers to kiosk_app via module globals
try:
    from db import firebase_helpers as fh
    # Expose functions expected by kiosk_app
    read_user = fh.read_user
    write_user = fh.write_user
    # Also expose references if available (users_ref used by some screens)
    users_ref = getattr(fh, "users_ref", None)
    slots_ref = getattr(fh, "slots_ref", None)
    append_audit_log = fh.append_audit_log
except Exception:
    # Fallback no-op implementations if firebase helpers not available
    def read_user(uid):
        return None

    def write_user(uid, data):
        return False

    users_ref = None
    slots_ref = None

    def append_audit_log(**_):
        return False

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = KioskApp()
    app.mainloop()
