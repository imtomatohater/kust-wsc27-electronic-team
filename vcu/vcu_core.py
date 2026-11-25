"""VCU core for Prohelion WaveSculptor22 — CAN I/O, protection, logging.

Threads:
  RX   : read CAN -> decode with DBC -> telemetry + raw log
  CTRL : every 1/control_hz s -> protection -> command -> send 0x501/0x502 -> signal log
"""
import csv
import json
import math
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field

from dbc_lite import Database
from pedal import Pedal, merged as pedal_merged, validate as validate_pedal

# HERE     : bundled read-only resources (dbc/, default configs). PyInstaller -> sys._MEIPASS
# DATA_DIR : user-editable configs + logs. Packaged exe -> Documents/WS22_VCU, source run -> HERE
HERE = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
FROZEN = getattr(sys, 'frozen', False)


def _documents_dir():
    """Real 'Documents' folder (may be redirected, e.g. to OneDrive, on Windows)."""
    if sys.platform == 'win32':
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(1024)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:  # CSIDL_PERSONAL
                return buf.value
        except Exception:  # noqa: BLE001
            pass
    return os.path.join(os.path.expanduser('~'), 'Documents')


DATA_DIR = os.path.join(_documents_dir(), 'WS22_VCU') if FROZEN else HERE
DEFAULT_CONFIGS = ('config.json', 'config_bench24v.json')


def ensure_data_dir():
    """Create DATA_DIR and copy default configs there once (never overwrites user edits)."""
    os.makedirs(os.path.join(DATA_DIR, 'logs'), exist_ok=True)
    for name in DEFAULT_CONFIGS:
        dst = os.path.join(DATA_DIR, name)
        src = os.path.join(HERE, name)
        if not os.path.exists(dst) and os.path.exists(src):
            shutil.copyfile(src, dst)
    return DATA_DIR

DISARMED, ARMED, FAULT = 'DISARMED', 'ARMED', 'FAULT'
TORQUE, SPEED = 'TORQUE', 'SPEED'
SLIDER, PEDAL, SCREEN = 'SLIDER', 'PEDAL', 'SCREEN'   # command source (SCREEN = on-screen pedal)
SCREEN_TIMEOUT_S = 0.3                     # on-screen pedal must be refreshed by the UI this often


# ----------------------------------------------------------------- CAN bus
@dataclass
class Frame:
    arbitration_id: int
    data: bytes
    timestamp: float = field(default_factory=time.time)


class RealBus:
    """python-can wrapper (PCAN, Kvaser, IXXAT, slcan, socketcan, ...)."""

    def __init__(self, interface, channel, bitrate):
        import can  # pip install python-can
        self._can = can
        self.bus = can.Bus(interface=interface, channel=channel, bitrate=bitrate)
        self.err_frames = self.ext_frames = 0     # diagnostics: frames we do not use

    def send(self, fid, data):
        self.bus.send(self._can.Message(arbitration_id=fid, data=data, is_extended_id=False))

    def recv(self, timeout):
        m = self.bus.recv(timeout)
        if m is None:
            return None
        if m.is_error_frame:
            self.err_frames += 1
            return None
        if m.is_extended_id:
            self.ext_frames += 1
            return None
        return Frame(m.arbitration_id, bytes(m.data), m.timestamp)

    def state(self):
        """Controller state if the adapter reports it (ACTIVE / PASSIVE / ERROR = bus-off)."""
        try:
            return self.bus.state.name
        except Exception:  # noqa: BLE001 — not every interface supports it
            return None

    def close(self):
        self.bus.shutdown()


