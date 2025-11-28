# check_private_key.py
import json

print("=== CHECKING PRIVATE KEY FORMAT ===")

with open("firebase-key.json", "r") as f:
    key_data = json.load(f)

private_key = key_data.get("private_key", "")
print("Private key starts with:", private_key[:50])
print("Private key ends with:", private_key[-50:])
print("Private key contains newlines:", "\n" in private_key)
print("Private key length:", len(private_key))

# Check if it's properly formatted
if private_key.startswith("-----BEGIN PRIVATE KEY-----") and private_key.endswith("-----END PRIVATE KEY-----\n"):
    print("Private key format appears correct")
else:
    print("Private key format may be corrupted")