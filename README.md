🧠 PROJECT SUMMARY FOR GITHUB COPILOT / DEVELOPMENT CONTEXT

Project Overview

The system is a Solar-Powered IoT-based Smart Vending and Charging Kiosk that provides:

Secure mobile charging slots

Mineral water vending

Integrated RFID-based authentication

Cloud database synchronization (Firebase Realtime DB)

Web admin dashboard for management and monitoring
It is powered by a 24V solar energy system with an automatic AC backup via ATS and inverter, ensuring continuous operation.



---

⚙️ SYSTEM COMPONENTS AND FUNCTIONS

Core Processing

Raspberry Pi 4 Model B (4GB RAM) – The main controller that handles UI, logic control, sensor inputs, relay actuation, and Firebase cloud synchronization.

Python 3 – Primary programming language used for kiosk automation and Firebase communication.

Firebase Realtime Database – Stores user information, allowances, session data, and system status in real time.

Touchscreen LCD (7-inch) – Main kiosk interface for user interaction and service selection.

Relay Modules (5V) – Control power delivery to each charging slot, solenoid locks, and pumps.

MCP3008 ADC – Reads analog signals from current sensors (ACS712) and water flow sensors.



---

Charging Subsystem (per slot)

Each of the 5 charging slots contains:

Relay 1 (Power Control): Switches ON/OFF power supply to the USB charging port.

Relay 2 (Solenoid Lock): Controls the door locking mechanism for the compartment.

ACS712 Current Sensor (5A): Detects current draw; identifies charging state (active/inactive).

TM1637 4-Digit LED Display: Displays countdown timer in Minutes:Seconds format.

Multi-purpose USB Cable (5V 2.4A): Connects user’s device.

Buck Converter (12V → 5V 5A): Supplies power to each slot individually.

Solenoid Lock (12V): Locks or unlocks compartment doors.


🧩 Slot Logic Summary:

1. When the user selects a slot, the relay powers ON the slot (USB active, solenoid unlocked).


2. When current sensor detects charging, timer starts and solenoid locks automatically.


3. If user stops session or time ends → relay OFF (no power), solenoid unlocks.


4. If user unplugs temporarily → timer pauses, continues upon replugging.


5. If idle > 1 minute → session terminates automatically.




---

Water Vending Subsystem

Bottom-Load Water Dispenser (AC-powered): Source of cold water.

Mini 5V Pump: Controls the water flow from dispenser to outlet.

Ultrasonic Sensor (HC-SR04): Detects cup presence — starts dispensing when a cup is detected, stops when removed.

Water Flow Sensor (YF-S201): Measures water flow (L/min) for balance deduction.

Relay Module: Controls 5V mini pump power.

XKC-Y26 NPN Non-Contact Liquid Level Sensors (2x):

Sensor 1 (High Level): Detects full water level.

Sensor 2 (Low Level): Detects refill trigger (when water is low).



💧 Water Logic Summary:

1. Cup detected → relay ON → pump activates → water flows.


2. Flow sensor measures L/min → deducts balance/time in database.


3. Cup removed → dispensing stops → timer pauses.


4. Idle >10 seconds → session terminates.


5. Liquid level sensors monitor tank status for maintenance alerts.




---

Coin Slot and Subscription

ALLAN Coin Acceptor (4-pin): Accepts 1, 5, and 10 peso coins for pay-per-use.

GPIO Input: Detects pulse signals per coin denomination.

Logic:

Each coin adds predefined time/volume credit.

Credits are stored under user’s temporary RFID record in Firebase.


Subscription Requests:

Non-members can request subscriptions via kiosk.

Admin dashboard receives notification and processes via Firebase.




---

Power and Energy System

Solar Panel (300W 12V × 2 in Series = 600W 24V) – Collects solar energy.

MPPT Charge Controller (24V 30A) – Regulates and optimizes solar power.

Solar Battery Bank (24V 100Ah LiFePO4) – Stores energy for day/night use.