class SimBus:
    """Fake WS22 + motor + DC source so the UI/protection can be tested without hardware.
    Uses the same DBC files to encode the broadcast frames.
    sim.bus_v / sim.source_r_ohm in config set the DC source (24 V PSU for the bench config).
    A power supply cannot sink current: regen raises the bus voltage (sim.sink=false)."""

    def __init__(self, rx_db, cmd_db, cfg, pedal_db=None):
        self.rx_db, self.cmd_db, self.pedal_db = rx_db, cmd_db, pedal_db
        sc = cfg.get('sim', {})
        self.v0, self.r_src = sc.get('bus_v', 140.0), sc.get('source_r_ohm', 0.15)
        self.can_sink = sc.get('sink', True)
        self.i_max = cfg['ws22']['max_motor_current_Arms']
        self.ib_lim = cfg['ws22']['bus_current_limit_A']
        self.q, self.lock = [], threading.Lock()
        self.vel_sp = self.cur_sp = 0.0
        self.bus_sp = 1.0
        self.rpm, self.hs_t, self.m_t, self.ah = 0.0, 25.0, 25.0, 0.0
        self.err = 0
        self.v_extra = 0.0            # fault injection (test)
        self.t_extra = 0.0
        self.ke = sc.get('ke_Vpk_per_rpm', 0.075)   # sim motor: V peak phase-neutral per rpm
        self.last_cmd = 0.0
        # virtual pedal node
        self.pedal_mv1, self.pedal_mv2, self.pedal_brake = 600.0, 300.0, 0
        self.pedal_on, self.pedal_frozen, self.pedal_cnt = True, False, 0
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def set_pedal(self, mv1=None, mv2=None, brake=None, on=None, frozen=None):
        if mv1 is not None: self.pedal_mv1 = mv1
        if mv2 is not None: self.pedal_mv2 = mv2
        if brake is not None: self.pedal_brake = int(bool(brake))
        if on is not None: self.pedal_on = on
        if frozen is not None: self.pedal_frozen = frozen

    def send(self, fid, data):
        m = self.cmd_db.messages.get(fid)
        if not m:
            return
        v = m.decode(data)
        if m.name == 'MotorDriveCommand':
            self.vel_sp, self.cur_sp = v['MotorVelocitySetpoint'], v['MotorCurrentSetpoint']
            self.last_cmd = time.time()
        elif m.name == 'MotorPowerCommand':
            self.bus_sp = v['BusCurrentSetpoint']
        elif m.name == 'ResetCommand':
            self.err = 0

    def recv(self, timeout):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                if self.q:
                    return self.q.pop(0)
            time.sleep(0.002)
        return None

    def _put(self, name, db=None, **sig):
        m = (db or self.rx_db).by_name[name]
        with self.lock:
            self.q.append(Frame(m.frame_id, m.encode(sig)))

    def _run(self):
        dt, n = 0.02, 0
        v_bus = self.v0
        while self.running:
            # WS22 behaviour: no command for 250 ms -> stop driving
            cur = self.cur_sp if time.time() - self.last_cmd < 0.25 else 0.0
            kt, J, drag = 0.9, 0.6, 0.004
            iq = cur * self.i_max * (1 if self.vel_sp > self.rpm else -1)
            if abs(self.vel_sp - self.rpm) < 40:       # WS22 velocity loop ~ proportional near setpoint
                iq *= abs(self.vel_sp - self.rpm) / 40
            if iq * self.rpm > 0:                      # motoring: no torque once BEMF reaches Vbus
                iq *= ramp01(SQRT3 * self.ke * abs(self.rpm), 0.85 * v_bus, v_bus)
            w = self.rpm * 2 * math.pi / 60
            p_mech = kt * iq * w
            if p_mech > 0:
                i_bus = p_mech / v_bus / 0.95
                lim = self.bus_sp * self.ib_lim
                if i_bus > lim:                        # WS22 bus current limit
                    iq, i_bus = iq * lim / i_bus, lim
            else:
                i_bus = p_mech * 0.9 / v_bus
            if i_bus >= 0 or self.can_sink:
                v_bus = self.v0 + self.v_extra - self.r_src * i_bus
            else:                                      # PSU: regen charges the DC-link caps
                v_bus += -i_bus * dt * 20.0
            self.rpm += (kt * iq - drag * self.rpm * abs(self.rpm) / 100) / J * dt * 60 / (2 * math.pi)
            self.hs_t += (abs(iq) * 0.004 - (self.hs_t - 25) * 0.002) * dt * 10
            self.m_t += (abs(iq) * 0.006 - (self.m_t - 25) * 0.002) * dt * 10
            self.ah += i_bus * dt / 3600
            n += 1
            if self.pedal_db and self.pedal_on:        # 50 Hz pedal node
                if not self.pedal_frozen:
                    self.pedal_cnt = (self.pedal_cnt + 1) & 0xFF
                self._put('PedalInput', self.pedal_db, Apps1_mV=self.pedal_mv1,
                          Apps2_mV=self.pedal_mv2, BrakeSwitch=self.pedal_brake,
                          PedalCounter=self.pedal_cnt)
            if n % 10 == 0:  # 200 ms
                self._put('Status', ErrorHardwareOverCurrent=self.err, ActiveMotor=0)
                self._put('BusMeasurement', BusVoltage=v_bus, BusCurrent=i_bus)
                self._put('VelocityMeasurement', MotorVelocity=self.rpm,
                          VehicleVelocity=self.rpm * 2 * math.pi / 60 * 0.28)
                ip = abs(iq) / math.sqrt(2)
                self._put('PhaseCurrentMeasurement', PhaseCurrentB=ip, PhaseCurrentC=ip)
                self._put('MotorCurrentVectorMeasurement', Iq=iq, Id=0.0)
                self._put('BackEMFMeasurementPrediction', BEMFq=self.ke * self.rpm, BEMFd=0.0)
            if n % 50 == 0:  # 1 s
                self._put('HeatsinkMotorTempMeasurement', MotorTemp=self.m_t, HeatsinkTemp=self.hs_t + self.t_extra)
                self._put('OdometerBusAhMeasurement', Odometer=0.0, DCBusAh=self.ah)
            time.sleep(dt)

    def close(self):
        self.running = False


# ------------------------------------------------------- datasheet limits
# Prohelion WaveSculptor22 datasheet (PHLN88.003) / product page.
# These are NOT user settings — config.json must stay below them (checked at start).
DATASHEET = {
    'bus_v_max': 160.0,          # V DC, continuous maximum battery voltage
    'motor_i_max': 100.0,        # A rms, maximum motor current
    'bus_i_max': 122.0,          # A DC, instantaneous bus current (drive & regen)
    'heatsink_max': 85.0,        # °C, heatsink kept below this (WS20 datasheet; conservative)
}
MARGIN = {                       # how far below the datasheet our trips must sit
    'bus_v': 5.0,                # V   -> trip_high <= 155 V
    'current': 0.95,             # x   -> phase <= 95 Arms, bus <= 115.9 A
    'heatsink': 5.0,             # °C  -> trip <= 80 °C
}
SQRT3 = math.sqrt(3)


