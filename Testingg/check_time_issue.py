# check_time_issue.py
import datetime
import time
import subprocess
import os

print("=== TIME DIAGNOSTICS ===")
print("Current local time:", datetime.datetime.now())
print("Current UTC time:", datetime.datetime.utcnow())
print("System timestamp:", time.time())
print("Timezone:", time.tzname)

# Check if time is reasonable
current_time = datetime.datetime.now()
print(f"Year: {current_time.year}, Month: {current_time.month}")

if current_time.year < 2024:
    print("SYSTEM TIME IS COMPLETELY WRONG!")
    print("This is causing the JWT signature error")
else:
    print("Time appears correct")

# Check time sync status
print("\n=== TIME SYNC STATUS ===")
try:
    result = subprocess.run(['timedatectl', 'status'], capture_output=True, text=True)
    print(result.stdout)
except Exception as e:
    print(f"Error checking timedatectl: {e}")