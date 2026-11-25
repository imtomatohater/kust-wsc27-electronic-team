"""Integration: bench 24 V config + simulated WS22/PSU + virtual pedal (takes ~15 s)."""
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from vcu_core import Vcu, ARMED, FAULT, DISARMED, IMMEDIATE, SPEED  # noqa: E402

CFG = os.path.join(HERE, 'config_bench24v.json')


def wait(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


class BenchSim(unittest.TestCase):
    def setUp(self):
        self.v = Vcu(CFG, sim=True)
        self.pc = self.v.pc
        self.assertTrue(wait(lambda: self.v.snapshot()['fresh'] and self.v.snapshot()['pedal']))

    def tearDown(self):
        self.v.close()

    def mv(self, pct):
        return self.pc['ch1_mV_released'] + pct / 100 * (self.pc['ch1_mV_full'] - self.pc['ch1_mV_released'])

    def test_arm_drive_and_caps(self):
        v = self.v
        v.sim_pedal(mv1=self.mv(50))
        time.sleep(0.2)
        self.assertEqual(v.arm(), '페달을 떼고 ARM')
        self.assertEqual(v.set_mode(SPEED)[:5], '페달 입력')
        v.sim_pedal(mv1=self.mv(0))
        time.sleep(0.2)
        self.assertIsNone(v.arm())
        v.sim_pedal(mv1=self.mv(100))
        self.assertTrue(wait(lambda: v.snapshot()['cur'] > 0.19, 2.0))   # ramp 20 %/s -> ~1 s
        s = v.snapshot()
        self.assertLessEqual(s['cur'], 0.2001)                           # max_torque_pct
        self.assertEqual(s['state'], ARMED)
        time.sleep(4.0)
        s = v.snapshot()
        self.assertEqual(s['state'], ARMED, s['fault'])
        self.assertLess(abs(s['tel']['MotorVelocity']), 200)
        self.assertLessEqual(s['tel']['BusCurrent'], 10.5)
        v.sim_pedal(mv1=self.mv(0))
        self.assertTrue(wait(lambda: v.snapshot()['cur'] == 0.0, 0.5))   # release -> 0 at once
        v.sim_pedal(brake=True, mv1=self.mv(60))
        time.sleep(0.3)
        self.assertEqual(v.snapshot()['cur'], 0.0)

    def test_pedal_fault_trips(self):
        v = self.v
        self.assertIsNone(v.arm())
        v.sim_pedal(mv1=100)                       # open wire
        self.assertTrue(wait(lambda: v.snapshot()['state'] == FAULT, 1.0))
        s = v.snapshot()
        self.assertEqual(s['fault_level'], IMMEDIATE)
        self.assertIn('페달 고장', s['fault'])
        self.assertEqual(s['cur'], 0.0)

    def test_pedal_frozen_counter_trips(self):
        v = self.v
        self.assertIsNone(v.arm())
        v.sim_pedal(frozen=True)
        self.assertTrue(wait(lambda: v.snapshot()['state'] == FAULT, 1.0))
        self.assertIn('끊김', v.snapshot()['fault'])
        v.sim_pedal(frozen=False)
        time.sleep(0.3)
        v.reset_fault()
        self.assertEqual(v.snapshot()['state'], DISARMED)

    def test_monitor_only_never_sends(self):
        self.v.close()
        self.v = Vcu(CFG, sim=True, monitor=True)
        sent = []
        orig = self.v.bus.send
        self.v.bus.send = lambda fid, d: (sent.append(fid), orig(fid, d))
        self.assertTrue(wait(lambda: self.v.snapshot()['fresh']))
        self.assertIn('모니터링', self.v.arm())
        time.sleep(0.5)
        self.assertEqual(sent, [])
        self.assertIsNotNone(self.v.snapshot()['pedal'])


if __name__ == '__main__':
    unittest.main()
