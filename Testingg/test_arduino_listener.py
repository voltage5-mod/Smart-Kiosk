import unittest
from arduino_listener import ArduinoListener

class ParseTests(unittest.TestCase):
    def setUp(self):
        self.l = ArduinoListener(simulate=True)

    def test_coin_parsing(self):
        ev = self.l._parse_line('Coin detected, new credit: 250 ml')
        self.assertEqual(ev['event'], 'COIN_INSERTED')
        self.assertEqual(ev['volume_ml'], 250)

    def test_credit_parsing(self):
        ev = self.l._parse_line('CREDIT_ML: 300')
        self.assertEqual(ev['event'], 'CREDIT_UPDATE')
        self.assertEqual(ev['credit_ml'], 300)

    def test_dispensing_done(self):
        ev = self.l._parse_line('Dispensing complete. Total: 250.3 ml')
        self.assertEqual(ev['event'], 'DISPENSING_DONE')
        self.assertAlmostEqual(ev['total_ml'], 250.3)

    def test_flow_pulses(self):
        ev = self.l._parse_line('FLOW_PULSES: 45')
        self.assertEqual(ev['event'], 'FLOW_PULSES')
        self.assertEqual(ev['pulses'], 45)

    def test_cup_detect(self):
        ev = self.l._parse_line('Cup detected. Starting dispense...')
        self.assertEqual(ev['event'], 'CUP_DETECTED')

    def test_unknown_falls_back(self):
        ev = self.l._parse_line('Hello world')
        self.assertEqual(ev['event'], 'RAW')

if __name__ == '__main__':
    unittest.main()
