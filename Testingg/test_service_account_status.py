# test_service_account_status.py
from google.oauth2 import service_account
import google.auth.transport.requests

print("=== CHECKING SERVICE ACCOUNT STATUS ===")

try:
    # Load credentials
    credentials = service_account.Credentials.from_service_account_file(
        "firebase-key.json",
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    
    # Refresh token to test if account is active
    request = google.auth.transport.requests.Request()
    credentials.refresh(request)
    
    print("SUCCESS: Service account is ACTIVE and can generate tokens")
    print("Token type:", credentials.token)
    print("Project ID:", credentials.project_id)
    
except Exception as e:
    print(f"ERROR: Service account issue - {e}")