def validate_config(cfg):
    """Return list of problems. VCU refuses to start if any."""
    L, W, ke = cfg['limits'], cfg['ws22'], cfg['motor'].get('ke_Vpk_per_rpm')
    D, M, e = DATASHEET, MARGIN, []

    def need(cond, msg):
        if not cond:
            e.append(msg)

    vmax_ok = D['bus_v_max'] - M['bus_v']
    need(L['bus_v_trip_high'] <= vmax_ok,
         f'bus_v_trip_high {L["bus_v_trip_high"]} > {vmax_ok} V (WS22 {D["bus_v_max"]} V - {M["bus_v"]})')
    need(L['bus_v_trip_low'] < L['bus_v_min'] < L['bus_v_max'] < L['bus_v_trip_high'],
         '전압 순서는 trip_low < min < max < trip_high 이어야 함')
    iph_ok = D['motor_i_max'] * M['current']
    need(W['max_motor_current_Arms'] <= D['motor_i_max'],
         f'max_motor_current_Arms {W["max_motor_current_Arms"]} > WS22 {D["motor_i_max"]} Arms')
    need(L['phase_i_trip_Arms'] <= iph_ok, f'phase_i_trip_Arms > {iph_ok:.1f} Arms')
    ib_ok = D['bus_i_max'] * M['current']
    need(W['bus_current_limit_A'] <= D['bus_i_max'], f'bus_current_limit_A > WS22 {D["bus_i_max"]} A')
    need(L['bus_i_trip_A'] <= ib_ok, f'bus_i_trip_A > {ib_ok:.1f} A')
    need(L['bus_i_max_drive_A'] < L['bus_i_trip_A'], 'bus_i_max_drive_A 는 bus_i_trip_A 보다 작아야 함')
    need(L['bus_i_max_drive_A'] <= W['bus_current_limit_A'],
         'bus_i_max_drive_A 는 bus_current_limit_A 이하여야 함')
    hs_ok = D['heatsink_max'] - M['heatsink']
    need(L['heatsink_trip_C'] <= hs_ok, f'heatsink_trip_C > {hs_ok} °C')
    need(L['heatsink_derate_C'] < L['heatsink_trip_C'], 'heatsink_derate_C < heatsink_trip_C')
    need(L['motor_derate_C'] < L['motor_trip_C'], 'motor_derate_C < motor_trip_C')
    need(L['overspeed_trip_rpm'] > W['max_rpm_setpoint'], 'overspeed_trip_rpm > max_rpm_setpoint')
    need(0 < L['bemf_warn_ratio'] < L['bemf_cap_ratio'] < L['bemf_trip_ratio'] < 1.0,
         '0 < bemf_warn < bemf_cap < bemf_trip < 1')
    if ke:   # back-EMF at max speed must stay below the lowest allowed bus voltage
        v_ll = SQRT3 * ke * W['max_rpm_setpoint']
        lim = L['bemf_cap_ratio'] * L['bus_v_min']
        need(v_ll <= lim, f'max_rpm_setpoint 에서 역기전력 선간피크 {v_ll:.1f} V > '
                          f'{lim:.1f} V (bus_v_min x bemf_cap_ratio)')
    e += validate_pedal(cfg.get('pedal'))
    pc = pedal_merged(cfg.get('pedal'))
    if pc['enabled']:
        ids = cfg['ids']
        ws = range(ids['ws22_base'], ids['ws22_base'] + 0x20)
        dc = range(ids['driver_controls_base'], ids['driver_controls_base'] + 0x20)
        need(pc['can_id'] not in ws and pc['can_id'] not in dc,
             f'pedal.can_id 0x{pc["can_id"]:X} 가 WS22 / Driver controls ID 범위와 겹침')
    return e


# ------------------------------------------------------------- protection
IMMEDIATE, RAMP = 'IMMEDIATE', 'RAMP'
FAULT_TEXT = {IMMEDIATE: '즉시 차단', RAMP: '감속 차단'}


def ramp01(x, ok_below, zero_at):
    """1.0 while x <= ok_below, linearly to 0.0 at x >= zero_at."""
    if zero_at == ok_below:
        return 1.0 if x < zero_at else 0.0
    return max(0.0, min(1.0, (zero_at - x) / (zero_at - ok_below)))


