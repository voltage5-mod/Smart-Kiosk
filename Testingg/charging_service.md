CHARGING SERVICE - SERIAL PROTOCOL (Pi <-> Arduino)

Arduino Responsibilities:
- Detect coin acceptor pulses using interrupts
- Convert pulses to peso value (1P, 5P, 10P)
- Send serial messages to Raspberry Pi:
    COIN_INSERTED <peso_value>

Pi Responsibilities:
- Activate CHARGE mode by sending: "MODE CHARGE"
- Read Arduino serial messages from /dev/ttyACM0 or /dev/ttyUSB0
- Convert peso value to charging minutes (5P=10min, 10P=20min)
- Show Charging Coin Popup with updated totals
- Allocate added minutes to selected charging slot
- Start countdown timers for each charging slot
- Update Firebase balance for members/subscribers
- Reset Arduino credit using: "RESET"

Serial Messages from Arduino:
- COIN_INSERTED 5        # user inserted ₱5
- COIN_INSERTED 10       # user inserted ₱10

No GPIO used for coin acceptor. All communication is serial.
