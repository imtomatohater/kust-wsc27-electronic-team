"""python -m unittest discover -s tests   (run from vcu/)"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pedal import Pedal, validate  # noqa: E402

BASE = dict(enabled=True, ch1_mV_released=600, ch1_mV_full=4300, deadzone_pct=8, full_pct=95,
            curve_exp=1.0, max_torque_pct=20, fault_debounce_s=0.1)


def mv_at(travel_pct, rel=600, full=4300):
    return rel + travel_pct / 100 * (full - rel)


class PedalTest(unittest.TestCase):
    def ev(self, p, mv1, mv2=None, brake=0, alive=True, now=0.0):
        return p.evaluate(mv1, mv2, brake, alive, now)

    def test_config_ok(self):
        self.assertEqual(validate(BASE), [])
        self.assertTrue(validate(dict(BASE, deadzone_pct=99)))
        self.assertTrue(validate(dict(BASE, ch1_mV_released=100)))   # below fault_low

    def test_mapping(self):
        p = Pedal(BASE)
        self.assertEqual(self.ev(p, 600)['torque'], 0.0)
        self.assertEqual(self.ev(p, mv_at(7))['torque'], 0.0)              # deadzone
        self.assertAlmostEqual(self.ev(p, mv_at(51.5))['torque'], 10.0, 3)  # half of 0..20
        self.assertEqual(self.ev(p, mv_at(96))['torque'], 20.0)             # saturate
        self.assertEqual(self.ev(p, 4600)['torque'], 20.0)                  # > full, in range

    def test_curve(self):
        p = Pedal(dict(BASE, curve_exp=2.0))
        self.assertAlmostEqual(self.ev(p, mv_at(51.5))['torque'], 5.0, 3)

    def test_inverted_sensor(self):
        p = Pedal(dict(BASE, ch1_mV_released=4300, ch1_mV_full=600))
        self.assertEqual(self.ev(p, 4300)['torque'], 0.0)
        self.assertEqual(self.ev(p, 600)['torque'], 20.0)

    def test_range_fault_debounced(self):
        p = Pedal(BASE)
        r = self.ev(p, 100, now=0.0)                 # open wire
        self.assertEqual(r['torque'], 0.0)
        self.assertIsNone(r['fault'])                # not latched yet
        self.assertIn('확인 중', r['note'])
        r = self.ev(p, 100, now=0.15)
        self.assertIn('APPS1', r['fault'])
        r = self.ev(p, 4900, now=0.2)                # short to supply (new problem, same timer)
        self.assertIn('APPS1', r['fault'])
        self.assertGreater(self.ev(p, mv_at(50), now=0.3)['torque'], 0)   # recovers

    def test_timeout(self):
        p = Pedal(BASE)
        self.assertIn('끊김', self.ev(p, mv_at(50), alive=False)['fault'])
        self.assertIn('끊김', self.ev(p, None)['fault'])

    def test_dual_plausibility(self):
        c = dict(BASE, dual_channel=True, ch2_mV_released=300, ch2_mV_full=2150)
        p = Pedal(c)
        ok = self.ev(p, mv_at(50), mv_at(50, 300, 2150))
        self.assertIsNone(ok['fault'])
        self.assertGreater(ok['torque'], 0)
        low2 = self.ev(p, mv_at(60), mv_at(55, 300, 2150))   # uses lower (conservative)
        self.assertAlmostEqual(low2['travel'], 55, 1)
        self.ev(p, mv_at(80), mv_at(50, 300, 2150), now=1.0)
        bad = self.ev(p, mv_at(80), mv_at(50, 300, 2150), now=1.2)
        self.assertIn('불일치', bad['fault'])
        self.assertEqual(bad['torque'], 0.0)

    def test_brake_override_and_rearm(self):
        p = Pedal(BASE)
        self.assertGreater(self.ev(p, mv_at(50))['torque'], 0)
        self.assertEqual(self.ev(p, mv_at(50), brake=1)['torque'], 0.0)
        r = self.ev(p, mv_at(50), brake=0)           # brake off but pedal still pressed
        self.assertEqual(r['torque'], 0.0)
        self.assertIn('다시 밟', r['note'])
        self.ev(p, 600)                              # release
        self.assertGreater(self.ev(p, mv_at(50))['torque'], 0)


if __name__ == '__main__':
    unittest.main()
