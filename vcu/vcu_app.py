"""WS22 VCU — minimal UI.

    python vcu_app.py [--sim] [--monitor] [--config config.json]

    --sim      no hardware: simulated WS22 + motor (+ virtual pedal node if pedal.enabled)
    --monitor  listen-only: shows WS22 + pedal telemetry, never transmits, cannot ARM

Keys:  SPACE / ESC = STOP (current 0, disarm)
"""
import argparse
import tkinter as tk
from tkinter import ttk

from vcu_core import Vcu, FAULT, TORQUE, SPEED, SLIDER, PEDAL, FAULT_TEXT

COLORS = {'DISARMED': '#6b7280', 'ARMED': '#16a34a', 'FAULT': '#dc2626', 'MONITOR': '#2563eb'}

READOUTS = [  # (label, key, fmt)
    ('버스 전압', 'BusVoltage', '{:.1f} V'),
    ('버스 전류', 'BusCurrent', '{:.1f} A'),
    ('전력', '_power', '{:.0f} W'),
    ('모터 속도', 'MotorVelocity', '{:.0f} rpm'),
    ('차속', '_kmh', '{:.1f} km/h'),
    ('상전류', '_iph', '{:.1f} Arms'),
    ('Iq', 'Iq', '{:.1f} A'),
    ('BEMFq', 'BEMFq', '{:.1f} V'),
    ('컨트롤러 온도', 'HeatsinkTemp', '{:.1f} °C'),
    ('모터 온도', 'MotorTemp', '{:.1f} °C'),
    ('누적 Ah', 'DCBusAh', '{:.3f} Ah'),
    ('역기전력/Vbus', '_bemf', '{:.0f} %'),
    ('속도 상한', '_cap', '{:.0f} rpm'),
    ('Ke (V/rpm)', '_ke', '{:.4f}'),
]


