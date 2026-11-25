"""CAN bitrate / WS22 base auto-detection with a fake adapter."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import can  # noqa: E402
from vcu_core import scan_can, guess_ws22_base, save_can_settings, WS22_OFFSETS  # noqa: E402


class FakeBus:
    """Valid WS22 frames (base 0x400) only at the right bitrate, error frames otherwise."""
    def __init__(self, bitrate, true_bitrate=250000, base=0x400):
        self.ok = bitrate == true_bitrate
        self.ids = [base + o for o in WS22_OFFSETS]
        self.n = 0

    def recv(self, timeout):
        self.n += 1
        if self.ok:
            return can.Message(arbitration_id=self.ids[self.n % len(self.ids)], is_extended_id=False)
        return can.Message(is_error_frame=True)

    def shutdown(self):
        pass


class ScanTest(unittest.TestCase):
    def test_guess_base(self):
        self.assertEqual(guess_ws22_base({0x400 + o: 1 for o in WS22_OFFSETS}), 0x400)
        self.assertEqual(guess_ws22_base({0x080 + o: 1 for o in (1, 2, 3, 4, 5)}), 0x080)
        self.assertIsNone(guess_ws22_base({0x123: 1, 0x700: 2}))

    def test_scan_finds_bitrate_and_base(self):
        best, res = scan_can('pcan', 'X', dwell=0.05, opener=lambda br: FakeBus(br))
        self.assertEqual(best['bitrate'], 250000)
        self.assertEqual(best['base'], 0x400)
        self.assertEqual(res[0]['bitrate'], 1000000)
        self.assertGreater(res[0]['errors'], 0)
        self.assertEqual(res[0]['frames'], 0)

    def test_scan_nothing(self):
        best, res = scan_can('pcan', 'X', dwell=0.02, opener=lambda br: FakeBus(br, true_bitrate=1))
        self.assertIsNone(best)

    def test_open_failure_reported(self):
        def opener(br):
            raise can.CanError('PCAN channel already in use')
        best, res = scan_can('pcan', 'X', dwell=0.02, opener=opener)
        self.assertIsNone(best)
        self.assertIn('already in use', res[0]['error'])
        self.assertEqual(len(res), 1)        # stops: retrying other bitrates is pointless

    def test_save(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'c.json')
            json.dump({'can': {'bitrate': 500000}, 'ids': {'ws22_base': 128}}, open(p, 'w'))
            save_can_settings(p, bitrate=250000, ws22_base=1024)
            c = json.load(open(p, encoding='utf-8'))
            self.assertEqual((c['can']['bitrate'], c['ids']['ws22_base']), (250000, 1024))


if __name__ == '__main__':
    unittest.main()
