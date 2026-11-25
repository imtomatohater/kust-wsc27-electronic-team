"""WS22 VCU — minimal UI.   python vcu_app.py [--sim] [--config config.json]

Keys:  SPACE / ESC = STOP (current 0, disarm)
"""
import argparse
import tkinter as tk
from tkinter import ttk

from vcu_core import Vcu, ARMED, FAULT, TORQUE, SPEED, FAULT_TEXT

COLORS = {'DISARMED': '#6b7280', 'ARMED': '#16a34a', 'FAULT': '#dc2626'}

READOUTS = [  # (label, key, fmt)
    ('버스 전압', 'BusVoltage', '{:.1f} V'),
    ('버스 전류', 'BusCurrent', '{:.1f} A'),
    ('전력', '_power', '{:.0f} W'),
    ('모터 속도', 'MotorVelocity', '{:.0f} rpm'),
    ('차속', '_kmh', '{:.1f} km/h'),
    ('상전류', '_iph', '{:.1f} Arms'),
    ('컨트롤러 온도', 'HeatsinkTemp', '{:.1f} °C'),
    ('모터 온도', 'MotorTemp', '{:.1f} °C'),
    ('누적 Ah', 'DCBusAh', '{:.3f} Ah'),
    ('역기전력/Vbus', '_bemf', '{:.0f} %'),
    ('속도 상한', '_cap', '{:.0f} rpm'),
]


class App:
    def __init__(self, root, vcu):
        self.v, self.root = vcu, root
        L = vcu.cfg['limits']
        self.max_rpm = vcu.cfg['ws22']['max_rpm_setpoint']
        self.regen_max = L['regen_max_pct']
        root.title('WS22 VCU')
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
        tk.Button(f, text='ARM', width=9, command=self.arm).grid(row=2, column=0, pady=2)
        tk.Button(f, text='STOP', width=9, bg='#dc2626', fg='white',
                  font=('Segoe UI', 11, 'bold'), command=self.stop).grid(row=2, column=1)
        tk.Button(f, text='FAULT 리셋', width=9, command=self.reset).grid(row=2, column=2)
        self.log_btn = tk.Button(f, text='로그 시작', width=9, command=self.toggle_log)
        self.log_btn.grid(row=2, column=3)
        self.btn_bg = self.log_btn.cget('bg')

        # mode / direction
        self.mode = tk.StringVar(value=TORQUE)
        self.dir = tk.IntVar(value=1)
        ttk.Radiobutton(f, text='토크(전류 %)', variable=self.mode, value=TORQUE,
                        command=self.on_mode).grid(row=3, column=0, columnspan=2, sticky='w')
        ttk.Radiobutton(f, text='속도(rpm)', variable=self.mode, value=SPEED,
                        command=self.on_mode).grid(row=3, column=2, columnspan=2, sticky='w')
        ttk.Radiobutton(f, text='전진', variable=self.dir, value=1,
                        command=self.on_dir).grid(row=4, column=0, columnspan=2, sticky='w')
        ttk.Radiobutton(f, text='후진', variable=self.dir, value=-1,
                        command=self.on_dir).grid(row=4, column=2, columnspan=2, sticky='w')

        # command slider
        self.cmd_lbl = ttk.Label(f, text='')
        self.cmd_lbl.grid(row=5, column=0, columnspan=4, sticky='w', pady=(8, 0))
        self.scale = tk.Scale(f, orient='horizontal', length=460, resolution=1,
                              command=lambda x: self.v.set_cmd(x))
        self.scale.grid(row=6, column=0, columnspan=4)
        self.on_mode(apply=False)

        # readouts
        self.vals = {}
        for i, (name, key, _) in enumerate(READOUTS):
            r, c = 7 + i // 2, (i % 2) * 2
            ttk.Label(f, text=name).grid(row=r, column=c, sticky='w', pady=1)
            lb = ttk.Label(f, text='-', font=('Consolas', 12, 'bold'))
            lb.grid(row=r, column=c + 1, sticky='e', padx=(0, 16))
            self.vals[key] = lb
        self.out_lbl = ttk.Label(f, text='', foreground='#555')
        self.out_lbl.grid(row=20, column=0, columnspan=4, sticky='w', pady=(6, 0))
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
        if self.mode.get() == TORQUE:
            self.scale.config(from_=-self.regen_max, to=100)
            self.cmd_lbl.config(text='명령: 모터 전류 %  (음수 = 회생제동)')
        else:
            self.scale.config(from_=0, to=self.max_rpm)
            self.cmd_lbl.config(text='명령: 목표 속도 rpm')
        self.scale.set(0)

    def on_dir(self):
        err = self.v.set_direction(self.dir.get())
        if err:
            self.flash(err)
            self.dir.set(self.v.direction)

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
        self.state_lbl.config(text=st, bg=COLORS.get(st, '#f59e0b'))
        if s['state'] == FAULT and self.scale.get() != 0:
            self.scale.set(0)                       # slider follows a trip back to 0

        t['_power'] = t.get('BusVoltage', 0) * t.get('BusCurrent', 0)
        t['_kmh'] = t.get('VehicleVelocity', 0) * 3.6
        t['_iph'] = max(abs(t.get('PhaseCurrentB', 0)), abs(t.get('PhaseCurrentC', 0)))
        if s['bemf'] is not None:
            t['_bemf'] = s['bemf'] * 100
        if s['rpm_cap'] is not None:
            t['_cap'] = s['rpm_cap']
        for _, key, fmt in READOUTS:
            self.vals[key].config(text=fmt.format(t[key]) if key in t else '-')

        msgs = []
        if s['fault']:
            msgs.append(f"FAULT [{FAULT_TEXT.get(s['fault_level'], '')}]: {s['fault']}")
        msgs += s['warn']
        if self.note_n > 0:
            msgs.append(self.note)
            self.note_n -= 1
        self.msg_lbl.config(text='\n'.join(msgs))
        self.out_lbl.config(text=f"송신 → vel {s['vel']:.0f} rpm | cur {s['cur']*100:.0f}% | "
                                 f"bus {s['bus']*100:.0f}%")
        self.root.after(100, self.tick)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--sim', action='store_true', help='하드웨어 없이 시뮬레이터로 실행')
    ap.add_argument('--config', default=None)
    a = ap.parse_args()
    root = tk.Tk()
    try:
        vcu = Vcu(a.config, sim=a.sim) if a.config else Vcu(sim=a.sim)
    except ValueError as e:                 # config outside WS22 datasheet -> refuse to run
        from tkinter import messagebox
        root.withdraw()
        messagebox.showerror('설정 오류 — 실행 거부', str(e))
        raise SystemExit(1)
    App(root, vcu)
    root.mainloop()