class App:
    def __init__(self, root, vcu):
        self.v, self.root = vcu, root
        L = vcu.cfg['limits']
        self.pc = vcu.pc
        self.max_rpm = vcu.cfg['ws22']['max_rpm_setpoint']
        self.regen_max = L['regen_max_pct']
        root.title('WS22 VCU' + (' — MONITOR' if vcu.monitor else '') + (' — SIM' if vcu.sim else ''))
        root.protocol('WM_DELETE_WINDOW', self.quit)
        root.bind('<space>', lambda e: self.stop())
        root.bind('<Escape>', lambda e: self.stop())
        f = ttk.Frame(root, padding=10)
        f.grid(sticky='nsew')

        # state banner
        self.state_lbl = tk.Label(f, text='', font=('Segoe UI', 22, 'bold'), fg='white', width=18)
        self.state_lbl.grid(row=0, column=0, columnspan=4, sticky='ew')
        self.msg_lbl = tk.Label(f, text='', fg='#dc2626', font=('Segoe UI', 10), wraplength=460,
                                justify='left')
        self.msg_lbl.grid(row=1, column=0, columnspan=4, sticky='w', pady=(2, 6))

        # buttons
        self.arm_btn = tk.Button(f, text='ARM', width=9, command=self.arm)
        self.arm_btn.grid(row=2, column=0, pady=2)
        if vcu.monitor:
            self.arm_btn.config(state='disabled')
        tk.Button(f, text='STOP', width=9, bg='#dc2626', fg='white',
                  font=('Segoe UI', 11, 'bold'), command=self.stop).grid(row=2, column=1)
        tk.Button(f, text='FAULT 리셋', width=9, command=self.reset).grid(row=2, column=2)
        self.log_btn = tk.Button(f, text='로그 시작', width=9, command=self.toggle_log)
        self.log_btn.grid(row=2, column=3)
        self.btn_bg = self.log_btn.cget('bg')

        # mode / direction / source
        self.mode = tk.StringVar(value=vcu.mode)
        self.dir = tk.IntVar(value=1)
        self.src = tk.StringVar(value=vcu.source)
        ttk.Radiobutton(f, text='토크(전류 %)', variable=self.mode, value=TORQUE,
                        command=self.on_mode).grid(row=3, column=0, columnspan=2, sticky='w')
        ttk.Radiobutton(f, text='속도(rpm)', variable=self.mode, value=SPEED,
                        command=self.on_mode).grid(row=3, column=2, columnspan=2, sticky='w')
        ttk.Radiobutton(f, text='전진', variable=self.dir, value=1,
                        command=self.on_dir).grid(row=4, column=0, columnspan=2, sticky='w')
        ttk.Radiobutton(f, text='후진', variable=self.dir, value=-1,
                        command=self.on_dir).grid(row=4, column=2, columnspan=2, sticky='w')
        ttk.Radiobutton(f, text='입력: 슬라이더', variable=self.src, value=SLIDER,
                        command=self.on_src).grid(row=5, column=0, columnspan=2, sticky='w')
        rb = ttk.Radiobutton(f, text='입력: CAN 가속페달', variable=self.src, value=PEDAL,
                             command=self.on_src)
        rb.grid(row=5, column=2, columnspan=2, sticky='w')
        if not vcu.pedal:
            rb.state(['disabled'])

        # command slider
        self.cmd_lbl = ttk.Label(f, text='')
        self.cmd_lbl.grid(row=6, column=0, columnspan=4, sticky='w', pady=(8, 0))
        self.scale = tk.Scale(f, orient='horizontal', length=460, resolution=1,
                              command=lambda x: self.v.set_cmd(x))
        self.scale.grid(row=7, column=0, columnspan=4)

        # pedal monitor
        self.pv = {}
        if vcu.pedal:
            pf = ttk.LabelFrame(f, text=f'가속페달 모니터 (CAN 0x{self.pc["can_id"]:03X})', padding=6)
            pf.grid(row=8, column=0, columnspan=4, sticky='ew', pady=(6, 0))
            for i, (name, key) in enumerate([('페달 위치', 'travel'), ('토크 명령', 'torque')]):
                ttk.Label(pf, text=name).grid(row=i, column=0, sticky='w')
                bar = ttk.Progressbar(pf, length=280, maximum=100)
                bar.grid(row=i, column=1, columnspan=2, padx=6, pady=1)
                lb = ttk.Label(pf, text='-', font=('Consolas', 11, 'bold'), width=9, anchor='e')
                lb.grid(row=i, column=3, sticky='e')
                self.pv[key] = (bar, lb)
            for i, (name, key) in enumerate([('APPS1', 'v1'), ('APPS2', 'v2'),
                                             ('브레이크', 'brake'), ('페달 상태', 'status')]):
                r, c = 2 + i // 2, (i % 2) * 2
                ttk.Label(pf, text=name).grid(row=r, column=c, sticky='w')
                lb = ttk.Label(pf, text='-', font=('Consolas', 11, 'bold'))
                lb.grid(row=r, column=c + 1, sticky='w', padx=(4, 12))
                self.pv[key] = lb

        # simulator: virtual pedal node
        if vcu.sim and vcu.pedal:
            sf = ttk.LabelFrame(f, text='시뮬레이터 — 가상 페달 노드', padding=6)
            sf.grid(row=9, column=0, columnspan=4, sticky='ew', pady=(6, 0))
            ttk.Label(sf, text='APPS1 mV').grid(row=0, column=0, sticky='w')
            self.sim_mv = tk.Scale(sf, orient='horizontal', length=360, from_=0, to=5000,
                                   resolution=10, command=lambda x: self.sim_update())
            self.sim_mv.set(self.pc['ch1_mV_released'])
            self.sim_mv.grid(row=0, column=1, columnspan=3)
            self.sim_flags = {}
            for i, (name, key) in enumerate([('브레이크', 'brake'), ('노드 끊기', 'off'),
                                             ('카운터 정지', 'frozen'), ('APPS2 불일치', 'mismatch')]):
                var = tk.BooleanVar(value=False)
                ttk.Checkbutton(sf, text=name, variable=var, command=self.sim_update) \
                    .grid(row=1, column=i, sticky='w')
                self.sim_flags[key] = var
            self.sim_update()

        # readouts
        self.vals = {}
        for i, (name, key, _) in enumerate(READOUTS):
            r, c = 10 + i // 2, (i % 2) * 2
            ttk.Label(f, text=name).grid(row=r, column=c, sticky='w', pady=1)
            lb = ttk.Label(f, text='-', font=('Consolas', 12, 'bold'))
            lb.grid(row=r, column=c + 1, sticky='e', padx=(0, 16))
            self.vals[key] = lb
        self.out_lbl = ttk.Label(f, text='', foreground='#555')
        self.out_lbl.grid(row=30, column=0, columnspan=4, sticky='w', pady=(6, 0))
        self.on_mode(apply=False)
        self.tick()

    # ---- actions ----
    def flash(self, text):
        self.note, self.note_n = text, 15      # show ~1.5 s

    def arm(self):
        err = self.v.arm()
        if err:
            self.flash(err)

    def stop(self):
        self.v.stop()
        self.scale.set(0)

    def reset(self):
        self.scale.set(0)
        err = self.v.reset_fault()
        if err:
            self.flash(err)

    def on_mode(self, apply=True):
        if apply:
            err = self.v.set_mode(self.mode.get())
            if err:
                self.flash(err)
                self.mode.set(self.v.mode)
                return
        self.scale.config(state='normal')
        if self.mode.get() == TORQUE:
            self.scale.config(from_=-self.regen_max, to=100)
            self.cmd_lbl.config(text='명령: 모터 전류 %  (음수 = 회생제동)')
        else:
            self.scale.config(from_=0, to=self.max_rpm)
            self.cmd_lbl.config(text='명령: 목표 속도 rpm')
        self.scale.set(0)
        if self.v.source == PEDAL and self.mode.get() == TORQUE:
            self.cmd_lbl.config(text='명령: CAN 가속페달 (슬라이더 비활성)')
            self.scale.config(state='disabled')

    def on_src(self):
        err = self.v.set_source(self.src.get())
        if err:
            self.flash(err)
            self.src.set(self.v.source)
        self.on_mode(apply=False)

    def on_dir(self):
        err = self.v.set_direction(self.dir.get())
        if err:
            self.flash(err)
            self.dir.set(self.v.direction)

    def sim_update(self):
        pc, fl = self.pc, self.sim_flags
        mv1 = float(self.sim_mv.get())
        x = (mv1 - pc['ch1_mV_released']) / (pc['ch1_mV_full'] - pc['ch1_mV_released'])
        mv2 = pc['ch2_mV_released'] + x * (pc['ch2_mV_full'] - pc['ch2_mV_released'])
        if fl['mismatch'].get():
            mv2 += 0.3 * (pc['ch2_mV_full'] - pc['ch2_mV_released'])
        self.v.sim_pedal(mv1=mv1, mv2=max(0.0, mv2), brake=fl['brake'].get(),
                         on=not fl['off'].get(), frozen=fl['frozen'].get())

    def toggle_log(self):
        if self.v.log.active:
            self.v.log.stop()
            self.log_btn.config(text='로그 시작', bg=self.btn_bg)
        else:
            path = self.v.log.start()
            self.log_btn.config(text='로그 정지', bg='#fde68a')
            self.flash('로그: ' + path)

    def quit(self):
        self.v.close()
        self.root.destroy()

    # ---- 10 Hz refresh ----
    note, note_n = '', 0

    def tick(self):
        s = self.v.snapshot()
        t = s['tel']
        st = s['state'] if s['fresh'] or s['state'] == FAULT else 'NO CAN'
        if s['monitor']:
            st = 'MONITOR' if s['fresh'] else 'MONITOR · NO CAN'
        self.state_lbl.config(text=st, bg=COLORS.get(st, '#f59e0b'))
        if s['state'] == FAULT and self.scale.get() != 0 and str(self.scale.cget('state')) != 'disabled':
            self.scale.set(0)                       # slider follows a trip back to 0

        t['_power'] = t.get('BusVoltage', 0) * t.get('BusCurrent', 0)
        t['_kmh'] = t.get('VehicleVelocity', 0) * 3.6
        t['_iph'] = max(abs(t.get('PhaseCurrentB', 0)), abs(t.get('PhaseCurrentC', 0)))
        if s['bemf'] is not None:
            t['_bemf'] = s['bemf'] * 100
        if s['rpm_cap'] is not None:
            t['_cap'] = s['rpm_cap']
        if s['ke']:
            t['_ke'] = s['ke']
        for _, key, fmt in READOUTS:
            self.vals[key].config(text=fmt.format(t[key]) if key in t else '-')

        po = s['pedal']
        if self.pv and po:
            for key in ('travel', 'torque'):
                bar, lb = self.pv[key]
                val = po[key]
                bar['value'] = 0 if val is None else val
                lb.config(text='-' if val is None else f'{val:.0f} %')
            got = 'Apps1_mV' in t
            self.pv['v1'].config(text=f"{t['Apps1_mV'] / 1000:.2f} V" if got else '-')
            self.pv['v2'].config(text=f"{t['Apps2_mV'] / 1000:.2f} V"
                                 if got and self.pc['dual_channel'] else '사용 안 함')
            self.pv['brake'].config(text='ON' if po['brake'] else 'off',
                                    foreground='#dc2626' if po['brake'] else '')
            if po['fault']:
                self.pv['status'].config(text='고장', foreground='#dc2626')
            elif po['note']:
                self.pv['status'].config(text='제한', foreground='#d97706')
            else:
                self.pv['status'].config(text='정상', foreground='#16a34a')

        msgs = []
        if s['fault']:
            msgs.append(f"FAULT [{FAULT_TEXT.get(s['fault_level'], '')}]: {s['fault']}")
        msgs += s['warn']
        if self.note_n > 0:
            msgs.append(self.note)
            self.note_n -= 1
        self.msg_lbl.config(text='\n'.join(msgs))
        if s['monitor']:
            self.out_lbl.config(text='모니터링 전용 — CAN 송신 없음')
        else:
            self.out_lbl.config(text=f"송신 → vel {s['vel']:.0f} rpm | cur {s['cur']*100:.0f}% | "
                                     f"bus {s['bus']*100:.0f}%  (명령 {s['cmd']:.1f}, {s['source']})")
        self.root.after(100, self.tick)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--sim', action='store_true', help='하드웨어 없이 시뮬레이터로 실행')
    ap.add_argument('--monitor', action='store_true', help='모니터링 전용 (송신 안 함, ARM 불가)')
    ap.add_argument('--config', default=None)
    a = ap.parse_args()
    root = tk.Tk()
    try:
        kw = dict(sim=a.sim, monitor=a.monitor)
        vcu = Vcu(a.config, **kw) if a.config else Vcu(**kw)
    except ValueError as e:                 # config outside WS22 datasheet -> refuse to run
        from tkinter import messagebox
        root.withdraw()
        messagebox.showerror('설정 오류 — 실행 거부', str(e))
        raise SystemExit(1)
    App(root, vcu)
    root.mainloop()