class Protection:
    """Fault classes
      IMMEDIATE : current -> 0 now (WS22 keeps switching, motor coasts). Electrical damage risk.
      RAMP      : current ramps to 0 at fault_ramp_pct_per_s. Thermal / under-voltage / back-EMF.
      (limit)   : no fault, derate current or cap rpm.
    """

    def __init__(self, cfg):
        self.L, self.cfg = cfg['limits'], cfg

    def evaluate(self, t, armed, ke, v_ref=None):
        L, warn = self.L, []
        v, ib = t.get('BusVoltage'), t.get('BusCurrent')
        rpm = abs(t.get('MotorVelocity', 0.0))
        iph = max(abs(t.get('PhaseCurrentB', 0.0)), abs(t.get('PhaseCurrentC', 0.0)))
        hs, mt = t.get('HeatsinkTemp', 25.0), t.get('MotorTemp', 25.0)
        max_rpm = self.cfg['ws22']['max_rpm_setpoint']
        r = dict(trip=None, drive=1.0, regen=1.0, rpm_cap=max_rpm, bemf=None, warn=warn)

        # back-EMF ratio = line-line peak BEMF / bus voltage  (>= 1 -> uncontrolled rectification)
        if v and v > 1:
            cand = []
            if 'BEMFq' in t:
                cand.append(SQRT3 * abs(t['BEMFq']) / v)
            if ke:
                cand.append(SQRT3 * ke * rpm / v)
                vr = min(v, v_ref) if v_ref else v   # conservative: never use a regen-raised V
                r['rpm_cap'] = min(max_rpm, L['bemf_cap_ratio'] * vr / (SQRT3 * ke))
            if cand:
                r['bemf'] = max(cand)

        def trip(level, msg):
            r.update(trip=(level, msg), drive=0.0, regen=0.0)
            return r

        # ---- IMMEDIATE ----
        errs = [k for k, val in t.items() if k.startswith('Error') and val
                and k not in L['ignore_errors']]
        if errs:
            return trip(IMMEDIATE, 'WS22 에러: ' + ', '.join(errs))
        if v is not None and v > L['bus_v_trip_high']:
            return trip(IMMEDIATE, f'과전압 {v:.1f} V > {L["bus_v_trip_high"]} V')
        if ib is not None and abs(ib) > L['bus_i_trip_A']:
            return trip(IMMEDIATE, f'버스 과전류 {ib:.1f} A > {L["bus_i_trip_A"]} A')
        if iph > L['phase_i_trip_Arms']:
            return trip(IMMEDIATE, f'상 과전류 {iph:.1f} > {L["phase_i_trip_Arms"]} Arms')
        if rpm > L['overspeed_trip_rpm']:
            return trip(IMMEDIATE, f'과속 {rpm:.0f} > {L["overspeed_trip_rpm"]} rpm')

        # ---- RAMP ----
        if armed and v is not None and v < L['bus_v_trip_low']:
            return trip(RAMP, f'저전압 {v:.1f} V < {L["bus_v_trip_low"]} V')
        if hs > L['heatsink_trip_C']:
            return trip(RAMP, f'컨트롤러 과열 {hs:.1f} °C')
        if mt > L['motor_trip_C']:
            return trip(RAMP, f'모터 과열 {mt:.1f} °C')
        if r['bemf'] is not None and r['bemf'] > L['bemf_trip_ratio']:
            return trip(RAMP, f'역기전력 여유 부족 {r["bemf"]*100:.0f}% of Vbus — 기계식 브레이크로 감속')

        # ---- limit / derate ----
        band = L['bus_v_derate_band']
        if v is not None:
            r['drive'] = ramp01(-v, -(L['bus_v_min'] + band), -L['bus_v_min'])
            r['regen'] = ramp01(v, L['bus_v_max'] - band, L['bus_v_max'])
        tsc = min(ramp01(hs, L['heatsink_derate_C'], L['heatsink_trip_C']),
                  ramp01(mt, L['motor_derate_C'], L['motor_trip_C']))
        r['drive'], r['regen'] = min(r['drive'], tsc), min(r['regen'], tsc)
        if r['drive'] < 1: warn.append(f'구동 제한 {r["drive"]*100:.0f}%')
        if r['regen'] < 1: warn.append(f'회생 제한 {r["regen"]*100:.0f}%')
        if r['bemf'] is not None and r['bemf'] > L['bemf_warn_ratio']:
            warn.append(f'역기전력 {r["bemf"]*100:.0f}% of Vbus')
        if r['rpm_cap'] < max_rpm:
            warn.append(f'속도 상한 {r["rpm_cap"]:.0f} rpm (역기전력)')
        if not ke:
            warn.append('Ke 미확정 — 역기전력 속도상한 비활성 (주행하며 학습 중)')
        warn += [k.replace('Limit', 'WS22 제한: ') for k, val in t.items()
                 if k.startswith('Limit') and k != 'LimitReserved' and val]
        return r


# ---------------------------------------------------------------- logging
class Logger:
    def __init__(self, signal_names):
        self.cols = ['t_s', 'state', 'mode', 'cmd', 'vel_sp_rpm', 'cur_sp', 'bus_sp',
                     'drive_scale', 'regen_scale', 'fault'] + signal_names
        self.f_sig = self.f_raw = None
        self.lock = threading.Lock()

    @property
    def active(self):
        return self.f_sig is not None

    def start(self):
        os.makedirs(os.path.join(DATA_DIR, 'logs'), exist_ok=True)
        stamp = time.strftime('%Y%m%d_%H%M%S')
        base = os.path.join(DATA_DIR, 'logs', stamp)
        self.t0 = time.time()
        with self.lock:
            self.f_sig = open(base + '_signals.csv', 'w', newline='', encoding='utf-8-sig')
            self.f_raw = open(base + '_raw.csv', 'w', newline='', encoding='utf-8-sig')
            self.w_sig, self.w_raw = csv.writer(self.f_sig), csv.writer(self.f_raw)
            self.w_sig.writerow(self.cols)
            self.w_raw.writerow(['t_s', 'id_hex', 'msg', 'data_hex', 'dir'])
        return base

    def stop(self):
        with self.lock:
            for f in (self.f_sig, self.f_raw):
                if f: f.close()
            self.f_sig = self.f_raw = None

    def raw(self, ts, fid, name, data, direction):
        with self.lock:
            if self.f_raw:
                self.w_raw.writerow([f'{ts - self.t0:.4f}', f'{fid:03X}', name, data.hex(' '), direction])

    def row(self, snap):
        with self.lock:
            if self.f_sig:
                snap['t_s'] = f'{time.time() - self.t0:.3f}'
                self.w_sig.writerow([snap.get(c, '') for c in self.cols])
                self.f_sig.flush()