Automatic Transfer Switch (ATS) – Switches to AC backup when solar is insufficient.

Pure Sine Wave Inverter (24V → 220V AC) – Powers AC devices like the water dispenser.

Buck Converters (24V → 12V / 5V) – Supply correct voltage to each electronic subsystem.

Safety Components: Fuses (5A, 10A, 20A), circuit breakers, and surge protectors.


⚡ Power Flow Summary: Solar Panel → MPPT Controller → Battery (24V) →
↳ Buck Converters → 12V/5V Systems
↳ Inverter → AC Water Dispenser
↳ ATS + AC Charger → Backup from Campus Power


---

System Monitoring

Web Admin Dashboard: Firebase-based system for:

Monitoring kiosk status (Active/Offline, Water Level, Battery %)

Viewing user database and balances.

Handling membership registration and subscription.

Receiving real-time notifications for maintenance or requests.


RFID Authentication (125kHz USB Reader):

Reads unique UIDs.

Members → free allowance logic.

Non-members → prompt for coin or subscription.

New RFID → prompt for registration.




---

🧩 LOGICAL FLOWS

1. User Access Flow

1. User scans RFID.


2. System checks Firebase DB for user data.

If new: prompts Register / Coinslot / Subscription.

If member: proceeds to Main Menu (Charging/Water).

If non-member: goes to Main Menu with coin and subscription option.



3. After authentication → displays name, student ID, and balances.




---

2. Charging Slot Flow

1. User selects “Charging Service” → chooses available slot.


2. System unlocks slot (relay ON solenoid OFF).


3. When phone connected → ACS712 detects current change → timer starts, solenoid locks.


4. Timer updates every second (display + Firebase).


5. If user presses stop → relay OFF, solenoid unlocks, session ends.


6. If phone unplugged → timer pauses, resumes upon replug.


7. If idle > 1 min → session auto-terminates.


8. When time = 0 → power cut, solenoid unlocks, database resets session.




---

3. Water Dispensing Flow

1. User selects “Water Vendo”.


2. Ultrasonic detects cup → relay ON (pump activated).


3. Flow sensor measures water output → deducts time from balance.


4. If cup removed → pump OFF, timer stops.


5. Idle > 10s → session terminates.


6. Water level sensors monitor tank status; low water triggers admin alert.




---

4. Coin Slot and Subscription Flow

Coinslot:

RFID must be scanned first → temporary user session created.

User select service

Coin inserted → corresponding credit added to user DB.

User can then use charging or water vending based on credit.


Subscription:

Non-member presses “Subscription” → system sends request to admin dashboard.

Admin approves plan → Firebase updates user type to “Subscriber” with corresponding balance and expiry.




---

5. Solar and Power Management Flow

1. Solar panel provides power to charge battery via MPPT.


2. Raspberry Pi draws power from 5V buck converter.


3. All relays, sensors, and peripherals powered from 12V or 5V regulated rails.


4. If battery < threshold → ATS automatically switches to AC grid input.


5. Admin dashboard updates power source and battery % in real time.




---

6. Cloud Communication and IoT Logic

Firebase continuously syncs:

User sessions (UID, slot, balance, time)

Water and charging data

Kiosk power status, tank level, and errors


Raspberry Pi sends updates every 10 seconds or on state change.

Admin dashboard reflects updates instantly.



---

7. System Safety and Redundancy

Relay isolation for all high-current lines.

Fuse protection per slot (5A each).

ATS for automatic AC fallback.

Solenoid locks default to locked when no power (failsafe).

Overcurrent detection from ACS712 triggers automatic cut-off.



---
> This project integrates renewable energy, IoT-based automation, and RFID-based user authentication into a single unified kiosk system. Each charging slot and vending mechanism operates independently but synchronizes data through a shared Firebase cloud. The Raspberry Pi serves as the logic controller, user interface hub, and IoT gateway. Power management ensures sustainability via solar integration and AC backup, while real-time database operations allow for accurate usage monitoring, subscription management, and system alerts via an admin dashboard.

