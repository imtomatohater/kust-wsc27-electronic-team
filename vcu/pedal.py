"""Accelerator pedal (APPS) processing — pure logic, no CAN / UI.

Kept free of I/O on purpose so it can be ported 1:1 to the MCU VCU later.

    raw mV  ->  range check  ->  travel 0..1  ->  (dual) plausibility
            ->  deadzone / full / curve  ->  brake override  ->  torque command %

Torque command % is MotorCurrentSetpoint % (WS22 torque mode: current ∝ Iq ∝ torque).
"""

DEFAULTS = {
    'enabled': False,
    'can_id': 0x600,
    'timeout_s': 0.15,          # no fresh frame (counter change) for this long -> fault
    'counter_check': True,
    'dual_channel': False,
    'ch1_mV_released': 600, 'ch1_mV_full': 4300,
    'ch2_mV_released': 300, 'ch2_mV_full': 2150,
    'fault_low_mV': 300,        # below = open wire / short to GND
    'fault_high_mV': 4700,      # above = short to supply
    'plausibility_pct': 10.0,   # |travel1 - travel2| above this -> fault (dual only)
    'fault_debounce_s': 0.1,    # range/plausibility must persist this long to latch a fault
    'deadzone_pct': 8.0,        # travel below this -> 0 torque
    'full_pct': 95.0,           # travel above this -> 100 %
    'curve_exp': 1.0,           # 1 = linear, 1.5..2 = softer at low travel
    'max_torque_pct': 100.0,    # pedal 100 % -> this MotorCurrentSetpoint %
    'use_brake_switch': True,
    'rearm_travel_pct': 5.0,    # after brake: pedal must drop below this before torque returns
}


def merged(pc):
    c = dict(DEFAULTS)
    c.update(pc or {})
    return c


def validate(pc):
    """Return list of config problems (empty = OK)."""
    c, e = merged(pc), []
    if not c['enabled']:
        return e
    chans = [('ch1', c['ch1_mV_released'], c['ch1_mV_full'])]
    if c['dual_channel']:
        chans.append(('ch2', c['ch2_mV_released'], c['ch2_mV_full']))
    for n, rel, full in chans:
        if rel == full:
            e.append(f'pedal.{n}: released == full')
        if not (c['fault_low_mV'] < min(rel, full) and max(rel, full) < c['fault_high_mV']):
            e.append(f'pedal.{n}: released/full 은 fault_low_mV~fault_high_mV 안쪽이어야 함')
    if not (0 <= c['deadzone_pct'] < c['full_pct'] <= 100):
        e.append('pedal: 0 <= deadzone_pct < full_pct <= 100')
    if c['curve_exp'] <= 0:
        e.append('pedal: curve_exp > 0')
    if not (0 < c['max_torque_pct'] <= 100):
        e.append('pedal: 0 < max_torque_pct <= 100')
    if not (0 <= c['rearm_travel_pct'] <= c['deadzone_pct']):
        e.append('pedal: rearm_travel_pct <= deadzone_pct')
    if c['timeout_s'] <= 0:
        e.append('pedal: timeout_s > 0')
    return e


class Pedal:
    def __init__(self, pc):
        self.c = merged(pc)
        self.brake_latch = False
        self.bad_since = None           # time a range/plausibility problem first appeared

    @staticmethod
    def travel(mv, rel, full):
        """0 = released, 1 = full. Works for inverted sensors (full < released)."""
        return max(0.0, min(1.0, (mv - rel) / (full - rel)))

    def shape(self, x):
        c = self.c
        dz, fp = c['deadzone_pct'] / 100, c['full_pct'] / 100
        if x <= dz:
            return 0.0
        if x >= fp:
            return 1.0
        return ((x - dz) / (fp - dz)) ** c['curve_exp']

    def evaluate(self, mv1, mv2, brake, alive, now):
        """mv1/mv2: sensor mV (None = never received). alive: frame fresh.
        Returns dict(torque %, travel %, t1/t2 %, fault str|None, note str|None, brake)."""
        c = self.c
        r = dict(torque=0.0, travel=None, t1=None, t2=None, fault=None, note=None,
                 brake=bool(brake))
        if mv1 is None or not alive:
            self.bad_since = None
            r['fault'] = 'CAN 페달 수신 끊김 (페달 노드 응답 없음/카운터 정지)'
            return r

        problem = None
        if not c['fault_low_mV'] <= mv1 <= c['fault_high_mV']:
            problem = f'APPS1 범위 밖 {mv1:.0f} mV (단선/단락)'
        t1 = self.travel(mv1, c['ch1_mV_released'], c['ch1_mV_full'])
        r['t1'], x = t1 * 100, t1
        if c['dual_channel']:
            if mv2 is None or not c['fault_low_mV'] <= mv2 <= c['fault_high_mV']:
                problem = problem or f'APPS2 범위 밖 {0 if mv2 is None else mv2:.0f} mV (단선/단락)'
            else:
                t2 = self.travel(mv2, c['ch2_mV_released'], c['ch2_mV_full'])
                r['t2'] = t2 * 100
                if abs(t1 - t2) * 100 > c['plausibility_pct']:
                    problem = problem or f'APPS1/2 불일치 {t1*100:.0f}% vs {t2*100:.0f}%'
                x = min(t1, t2)            # conservative: use the lower one
        r['travel'] = x * 100

        if problem:                         # torque 0 at once, latch fault after debounce
            if self.bad_since is None:
                self.bad_since = now
            if now - self.bad_since >= c['fault_debounce_s']:
                r['fault'] = problem
            else:
                r['note'] = problem + ' (확인 중)'
            return r
        self.bad_since = None

        if c['use_brake_switch']:
            if brake:
                self.brake_latch = True
            elif self.brake_latch and x * 100 < c['rearm_travel_pct']:
                self.brake_latch = False
            if self.brake_latch:
                r['note'] = '브레이크 → 구동 0' if brake else '브레이크 해제 — 페달을 뗐다가 다시 밟으세요'
                return r

        r['torque'] = self.shape(x) * c['max_torque_pct']
        return r