# ------------------------------------------------------------------- VCU
class Vcu:
    def __init__(self, cfg_path=None, sim=False, monitor=False):
        cfg_path = cfg_path or os.path.join(DATA_DIR, 'config.json')
        self.cfg_path = cfg_path
        with open(cfg_path, encoding='utf-8') as f:
            self.cfg = json.load(f)
        problems = validate_config(self.cfg)
        if problems:
            raise ValueError('config.json 이 WS22 데이터시트 한계/규칙을 벗어남:\n - '
                             + '\n - '.join(problems))
        ids = self.cfg['ids']
        self.rx_db = Database().load(os.path.join(HERE, 'dbc', 'ws22.dbc')) \
            .remap(ids['ws22_dbc_base'], ids['ws22_base'])
        self.cmd_db = Database().load(os.path.join(HERE, 'dbc', 'ws22_drive_cmd.dbc')) \
            .remap(ids['drive_cmd_dbc_base'], ids['driver_controls_base'])
        self.m_drive = self.cmd_db.by_name['MotorDriveCommand']
        self.m_power = self.cmd_db.by_name['MotorPowerCommand']
        self.m_reset = self.cmd_db.by_name['ResetCommand']

        # CAN accelerator pedal (optional)
        self.pc = pedal_merged(self.cfg.get('pedal'))
        self.pedal_db = self.pedal = None
        if self.pc['enabled']:
            self.pedal_db = Database().load(os.path.join(HERE, 'dbc', 'vcu_pedal.dbc')) \
                .remap(0x600, self.pc['can_id'])
            self.pedal = Pedal(self.pc)
        self.pedal_out = self.pedal_can_out = None
        self.pedal_cnt, self.pedal_alive_t = None, 0.0
        # on-screen pedal: same calibration/processing as the CAN pedal, fed by the UI
        self.spedal = Pedal(self.pc)
        self.screen = None                # (mv1, mv2, brake, time)

        self.diag = dict(rx=0, rx_known=0, tx=0, tx_err=0, rx_err=0, last_err='',
                         ids={}, t0=time.time())
        self._rate = (time.time(), 0, 0)
        self.monitor = monitor            # listen-only: never transmits, cannot ARM
        self.sim = sim
        c = self.cfg['can']
        self.bus = SimBus(self.rx_db, self.cmd_db, self.cfg, self.pedal_db) if sim else \
            RealBus(c['interface'], c['channel'], c['bitrate'])

        self.prot = Protection(self.cfg)
        sig_names = [s.name for m in self.rx_db.messages.values() for s in m.signals]
        if self.pedal_db:
            sig_names += [s.name for m in self.pedal_db.messages.values() for s in m.signals]
        self.log = Logger(sig_names + ['bemf_ratio', 'rpm_cap', 'ke_used', 'fault_level',
                                       'source', 'pedal_travel_pct', 'pedal_torque_pct',
                                       'pedal_fault'])

        self.ke_cfg = self.cfg['motor'].get('ke_Vpk_per_rpm')
        self.ke_learn = None              # learned from BEMFq / rpm if not configured
        self.v_ref = None                 # bus V for rpm cap: follows drops instantly, rises slowly
        self.lock = threading.Lock()
        self.tel, self.rx_time = {}, {}
        self.state, self.fault, self.fault_level = DISARMED, '', ''
        self.mode, self.direction = TORQUE, 1
        self.source = PEDAL if self.pedal else SLIDER
        self.cmd = 0.0                    # torque: -regen..100 %, speed: rpm
        self.cur_out, self.vel_hold = 0.0, 0.0
        self.out = dict(vel=0.0, cur=0.0, bus=0.0, drive=1.0, regen=1.0, warn=[],
                        bemf=None, rpm_cap=None, ke=None)
        self.running = True
        threading.Thread(target=self._rx_loop, daemon=True).start()
        threading.Thread(target=self._ctrl_loop, daemon=True).start()

    # ---------- operator actions (called from UI) ----------
    def arm(self):
        with self.lock:
            if self.monitor:
                return '모니터링 전용 모드 — ARM 불가 (송신 안 함)'
            if self.state == FAULT:
                return '먼저 FAULT 리셋'
            if not self._fresh():
                return 'WS22 텔레메트리 수신 안됨'
            v = self.tel.get('BusVoltage', 0)
            L = self.cfg['limits']
            if not (L['bus_v_trip_low'] < v < L['bus_v_trip_high']):
                return f'버스 전압 {v:.1f} V 범위 밖'
            if self._pedal_active():
                po = self.pedal_out
                if po is None or po['fault']:
                    return '페달 이상: ' + (po['fault'] if po else '수신 전')
                if po['torque'] > 0 or (po['travel'] or 0) > self.pc['rearm_travel_pct']:
                    return '페달을 떼고 ARM'
            elif self.cmd != 0:
                return '명령을 0으로 놓고 ARM'
            self.state, self.cur_out = ARMED, 0.0
        return None

    def stop(self):
        with self.lock:
            self.cmd, self.cur_out = 0.0, 0.0
            if self.state == ARMED:
                self.state = DISARMED

    def reset_fault(self):
        with self.lock:
            if self.state != FAULT:
                return
            if self.fault_level == RAMP and self.cur_out > 0:
                return '감속 차단 진행 중 — 전류 0 이후 리셋'
            self.state, self.fault, self.fault_level, self.cmd = DISARMED, '', '', 0.0
        self._send(self.m_reset, {})       # clears latched WS22 errors

    def set_cmd(self, value):
        """Slider command. Ignored while the CAN pedal is the command source."""
        with self.lock:
            if not self._pedal_active():
                self.cmd = float(value)

    def set_mode(self, mode):
        with self.lock:
            if self.state == ARMED:
                return 'DISARM 상태에서만 모드 변경'
            if mode == SPEED and self.source in (PEDAL, SCREEN):
                return '페달 입력은 토크 모드 전용 — 입력을 슬라이더로 바꾼 뒤 속도 모드'
            self.mode, self.cmd = mode, 0.0

    def set_source(self, src):
        with self.lock:
            if self.state == ARMED:
                return 'DISARM 상태에서만 입력 변경'
            if src == PEDAL and not self.pedal:
                return 'config.json 의 pedal.enabled 가 false'
            if src in (PEDAL, SCREEN) and self.mode != TORQUE:
                return '페달 입력은 토크 모드에서만'
            self.source, self.cmd = src, 0.0

    def screen_pedal(self, travel_pct, brake=False):
        """On-screen pedal position from the UI (0..100 % travel). Converted to sensor mV with
        the configured calibration so it runs through exactly the same Pedal processing."""
        c = self.pc
        x = max(0.0, min(1.0, travel_pct / 100))
        mv1 = c['ch1_mV_released'] + x * (c['ch1_mV_full'] - c['ch1_mV_released'])
        mv2 = c['ch2_mV_released'] + x * (c['ch2_mV_full'] - c['ch2_mV_released'])
        with self.lock:
            self.screen = (mv1, mv2, bool(brake), time.time())

    def sim_pedal(self, **kw):
        """Simulator only: drive the virtual pedal node (mv1, mv2, brake, on, frozen)."""
        if self.sim and hasattr(self.bus, 'set_pedal'):
            self.bus.set_pedal(**kw)

    def set_direction(self, d):
        with self.lock:
            if self.state == ARMED or abs(self.tel.get('MotorVelocity', 0)) > 10:
                return '정지·DISARM 상태에서만 방향 변경'
            self.direction = 1 if d >= 0 else -1

    def snapshot(self):
        with self.lock:
            return dict(tel=dict(self.tel), state=self.state, fault=self.fault,
                        fault_level=self.fault_level, mode=self.mode, source=self.source,
                        direction=self.direction, cmd=self.cmd, fresh=self._fresh(),
                        monitor=self.monitor, pedal=self.pedal_out, pedal_can=self.pedal_can_out,
                        pedal_enabled=self.pedal is not None, can=self.can_diag(), **self.out)

    def close(self):
        self.stop()
        time.sleep(0.15)
        self.running = False
        self.log.stop()
        self.bus.close()

    # ---------- internals ----------
    def _fresh(self, names=('Status', 'BusMeasurement')):
        now, to = time.time(), self.cfg['limits']['rx_timeout_s']
        return all(now - self.rx_time.get(n, 0) < to for n in names)

    # WS22 broadcast offsets from its base address (see dbc/ws22.dbc)
    WS22_OFFSETS = (0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0B, 0x0C,
                    0x0E, 0x17)

    def can_diag(self):
        """CAN health + plain-language hints. Called with self.lock held (from snapshot)."""
        d, c, ids_cfg = self.diag, self.cfg['can'], self.cfg['ids']
        now = time.time()
        t_prev, rx_prev, tx_prev = self._rate
        dt = now - t_prev
        if dt >= 1.0:
            self._rate = (now, d['rx'], d['tx'])
            self._rx_fps = (d['rx'] - rx_prev) / dt
            self._tx_fps = (d['tx'] - tx_prev) / dt
        rx_fps, tx_fps = getattr(self, '_rx_fps', 0.0), getattr(self, '_tx_fps', 0.0)
        real = isinstance(self.bus, RealBus)
        st = self.bus.state() if real else None
        errf = self.bus.err_frames if real else 0
        extf = self.bus.ext_frames if real else 0
        base_cfg = ids_cfg['ws22_base']
        known = set(self.rx_db.messages) | set(self.pedal_db.messages if self.pedal_db else ())
        tx_ids = set(self.cmd_db.messages)
        seen = d['ids']
        unknown = sorted(i for i in seen if i not in known and i not in tx_ids)

        hints = []
        up = now - d['t0']
        if self.sim:
            hints.append('시뮬레이터 모드 — 실제 CAN 어댑터를 쓰지 않습니다. 시작 창에서 "실제 CAN 구동" 또는 "모니터링"을 고르세요.')
        if self.monitor:
            hints.append('모니터링 모드 — 설계상 송신하지 않습니다 (수신만).')
        if real and st in ('PASSIVE', 'ERROR'):
            hints.append(f'버스 상태 {st}: bitrate 불일치, 종단저항(120 Ω x 2) 누락, CAN_H/L 반대 연결 가능성이 큽니다.')
        if real and errf:
            hints.append(f'에러 프레임 {errf}개: bitrate({c["bitrate"]})가 WS22 설정과 같은지, 배선/종단을 확인하세요.')
        if real and up > 2 and d['rx'] == 0 and not errf:
            hints.append('수신 프레임 0개: WS22 전원(12 V/버스), CAN_H/L/GND 배선, 종단저항, '
                         f'어댑터 채널({c["channel"]}), bitrate({c["bitrate"]})를 확인하세요. '
                         'PCAN-View 같은 다른 프로그램에서는 보이는지도 확인해 보세요.')
        if d['rx'] and not d['rx_known']:
            hints.append('프레임은 들어오는데 설정된 WS22 ID가 아닙니다 (아래 수신 ID 참고).')
        # guess the WS22 base address from what is on the bus
        best = None
        for i in seen:
            for off in (0x00, 0x01, 0x02, 0x03):
                b = i - off
                if b < 0:
                    continue
                score = sum(1 for o in self.WS22_OFFSETS if b + o in seen)
                if score >= 4 and (best is None or score > best[1]):
                    best = (b, score)
        if best and best[0] != base_cfg:
            hints.append(f'WS22 형태의 프레임이 0x{best[0]:03X} 기준으로 들어옵니다. '
                         f'설정은 0x{base_cfg:03X} → config의 ids.ws22_base 를 {best[0]} 으로 바꾸세요.')
        dc = ids_cfg['driver_controls_base']
        if dc + 1 in seen:
            hints.append(f'다른 장치가 0x{dc + 1:03X}(구동 명령)을 보내고 있습니다. Profinity 등에서 구동 명령을 끄세요.')
        if extf:
            hints.append(f'확장 ID(29-bit) 프레임 {extf}개 수신 — WS22는 표준 ID(11-bit)를 씁니다.')
        if d['tx_err']:
            hints.append('송신 실패: ' + d['last_err'])
        elif d['rx_err']:
            hints.append('수신 오류: ' + d['last_err'])
        if real and self._fresh() and not hints:
            hints.append('정상 — WS22 텔레메트리 수신 중.')
        top = sorted(seen.items(), key=lambda kv: -kv[1])[:10]
        guess = best[0] if best and best[0] != base_cfg else None
        return dict(base_cfg=base_cfg, base_guess=guess, real=real, iface=f'{c["interface"]} / {c["channel"]} / {c["bitrate"]} bps',
                    state=st, rx=d['rx'], rx_known=d['rx_known'], tx=d['tx'], tx_err=d['tx_err'],
                    err_frames=errf, rx_fps=rx_fps, tx_fps=tx_fps,
                    ids=[(i, n, i in known or i in tx_ids) for i, n in top],
                    unknown=unknown, hints=hints)

    def apply_ws22_base(self, new_base, save=True):
        """Switch the WS22 broadcast base address live (DISARMED only) and save it to the
        config file the program was started with. Returns an error string or None."""
        with self.lock:
            if self.state == ARMED:
                return 'DISARM 상태에서만 주소 변경'
            old = self.cfg['ids']['ws22_base']
            if new_base == old:
                return None
            if self.pedal and self.pc['can_id'] in range(new_base, new_base + 0x20):
                return f'새 주소 0x{new_base:03X} 범위가 페달 ID 0x{self.pc["can_id"]:03X}와 겹침'
            self.rx_db.remap(old, new_base)
            self.cfg['ids']['ws22_base'] = new_base
            for name in [n for n in self.rx_time if n != 'PedalInput']:
                del self.rx_time[name]
        if save:
            try:
                with open(self.cfg_path, encoding='utf-8') as f:
                    raw = json.load(f)
                raw['ids']['ws22_base'] = new_base
                with open(self.cfg_path, 'w', encoding='utf-8') as f:
                    json.dump(raw, f, ensure_ascii=False, indent=2)
            except Exception as e:  # noqa: BLE001
                return f'적용은 됐지만 설정 파일 저장 실패: {e}'
        return None

    def _pedal_active(self):
        if self.mode != TORQUE:
            return False
        return self.source == SCREEN or (self.source == PEDAL and self.pedal is not None)

    def _pedal_alive(self, now):
        to = self.pc['timeout_s']
        if self.pc['counter_check']:
            return now - self.pedal_alive_t < to
        return now - self.rx_time.get('PedalInput', 0) < to

    def _send(self, msg, values):
        if self.monitor:
            return
        data = msg.encode(values)
        try:
            self.bus.send(msg.frame_id, data)
            self.diag['tx'] += 1
        except Exception as e:  # noqa: BLE001
            with self.lock:
                self.diag['tx_err'] += 1
                self.diag['last_err'] = f'송신: {type(e).__name__}: {e}'
                self._trip(IMMEDIATE, f'CAN 송신 오류: {e}')
        if self.log.active:
            self.log.raw(time.time(), msg.frame_id, msg.name, data, 'TX')

    def _trip(self, level, reason):
        """Latch fault. IMMEDIATE always wins over RAMP (escalation)."""
        if self.state == FAULT and (self.fault_level == IMMEDIATE or level == RAMP):
            return
        if self.state == FAULT:                       # RAMP -> IMMEDIATE escalation
            reason = f'{reason}  (감속 차단 중 격상: {self.fault})'
        self.state, self.fault, self.fault_level, self.cmd = FAULT, reason, level, 0.0
        if level == IMMEDIATE:
            self.cur_out = 0.0

    def _learn_ke(self):
        t = self.tel
        rpm = abs(t.get('MotorVelocity', 0.0))
        if rpm > self.cfg['motor'].get('ke_learn_min_rpm', 150) and 'BEMFq' in t and self._fresh(('BackEMFMeasurementPrediction',
                                                       'VelocityMeasurement')):
            k = abs(t['BEMFq']) / rpm
            self.ke_learn = k if self.ke_learn is None else 0.98 * self.ke_learn + 0.02 * k

    def _rx_loop(self):
        while self.running:
            try:
                fr = self.bus.recv(0.05)
            except Exception as e:  # noqa: BLE001
                self.diag['rx_err'] += 1
                self.diag['last_err'] = f'수신: {type(e).__name__}: {e}'
                time.sleep(0.05)
                continue
            if fr is None:
                continue
            d = self.diag
            d['rx'] += 1
            d['ids'][fr.arbitration_id] = d['ids'].get(fr.arbitration_id, 0) + 1
            m = self.rx_db.messages.get(fr.arbitration_id)
            if m is None and self.pedal_db:
                m = self.pedal_db.messages.get(fr.arbitration_id)
            if m is None:
                if self.log.active:
                    self.log.raw(time.time(), fr.arbitration_id, '?', fr.data, 'RX')
                continue
            d['rx_known'] += 1
            vals = m.decode(fr.data)
            now = time.time()
            with self.lock:
                self.tel.update(vals)
                self.rx_time[m.name] = now
                if m.name == 'PedalInput' and vals['PedalCounter'] != self.pedal_cnt:
                    self.pedal_cnt, self.pedal_alive_t = vals['PedalCounter'], now
            if self.log.active:
                self.log.raw(time.time(), fr.arbitration_id, m.name, fr.data, 'RX')

    def _ctrl_loop(self):
        cfg, L, W = self.cfg, self.cfg['limits'], self.cfg['ws22']
        dt = 1.0 / cfg['loop']['control_hz']
        log_every = max(1, round(cfg['loop']['control_hz'] / cfg['loop']['log_hz']))
        # runtime clamps derived from datasheet-checked config
        cur_cap = min(1.0, 0.9 * L['phase_i_trip_Arms'] / W['max_motor_current_Arms'])
        bus_frac = min(1.0, L['bus_i_max_drive_A'] / W['bus_current_limit_A'])
        n, nxt = 0, time.time()
        while self.running:
            with self.lock:
                self._learn_ke()
                ke = self.ke_cfg or self.ke_learn
                v_now = self.tel.get('BusVoltage')
                if v_now is not None:
                    if self.v_ref is None or v_now < self.v_ref:
                        self.v_ref = v_now
                    else:
                        self.v_ref += (v_now - self.v_ref) * dt / 5.0   # ~5 s time constant
                fresh = self._fresh()
                armed = self.state == ARMED
                p = self.prot.evaluate(self.tel, armed, ke, self.v_ref) if self.tel else \
                    dict(trip=None, drive=1.0, regen=1.0, rpm_cap=W['max_rpm_setpoint'],
                         bemf=None, warn=[])
                if armed and not fresh:
                    p['trip'] = (IMMEDIATE, 'CAN 수신 타임아웃 (WS22 응답 없음)')
                now, t = time.time(), self.tel
                self.pedal_can_out = None
                if self.pedal:                         # evaluated always, for monitoring
                    got = 'PedalInput' in self.rx_time
                    self.pedal_can_out = self.pedal.evaluate(
                        t.get('Apps1_mV') if got else None, t.get('Apps2_mV') if got else None,
                        t.get('BrakeSwitch', 0), self._pedal_alive(now), now)
                if self.source == SCREEN:
                    sc = self.screen
                    alive = sc is not None and now - sc[3] < SCREEN_TIMEOUT_S
                    po = self.spedal.evaluate(sc[0] if sc else None, sc[1] if sc else None,
                                              sc[2] if sc else 0, alive, now)
                    if po['fault'] and not alive:
                        po['fault'] = '화면 페달 응답 없음 (UI 멈춤)'
                else:
                    po = self.pedal_can_out
                self.pedal_out = po
                if po:
                    if self._pedal_active():
                        self.cmd = po['torque']
                        if po['fault'] and armed and not p['trip']:
                            p['trip'] = (IMMEDIATE, '페달 고장: ' + po['fault'])
                    if po['fault']:
                        p['warn'] = p['warn'] + ['페달: ' + po['fault']]
                    elif po['note']:
                        p['warn'] = p['warn'] + ['페달: ' + po['note']]
                if p['trip']:
                    self._trip(*p['trip'])
                cap = p['rpm_cap']

                vel, cur_tgt = 0.0, 0.0
                if self.state == ARMED:
                    if self.mode == TORQUE:
                        x = self.cmd / 100.0
                        if x >= 0:   # drive: velocity = ceiling, current % = torque
                            vel, cur_tgt = self.direction * cap, x * p['drive']
                        else:        # regen: velocity 0 -> braking torque
                            vel = 0.0
                            cur_tgt = min(-x, L['regen_max_pct'] / 100) * p['regen']
                    else:            # speed (cruise) mode
                        vel = self.direction * min(abs(self.cmd), cap)
                        cur_tgt = L['speed_mode_current_pct'] / 100 * min(p['drive'], p['regen'])
                    self.vel_hold = vel
                    step = L['current_ramp_pct_per_s'] / 100 * dt
                    self.cur_out = min(cur_tgt, self.cur_out + step)   # up: slew, down: instant
                elif self.state == FAULT and self.fault_level == RAMP:
                    vel = self.vel_hold                                 # keep direction/ceiling
                    self.cur_out = max(0.0, self.cur_out - L['fault_ramp_pct_per_s'] / 100 * dt)
                else:
                    self.cur_out = 0.0
                cur = max(0.0, min(cur_cap, self.cur_out))
                if cur == 0.0:
                    vel = 0.0
                bus = bus_frac if cur > 0 else 0.0
                self.out = dict(vel=vel, cur=cur, bus=bus, drive=p['drive'], regen=p['regen'],
                                warn=p['warn'], bemf=p['bemf'], rpm_cap=cap, ke=ke)
                snap = dict(self.tel, state=self.state, mode=self.mode, cmd=self.cmd,
                            vel_sp_rpm=round(vel, 1), cur_sp=round(cur, 4), bus_sp=round(bus, 4),
                            drive_scale=round(p['drive'], 3), regen_scale=round(p['regen'], 3),
                            fault=self.fault, fault_level=self.fault_level,
                            bemf_ratio='' if p['bemf'] is None else round(p['bemf'], 3),
                            rpm_cap=round(cap, 1), ke_used='' if not ke else f'{ke:.5f}',
                            source=self.source)
                po = self.pedal_out
                if po:
                    snap.update(pedal_travel_pct='' if po['travel'] is None else round(po['travel'], 1),
                                pedal_torque_pct=round(po['torque'], 1),
                                pedal_fault=po['fault'] or '')

            self._send(self.m_drive, dict(MotorVelocitySetpoint=vel, MotorCurrentSetpoint=cur))
            self._send(self.m_power, dict(BusCurrentSetpoint=bus))
            n += 1
            if self.log.active and n % log_every == 0:
                self.log.row(snap)
            nxt += dt
            time.sleep(max(0.0, nxt - time.time()))
            if time.time() - nxt > 0.5:
                nxt = time.time()
