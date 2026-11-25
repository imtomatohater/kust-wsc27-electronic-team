"""WS22 VCU — UI.

    python vcu_app.py                         # launcher window (choose config + run mode)
    python vcu_app.py [--sim] [--monitor] [--config FILE]   # skip the launcher

    --sim      no hardware: simulated WS22 + motor (+ virtual pedal node if pedal.enabled)
    --monitor  listen-only: shows WS22 + pedal telemetry, never transmits, cannot ARM

Keys:  SPACE / ESC = STOP      ↑ / ↓ = press / ease accelerator      X = foot off
       B (hold) = brake        mouse: drag the accelerator down, wheel = fine adjust
"""
import argparse
import glob
import json
import math
import os
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from vcu_core import (Vcu, FAULT, TORQUE, SPEED, SLIDER, PEDAL, SCREEN, FAULT_TEXT,
                      DATA_DIR, ensure_data_dir)

APP_NAME = 'WS22 VCU'
FONT = 'Malgun Gothic' if sys.platform == 'win32' else 'TkDefaultFont'
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


# ======================================================================= pedal widget
class PedalCanvas:
    """Animated accelerator + brake pedal, side view.

    The pedal does not jump to a number: a 'foot' pushes it with a limited speed, the
    return spring pulls it back when the foot lifts. So *how far* (depth, hold, adjust)
    and *how long / how fast* you press both come out naturally.

      mouse  : press on the pedal and drag down = press deeper, drag up = ease off,
               release button = foot off (spring return) unless '발 고정' is ticked,
               wheel = fine adjust while the foot is on
      keys   : ↑ press deeper, ↓ ease off (keyboard foot stays where it is), X foot off,
               B (hold) brake
    """
    W, H = 360, 260
    PRESS_RATE = 160.0    # %/s fastest a foot pushes the pedal down
    EASE_RATE = 160.0     # %/s easing off with the foot still on
    SPRING_RATE = 450.0   # %/s spring return when the foot lifts
    KEY_RATE = 45.0       # %/s while ↑ / ↓ held
    DRAG_PX = 170         # mouse drag distance for 0 -> 100 %
    ANG_REL, ANG_FULL = 62.0, 30.0   # pedal angle from the floor (deg)

    def __init__(self, parent, deadzone_pct, full_pct):
        self.dz, self.fp = deadzone_pct, full_pct
        self.travel = self.target = 0.0
        self.foot = self.brake = False
        self.mouse_brake = self.key_brake = False
        self.drag = None                     # (y0, target0)
        self.keys = set()
        self.interactive = True
        self.caption = ''
        self.press_t, self.peak = 0.0, 0.0
        self.last_press = None               # (duration s, peak %)
        self.lock = tk.BooleanVar(value=False)

        c = self.c = tk.Canvas(parent, width=self.W, height=self.H, bg='#f8fafc',
                               highlightthickness=1, highlightbackground='#cbd5e1')
        # static scenery
        self.floor_y, self.px, self.py = 236, 262, 230
        c.create_rectangle(0, self.floor_y, self.W, self.H, fill='#94a3b8', outline='')
        c.create_rectangle(0, 0, 22, self.floor_y, fill='#cbd5e1', outline='')       # firewall
        # brake pedal (pendant pad, left)
        self.bk = c.create_rectangle(40, 150, 108, 196, fill='#e5e7eb', outline='#475569', width=2)
        c.create_rectangle(22, 92, 90, 100, fill='#64748b', outline='')               # bracket
        self.bk_arm = c.create_line(74, 96, 74, 150, fill='#475569', width=6)
        self.bk_txt = c.create_text(74, 173, text='브레이크', font=(FONT, 9, 'bold'))
        # accelerator parts (coords set in draw)
        self.spring = c.create_line(0, 0, 0, 0, fill='#64748b', width=2)
        self.plate = c.create_polygon(0, 0, 0, 0, fill='#1f2937', outline='#111827')
        self.ridges = [c.create_line(0, 0, 0, 0, fill='#6b7280', width=2) for _ in range(6)]
        self.leg = c.create_line(0, 0, 0, 0, fill='#1e3a8a', width=30, capstyle='round')
        self.shoe = c.create_polygon(0, 0, 0, 0, fill='#7c2d12', outline='#431407', smooth=True)
        c.create_oval(self.px - 7, self.py - 7, self.px + 7, self.py + 7, fill='#334155', outline='')
        # gauge (right edge)
        gx0, gx1, self.gy0, self.gy1 = 330, 348, 30, 226
        self.gx0, self.gx1 = gx0, gx1
        c.create_rectangle(gx0, self.gy0, gx1, self.gy1, outline='#475569', fill='white')
        self.gfill = c.create_rectangle(gx0, self.gy1, gx1, self.gy1, fill='#2563eb', outline='')
        for pct, col in ((self.dz, '#f59e0b'), (self.fp, '#dc2626')):
            y = self._gy(pct)
            c.create_line(gx0 - 4, y, gx1 + 4, y, fill=col, width=2)
        self.gtxt = c.create_text((gx0 + gx1) / 2, 16, text='0%', font=(FONT, 10, 'bold'))
        # texts
        self.t_big = c.create_text(150, 22, text='', font=(FONT, 15, 'bold'), fill='#0f172a')
        self.t_small = c.create_text(150, 46, text='', font=(FONT, 9), fill='#475569')
        self.t_cap = c.create_text(150, 66, text='', font=(FONT, 9, 'bold'), fill='#2563eb')

        c.bind('<ButtonPress-1>', self._down)
        c.bind('<B1-Motion>', self._move)
        c.bind('<ButtonRelease-1>', self._up)
        c.bind('<MouseWheel>', lambda e: self._wheel(1 if e.delta > 0 else -1))
        c.bind('<Button-4>', lambda e: self._wheel(1))
        c.bind('<Button-5>', lambda e: self._wheel(-1))
        self.draw()

    # ---- geometry helpers
    def _gy(self, pct):
        return self.gy1 - (self.gy1 - self.gy0) * max(0.0, min(100.0, pct)) / 100

    def _geom(self, travel):
        th = math.radians(self.ANG_REL - (self.ANG_REL - self.ANG_FULL) * travel / 100)
        d = (-math.cos(th), -math.sin(th))          # pivot -> toe
        n = (math.sin(th), -math.cos(th))           # face normal, toward the driver
        return d, n

    def _on_brake(self, x, y):
        return 30 <= x <= 118 and 140 <= y <= 206

    # ---- input
    def bind_keys(self, root):
        root.bind('<KeyPress-Up>', lambda e: self.keys.add('up'))
        root.bind('<KeyRelease-Up>', lambda e: self.keys.discard('up'))
        root.bind('<KeyPress-Down>', lambda e: self.keys.add('down'))
        root.bind('<KeyRelease-Down>', lambda e: self.keys.discard('down'))
        for k in ('x', 'X'):
            root.bind(f'<KeyPress-{k}>', lambda e: self.release())
        for k in ('b', 'B'):
            root.bind(f'<KeyPress-{k}>', lambda e: setattr(self, 'key_brake', True))
            root.bind(f'<KeyRelease-{k}>', lambda e: setattr(self, 'key_brake', False))

    def _down(self, e):
        if not self.interactive:
            return
        self.c.focus_set()
        if self._on_brake(e.x, e.y):
            self.mouse_brake = True
            return
        self.foot = True
        self.drag = (e.y, self.target)

    def _move(self, e):
        if self.drag:
            y0, t0 = self.drag
            self.target = max(0.0, min(100.0, t0 + (e.y - y0) / self.DRAG_PX * 100))

    def _up(self, e):
        self.mouse_brake = False
        if self.drag:
            self.drag = None
            if not self.lock.get():
                self.release()

    def _wheel(self, sign):
        if not self.interactive:
            return
        self.foot = True
        self.target = max(0.0, min(100.0, self.target + 3.0 * sign))

    def release(self):
        """Foot off: the spring returns the pedal."""
        self.foot, self.drag, self.target = False, None, 0.0
        self.keys.clear()

    # ---- simulation / display
    def step(self, dt):
        if self.interactive:
            if 'up' in self.keys:
                self.foot = True
                self.target = min(100.0, self.target + self.KEY_RATE * dt)
            if 'down' in self.keys:
                self.target = max(0.0, self.target - self.KEY_RATE * dt)
            goal = self.target if self.foot else 0.0
            if goal > self.travel:
                rate = self.PRESS_RATE
            else:
                rate = self.EASE_RATE if self.foot else self.SPRING_RATE
            delta = max(-rate * dt, min(rate * dt, goal - self.travel))
            self.travel = max(0.0, min(100.0, self.travel + delta))
            self.brake = self.mouse_brake or self.key_brake
        # press timing (independent of who moves the pedal)
        if self.travel > self.dz:
            self.press_t += dt
            self.peak = max(self.peak, self.travel)
        elif self.press_t > 0:
            self.last_press, self.press_t, self.peak = (self.press_t, self.peak), 0.0, 0.0
        self.draw()

    def show(self, travel, brake):
        """Display-only mode: mirror a real (CAN) pedal."""
        self.travel, self.brake = (travel or 0.0), bool(brake)

    def set_interactive(self, on, caption):
        if not on and self.interactive:
            self.release()
        self.interactive, self.caption = on, caption

    def draw(self):
        c, t = self.c, self.travel
        d, n = self._geom(t)
        L, T = 165, 14
        px, py = self.px, self.py

        def pt(s, off=0.0):          # point s (0..1) along the pedal, 'off' px out of the face
            return (px + d[0] * L * s + n[0] * off, py + d[1] * L * s + n[1] * off)

        a, b = pt(0, 0), pt(1, 0)
        c.coords(self.plate, *a, *b, *pt(1, T), *pt(0, T))
        for i, r in enumerate(self.ridges):
            s = 0.45 + i * 0.09
            c.coords(r, *pt(s, T), *pt(s, T + 4))
        # return spring: from the back of the plate down to the floor
        s0 = pt(0.55, -2)
        s1 = (s0[0] - 18, self.floor_y)
        pts, k = [], 9
        for i in range(k + 1):
            f = i / k
            x = s0[0] + (s1[0] - s0[0]) * f
            y = s0[1] + (s1[1] - s0[1]) * f
            if 0 < i < k:
                x += 6 if i % 2 else -6
            pts += [x, y]
        c.coords(self.spring, *pts)
        # foot + leg
        if self.foot and self.interactive:
            sh = [pt(0.12, T), pt(0.97, T), pt(1.0, T + 16), pt(0.78, T + 34),
                  pt(0.32, T + 42), pt(0.10, T + 30)]
            c.coords(self.shoe, *[v for p in sh for v in p])
            knee = pt(0.30, T + 40)
            c.coords(self.leg, *knee, self.W - 50, 8)
            c.itemconfigure(self.shoe, state='normal')
            c.itemconfigure(self.leg, state='normal')
        else:
            c.itemconfigure(self.shoe, state='hidden')
            c.itemconfigure(self.leg, state='hidden')
        # brake pad
        dy = 8 if self.brake else 0
        c.coords(self.bk, 40, 150 + dy, 108, 196 + dy)
        c.coords(self.bk_txt, 74, 173 + dy)
        c.coords(self.bk_arm, 74, 96, 74, 150 + dy)
        c.itemconfigure(self.bk, fill='#dc2626' if self.brake else '#e5e7eb')
        c.itemconfigure(self.bk_txt, fill='white' if self.brake else '#111827')
        # gauge
        c.coords(self.gfill, self.gx0 + 1, self._gy(t), self.gx1 - 1, self.gy1)
        c.itemconfigure(self.gfill, fill='#2563eb' if t > self.dz else '#94a3b8')
        c.itemconfigure(self.gtxt, text=f'{t:.0f}%')
        # timing text
        if self.press_t > 0:
            c.itemconfigure(self.t_big, text=f'밟는 중  {self.press_t:4.1f} s')
        else:
            c.itemconfigure(self.t_big, text='발 뗌' if not self.foot else '데드존 안')
        if self.last_press:
            dur, pk = self.last_press
            c.itemconfigure(self.t_small, text=f'직전: {dur:.1f} s 동안, 최대 {pk:.0f}%')
        cap = self.caption
        if self.lock.get() and self.foot and self.interactive:
            cap = (cap + '  ·  발 고정 중').strip(' ·')
        c.itemconfigure(self.t_cap, text=cap)


# ======================================================================= trend chart
class TrendChart:
    """Last `span` seconds: pedal travel, torque command (sent current %), motor rpm."""
    W, H = 360, 120

    def __init__(self, parent, max_rpm, span=10.0):
        self.span, self.max_rpm = span, max(1.0, float(max_rpm))
        self.buf = []   # (t, travel, cur %, rpm)
        c = self.c = tk.Canvas(parent, width=self.W, height=self.H, bg='white',
                               highlightthickness=1, highlightbackground='#cbd5e1')
        self.x0, self.x1, self.y0, self.y1 = 30, self.W - 6, 8, self.H - 22
        for f in (0, 0.25, 0.5, 0.75, 1.0):
            y = self.y1 - (self.y1 - self.y0) * f
            c.create_line(self.x0, y, self.x1, y, fill='#e5e7eb')
            c.create_text(self.x0 - 4, y, text=f'{f*100:.0f}', anchor='e', font=(FONT, 7),
                          fill='#64748b')
        c.create_text(self.x1, self.H - 10, text='0 s', anchor='e', font=(FONT, 7), fill='#64748b')
        c.create_text(self.x0, self.H - 10, text=f'-{span:.0f} s', anchor='w', font=(FONT, 7),
                      fill='#64748b')
        self.lines = {}
        for i, (key, col, name) in enumerate([('travel', '#2563eb', '페달 %'),
                                              ('cur', '#ea580c', '토크명령 %'),
                                              ('rpm', '#16a34a', f'rpm/{self.max_rpm:.0f}')]):
            self.lines[key] = c.create_line(0, 0, 0, 0, fill=col, width=2)
            c.create_text(95 + i * 85, self.H - 10, text='━ ' + name, fill=col, font=(FONT, 8))

    def add(self, travel, cur_pct, rpm):
        now = time.time()
        self.buf.append((now, travel, cur_pct, rpm))
        while self.buf and now - self.buf[0][0] > self.span:
            self.buf.pop(0)
        if len(self.buf) < 2:
            return
        sx = (self.x1 - self.x0) / self.span
        sy = (self.y1 - self.y0) / 100
        for idx, key in ((1, 'travel'), (2, 'cur'), (3, 'rpm')):
            pts = []
            for row in self.buf:
                v = row[idx] if key != 'rpm' else abs(row[idx]) / self.max_rpm * 100
                v = max(0.0, min(100.0, v))
                pts += [self.x1 - (now - row[0]) * sx, self.y1 - v * sy]
            self.c.coords(self.lines[key], *pts)


# ======================================================================= main window
class App:
    def __init__(self, root, vcu):
        self.v, self.root = vcu, root
        L = vcu.cfg['limits']
        self.pc = vcu.pc
        self.max_rpm = vcu.cfg['ws22']['max_rpm_setpoint']
        self.regen_max = L['regen_max_pct']
        cfg_name = os.path.basename(getattr(vcu, 'cfg_path', '') or '')
        root.title(f'{APP_NAME} — {cfg_name}' + (' — MONITOR' if vcu.monitor else '')
                   + (' — SIM' if vcu.sim else ''))
        root.protocol('WM_DELETE_WINDOW', self.quit)
        root.bind('<space>', lambda e: self.stop())
        root.bind('<Escape>', lambda e: self.stop())
        root.bind('<FocusOut>', lambda e: root.after(60, self._focus_check))
        f = ttk.Frame(root, padding=10)
        f.grid(sticky='nsew')
        left = ttk.Frame(f)
        left.grid(row=2, column=0, sticky='nw')
        right = ttk.Frame(f, padding=(14, 0, 0, 0))
        right.grid(row=2, column=1, sticky='nw')
        third = ttk.Frame(f, padding=(14, 0, 0, 0))
        third.grid(row=2, column=2, sticky='nw')

        # state banner (all columns)
        self.state_lbl = tk.Label(f, text='', font=(FONT, 20, 'bold'), fg='white')
        self.state_lbl.grid(row=0, column=0, columnspan=3, sticky='ew')
        self.msg_lbl = tk.Label(f, text='', fg='#dc2626', font=(FONT, 9), wraplength=1000,
                                justify='left', anchor='w', height=3)
        self.msg_lbl.grid(row=1, column=0, columnspan=3, sticky='ew', pady=(2, 6))

        # ---------------- left column: controls + pedal + trend
        bf = ttk.Frame(left)
        bf.grid(row=0, column=0, sticky='w')
        self.arm_btn = tk.Button(bf, text='ARM', width=9, command=self.arm)
        self.arm_btn.grid(row=0, column=0, padx=(0, 4))
        if vcu.monitor:
            self.arm_btn.config(state='disabled')
        tk.Button(bf, text='STOP', width=9, bg='#dc2626', fg='white',
                  font=(FONT, 10, 'bold'), command=self.stop).grid(row=0, column=1, padx=4)
        tk.Button(bf, text='FAULT 리셋', width=9, command=self.reset).grid(row=0, column=2, padx=4)
        self.log_btn = tk.Button(bf, text='로그 시작', width=9, command=self.toggle_log)
        self.log_btn.grid(row=0, column=3, padx=4)
        self.btn_bg = self.log_btn.cget('bg')

        rf = ttk.Frame(left)
        rf.grid(row=1, column=0, sticky='w', pady=(6, 0))
        self.mode = tk.StringVar(value=vcu.mode)
        self.dir = tk.IntVar(value=1)
        self.src = tk.StringVar(value=vcu.source)
        ttk.Radiobutton(rf, text='토크(전류 %)', variable=self.mode, value=TORQUE,
                        command=self.on_mode).grid(row=0, column=0, sticky='w')
        ttk.Radiobutton(rf, text='속도(rpm)', variable=self.mode, value=SPEED,
                        command=self.on_mode).grid(row=0, column=1, sticky='w')
        ttk.Radiobutton(rf, text='전진', variable=self.dir, value=1,
                        command=self.on_dir).grid(row=0, column=2, sticky='w', padx=(16, 0))
        ttk.Radiobutton(rf, text='후진', variable=self.dir, value=-1,
                        command=self.on_dir).grid(row=0, column=3, sticky='w')
        ttk.Label(rf, text='입력:').grid(row=1, column=0, sticky='w')
        ttk.Radiobutton(rf, text='슬라이더', variable=self.src, value=SLIDER,
                        command=self.on_src).grid(row=1, column=1, sticky='w')
        rb = ttk.Radiobutton(rf, text='CAN 가속페달', variable=self.src, value=PEDAL,
                             command=self.on_src)
        rb.grid(row=1, column=2, sticky='w', padx=(16, 0))
        if not vcu.pedal:
            rb.state(['disabled'])
        ttk.Radiobutton(rf, text='화면 페달', variable=self.src, value=SCREEN,
                        command=self.on_src).grid(row=1, column=3, sticky='w')

        self.cmd_lbl = ttk.Label(left, text='')
        self.cmd_lbl.grid(row=2, column=0, sticky='w', pady=(6, 0))
        self.scale = tk.Scale(left, orient='horizontal', length=360, resolution=1, takefocus=0,
                              command=lambda x: self.v.set_cmd(x))
        self.scale.grid(row=3, column=0, sticky='w')

        pf = ttk.LabelFrame(left, text='가속페달', padding=4)
        pf.grid(row=4, column=0, sticky='w', pady=(4, 0))
        self.ped = PedalCanvas(pf, self.pc['deadzone_pct'], self.pc['full_pct'])
        self.ped.c.grid(row=0, column=0, columnspan=2)
        self.ped.bind_keys(root)
        ttk.Checkbutton(pf, text='발 고정 (마우스를 놓아도 유지)', variable=self.ped.lock) \
            .grid(row=1, column=0, sticky='w')
        ttk.Button(pf, text='발 떼기 (X)', command=self.ped.release).grid(row=1, column=1, sticky='e')
        ttk.Label(pf, text='페달을 누른 채 아래로 끌기 = 밟기 · 위로 = 풀기 · 휠 = 미세조정\n'
                           '키보드 ↑ 밟기 / ↓ 풀기 (키를 떼도 유지) · X 발 떼기 · B 브레이크',
                  foreground='#64748b', font=(FONT, 8)).grid(row=2, column=0, columnspan=2, sticky='w')

        tf = ttk.LabelFrame(left, text='최근 10초', padding=4)
        tf.grid(row=5, column=0, sticky='w', pady=(4, 0))
        self.chart = TrendChart(tf, self.max_rpm)
        self.chart.c.grid()

        # ---------------- right column: readouts + pedal monitor (+ sim fault injection)
        rd = ttk.LabelFrame(right, text='WS22 / 모터', padding=6)
        rd.grid(row=0, column=0, sticky='nw')
        self.vals = {}
        for i, (name, key, _) in enumerate(READOUTS):
            ttk.Label(rd, text=name).grid(row=i, column=0, sticky='w', pady=1)
            lb = ttk.Label(rd, text='-', font=('Consolas', 12, 'bold'), width=12, anchor='e')
            lb.grid(row=i, column=1, sticky='e')
            self.vals[key] = lb

        pm = ttk.LabelFrame(third, text='페달 모니터', padding=6)
        pm.grid(row=0, column=0, sticky='nwe')
        self.pv = {}
        rows = [('입력 소스', 'src'), ('페달 위치', 'travel'), ('토크 명령', 'torque'),
                ('APPS1', 'v1'), ('APPS2', 'v2'), ('브레이크', 'brake'), ('상태', 'status')]
        if vcu.pedal:
            rows.insert(3, (f'CAN 0x{self.pc["can_id"]:03X}', 'can'))
        for i, (name, key) in enumerate(rows):
            ttk.Label(pm, text=name).grid(row=i, column=0, sticky='w', pady=1)
            lb = ttk.Label(pm, text='-', font=('Consolas', 11, 'bold'), width=14, anchor='e')
            lb.grid(row=i, column=1, sticky='e')
            self.pv[key] = lb

        self.sim_flags = {}
        if vcu.sim and vcu.pedal:
            sf = ttk.LabelFrame(third, text='시뮬레이터 — 페달 노드 고장 주입', padding=6)
            sf.grid(row=1, column=0, sticky='nwe', pady=(8, 0))
            for i, (name, key) in enumerate([('노드 끊기', 'off'), ('카운터 정지', 'frozen'),
                                             ('APPS1 단선', 'open'), ('APPS2 불일치', 'mismatch')]):
                var = tk.BooleanVar(value=False)
                ttk.Checkbutton(sf, text=name, variable=var).grid(row=i // 2, column=i % 2, sticky='w')
                self.sim_flags[key] = var

        # CAN diagnostics
        cd = ttk.LabelFrame(right, text='CAN 진단', padding=6)
        cd.grid(row=1, column=0, sticky='nwe', pady=(8, 0))
        self.cv = {}
        for i, (name, key) in enumerate([('어댑터', 'iface'), ('버스 상태', 'state'),
                                         ('수신', 'rx'), ('송신', 'tx')]):
            ttk.Label(cd, text=name).grid(row=i, column=0, sticky='nw', pady=1)
            lb = ttk.Label(cd, text='-', font=('Consolas', 9), anchor='e', justify='right')
            lb.grid(row=i, column=1, sticky='e')
            self.cv[key] = lb
        ttk.Label(cd, text='수신 ID').grid(row=4, column=0, sticky='nw', pady=(4, 0))
        self.cv['ids'] = ttk.Label(cd, text='-', font=('Consolas', 9), justify='left')
        self.cv['ids'].grid(row=4, column=1, sticky='w', pady=(4, 0))
        self.cv['hints'] = tk.Label(cd, text='', fg='#b45309', font=(FONT, 9), justify='left',
                                    wraplength=280, anchor='w')
        self.cv['hints'].grid(row=5, column=0, columnspan=2, sticky='w', pady=(4, 0))

        self.out_lbl = ttk.Label(f, text='', foreground='#555')
        self.out_lbl.grid(row=3, column=0, columnspan=3, sticky='w', pady=(6, 0))
        self.on_mode(apply=False)
        self.last_step = time.time()
        self.anim()
        self.tick()

    # ---- actions ----
    def flash(self, text):
        self.note, self.note_n = text, 20      # show ~2 s

    def arm(self):
        err = self.v.arm()
        if err:
            self.flash(err)

    def stop(self):
        self.v.stop()
        self.ped.release()
        self.scale.set(0)

    def reset(self):
        self.scale.set(0)
        err = self.v.reset_fault()
        if err:
            self.flash(err)

    def _focus_check(self):
        if self.root.focus_get() is None:      # window lost focus -> take the foot off
            self.ped.release()

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
        if self.v.source in (PEDAL, SCREEN) and self.mode.get() == TORQUE:
            self.cmd_lbl.config(text=('명령: CAN 가속페달' if self.v.source == PEDAL
                                      else '명령: 화면 페달') + '  (슬라이더 비활성)')
            self.scale.config(state='disabled')

    def on_src(self):
        err = self.v.set_source(self.src.get())
        if err:
            self.flash(err)
            self.src.set(self.v.source)
        self.ped.release()
        self.on_mode(apply=False)

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

    # ---- pedal role: who moves the drawn pedal, and where its value goes
    def _pedal_role(self):
        if self.v.source == SCREEN:
            return 'screen'
        if self.v.pedal and self.v.sim:
            return 'sim_node'
        if self.v.pedal:
            return 'display'
        return 'idle'

    # ---- ~33 Hz pedal animation
    def anim(self):
        now = time.time()
        dt, self.last_step = min(0.1, now - self.last_step), now
        role, ped = self._pedal_role(), self.ped
        if role == 'display':
            ped.set_interactive(False, '실제 CAN 페달 표시 중')
            s = self.v.snapshot()
            po = s['pedal_can']
            ped.show(po['travel'] if po else 0.0, po['brake'] if po else False)
        elif role == 'screen':
            ped.set_interactive(True, '화면 페달 → 토크 명령')
        elif role == 'sim_node':
            ped.set_interactive(True, '가상 CAN 페달 노드로 송신 (시뮬)')
        else:
            ped.set_interactive(True, "입력을 '화면 페달'로 바꾸면 토크 명령")
        ped.step(dt)
        if role == 'screen':
            self.v.screen_pedal(ped.travel, ped.brake)
        elif role == 'sim_node':
            pc, fl = self.pc, self.sim_flags
            x = ped.travel / 100
            mv1 = pc['ch1_mV_released'] + x * (pc['ch1_mV_full'] - pc['ch1_mV_released'])
            mv2 = pc['ch2_mV_released'] + x * (pc['ch2_mV_full'] - pc['ch2_mV_released'])
            if fl['mismatch'].get():
                mv2 += 0.3 * (pc['ch2_mV_full'] - pc['ch2_mV_released'])
            if fl['open'].get():
                mv1 = 50.0
            self.v.sim_pedal(mv1=mv1, mv2=max(0.0, mv2), brake=ped.brake,
                             on=not fl['off'].get(), frozen=fl['frozen'].get())
        self.root.after(30, self.anim)

    # ---- 10 Hz refresh ----
    note, note_n = '', 0

    def tick(self):
        s = self.v.snapshot()
        t = s['tel']
        st = s['state'] if s['fresh'] or s['state'] == FAULT else 'NO CAN'
        if s['monitor']:
            st = 'MONITOR' if s['fresh'] else 'MONITOR · NO CAN'
        self.state_lbl.config(text=st + ('  ·  SIM (가상)' if self.v.sim else ''),
                              bg=COLORS.get(st, '#f59e0b'))
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

        # pedal monitor
        po, pv = s['pedal'], self.pv
        pv['src'].config(text={SLIDER: '슬라이더', PEDAL: 'CAN 페달', SCREEN: '화면 페달'}[s['source']])
        if 'can' in pv:
            pc_out = s['pedal_can']
            ok = pc_out is not None and not (pc_out['fault'] and '끊김' in pc_out['fault'])
            pv['can'].config(text='수신 중' if ok else '수신 없음',
                             foreground='#16a34a' if ok else '#dc2626')
        if po:
            pv['travel'].config(text='-' if po['travel'] is None else f"{po['travel']:.0f} %")
            pv['torque'].config(text=f"{po['torque']:.1f} %")
            pv['brake'].config(text='ON' if po['brake'] else 'off',
                               foreground='#dc2626' if po['brake'] else '')
            if po['fault']:
                pv['status'].config(text='고장', foreground='#dc2626')
            elif po['note']:
                pv['status'].config(text='제한', foreground='#d97706')
            else:
                pv['status'].config(text='정상', foreground='#16a34a')
        else:
            for k in ('travel', 'torque', 'brake', 'status'):
                pv[k].config(text='-', foreground='')
        src_out = s['pedal_can'] if s['source'] != SCREEN else None
        if src_out is not None and 'Apps1_mV' in t:
            pv['v1'].config(text=f"{t['Apps1_mV'] / 1000:.2f} V")
            pv['v2'].config(text=f"{t['Apps2_mV'] / 1000:.2f} V" if self.pc['dual_channel'] else '사용 안 함')
        else:
            pv['v1'].config(text='-')
            pv['v2'].config(text='-')

        trav = po['travel'] if po and po['travel'] is not None else self.ped.travel
        self.chart.add(trav or 0.0, s['cur'] * 100, t.get('MotorVelocity', 0.0))

        msgs = []
        if s['fault']:
            msgs.append(f"FAULT [{FAULT_TEXT.get(s['fault_level'], '')}]: {s['fault']}")
        msgs += s['warn']
        if self.note_n > 0:
            msgs.append(self.note)
            self.note_n -= 1
        self.msg_lbl.config(text='\n'.join(msgs[:3]) + ('  …' if len(msgs) > 3 else ''))
        if s['monitor']:
            self.out_lbl.config(text='모니터링 전용 — CAN 송신 없음')
        else:
            self.out_lbl.config(text=f"송신 → vel {s['vel']:.0f} rpm | cur {s['cur']*100:.1f}% | "
                                     f"bus {s['bus']*100:.0f}%   (명령 {s['cmd']:.1f})")
        c = s['can']
        self.cv['iface'].config(text='시뮬레이터 (가상)' if not c['real'] else c['iface'])
        stc = c['state'] or ('-' if c['real'] else 'SIM')
        self.cv['state'].config(text=stc + (f"  에러프레임 {c['err_frames']}" if c['err_frames'] else ''),
                                foreground='#dc2626' if stc in ('PASSIVE', 'ERROR') or c['err_frames']
                                else '#16a34a' if stc == 'ACTIVE' else '')
        self.cv['rx'].config(text=f"{c['rx']} ({c['rx_fps']:.0f}/s) · 인식 {c['rx_known']}")
        self.cv['tx'].config(text=f"{c['tx']} ({c['tx_fps']:.0f}/s) · 실패 {c['tx_err']}",
                             foreground='#dc2626' if c['tx_err'] else '')
        self.cv['ids'].config(text='\n'.join(f"0x{i:03X} ×{n}{'' if ok else '  ?'}" for i, n, ok in c['ids'][:8])
                              or '(없음)')
        self.cv['hints'].config(text='\n'.join('• ' + h for h in c['hints']))
        self.root.after(100, self.tick)


# ======================================================================= launcher
class Launcher:
    """Start window for the packaged program: pick a config file and a run mode."""
    MODES = [('real', '실제 CAN 구동'), ('monitor', '모니터링 전용 (송신 없음, ARM 불가)'),
             ('sim', '시뮬레이터 (하드웨어 없이)')]

    def __init__(self, root):
        self.root = root
        root.title(APP_NAME)
        ensure_data_dir()
        f = self.f = ttk.Frame(root, padding=16)
        f.grid(sticky='nsew')
        ttk.Label(f, text=APP_NAME, font=(FONT, 18, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w')
        ttk.Label(f, text='Prohelion WaveSculptor22 + MARAND 모터', foreground='#64748b') \
            .grid(row=1, column=0, columnspan=3, sticky='w', pady=(0, 10))

        ttk.Label(f, text='설정 파일').grid(row=2, column=0, sticky='w')
        self.cfg = tk.StringVar()
        self.cb = ttk.Combobox(f, textvariable=self.cfg, width=34)
        self.cb.grid(row=2, column=1, sticky='we', padx=6)
        self.cb.bind('<<ComboboxSelected>>', lambda e: self.show_info())
        ttk.Button(f, text='찾아보기…', command=self.browse).grid(row=2, column=2)
        self.info = ttk.Label(f, text='', foreground='#475569', justify='left')
        self.info.grid(row=3, column=1, columnspan=2, sticky='w', padx=6, pady=(2, 8))

        ttk.Label(f, text='실행 모드').grid(row=4, column=0, sticky='nw')
        self.mode = tk.StringVar(value=self._load_state().get('mode', 'real'))
        mf = ttk.Frame(f)
        mf.grid(row=4, column=1, columnspan=2, sticky='w', padx=6)
        for i, (k, name) in enumerate(self.MODES):
            ttk.Radiobutton(mf, text=name, variable=self.mode, value=k).grid(row=i, column=0, sticky='w')

        bf = ttk.Frame(f)
        bf.grid(row=5, column=0, columnspan=3, sticky='we', pady=(14, 0))
        ttk.Button(bf, text='설정 폴더 열기', command=self.open_folder).pack(side='left')
        tk.Button(bf, text='시작', width=12, bg='#16a34a', fg='white', font=(FONT, 11, 'bold'),
                  command=self.start).pack(side='right')
        ttk.Label(f, text=f'설정·로그 폴더: {DATA_DIR}', foreground='#94a3b8', font=(FONT, 8)) \
            .grid(row=6, column=0, columnspan=3, sticky='w', pady=(10, 0))
        root.bind('<Return>', lambda e: self.start())
        self.refresh()

    def path(self):
        """Combobox shows file names from DATA_DIR; '찾아보기' may give a full path."""
        v = self.cfg.get()
        return v if os.path.isabs(v) else os.path.join(DATA_DIR, v)

    def refresh(self):
        files = sorted(os.path.basename(p) for p in glob.glob(os.path.join(DATA_DIR, '*.json')))
        self.cb['values'] = files
        last = self._load_state().get('config')
        pick = [p for p in files if p == last] or [p for p in files if p == 'config_bench24v.json'] or files
        if pick and not self.cfg.get():
            self.cfg.set(pick[0])
        self.show_info()

    def show_info(self):
        try:
            with open(self.path(), encoding='utf-8') as fh:
                c = json.load(fh)
            can, lim, ped = c['can'], c['limits'], c.get('pedal', {})
            self.info.config(text=(f"CAN {can['interface']} / {can['channel']} / {can['bitrate']} bps\n"
                                   f"버스 {lim['bus_v_min']}–{lim['bus_v_max']} V · 회생 {lim['regen_max_pct']}% · "
                                   f"CAN 페달 {'사용' if ped.get('enabled') else '미사용'}"))
        except Exception as e:  # noqa: BLE001 — show any problem reading the file
            self.info.config(text=f'설정 파일을 읽을 수 없음: {e}')

    def browse(self):
        p = filedialog.askopenfilename(initialdir=DATA_DIR, filetypes=[('JSON', '*.json')])
        if p:
            self.cfg.set(p)
            self.show_info()

    def open_folder(self):
        try:
            if sys.platform == 'win32':
                os.startfile(DATA_DIR)  # noqa: S606
            else:
                import subprocess
                subprocess.Popen(['xdg-open', DATA_DIR])
        except Exception as e:  # noqa: BLE001
            messagebox.showinfo(APP_NAME, f'{DATA_DIR}\n\n({e})')

    STATE_FILE = os.path.join(DATA_DIR, 'launcher_state.json')

    def _load_state(self):
        try:
            with open(self.STATE_FILE, encoding='utf-8') as fh:
                return json.load(fh)
        except Exception:  # noqa: BLE001
            return {}

    def start(self):
        m = self.mode.get()
        try:
            with open(self.STATE_FILE, 'w', encoding='utf-8') as fh:
                json.dump({'mode': m, 'config': self.cfg.get()}, fh)
        except Exception:  # noqa: BLE001
            pass
        vcu = open_vcu(self.path(), sim=m == 'sim', monitor=m == 'monitor')
        if vcu:
            self.root.unbind('<Return>')
            self.f.destroy()
            App(self.root, vcu)


def open_vcu(cfg_path, sim, monitor):
    try:
        return Vcu(cfg_path, sim=sim, monitor=monitor)
    except ValueError as e:                  # config outside WS22 datasheet -> refuse to run
        messagebox.showerror('설정 오류 — 실행 거부', str(e))
    except FileNotFoundError as e:
        messagebox.showerror('설정 파일 없음', str(e))
    except Exception as e:  # noqa: BLE001 — CAN adapter / driver problems
        messagebox.showerror('CAN 연결 실패',
                             f'{type(e).__name__}: {e}\n\n'
                             '- CAN 어댑터가 연결됐는지\n- 드라이버(PCAN 등)가 설치됐는지\n'
                             '- 설정 파일의 can.interface / channel 이 맞는지 확인하세요.\n\n'
                             '하드웨어 없이 확인하려면 시뮬레이터 모드로 실행하세요.')
    return None


def selftest():
    """Headless check of a packaged build: resources, configs, CAN library, sim loop.
    Exit code 0 = OK. Used by CI on the built exe."""
    import can  # noqa: F401  — python-can bundled?
    import can.interfaces.pcan  # noqa: F401
    ensure_data_dir()
    for name in ('config.json', 'config_bench24v.json'):
        v = Vcu(os.path.join(DATA_DIR, name), sim=True)
        ok = False
        for _ in range(40):
            time.sleep(0.05)
            ok = v.snapshot()['fresh']
            if ok:
                break
        v.close()
        if not ok:
            raise SystemExit(f'selftest: no telemetry with {name}')
    with open(os.path.join(DATA_DIR, 'logs', 'selftest_ok.txt'), 'w', encoding='utf-8') as fh:
        fh.write(time.strftime('%Y-%m-%d %H:%M:%S'))


def main():
    if '--selftest' in sys.argv:
        selftest()
        return
    ap = argparse.ArgumentParser()
    ap.add_argument('--sim', action='store_true', help='하드웨어 없이 시뮬레이터로 실행')
    ap.add_argument('--monitor', action='store_true', help='모니터링 전용 (송신 안 함, ARM 불가)')
    ap.add_argument('--config', default=None)
    a = ap.parse_args()
    root = tk.Tk()
    if a.sim or a.monitor or a.config:
        vcu = open_vcu(a.config, a.sim, a.monitor)
        if not vcu:
            raise SystemExit(1)
        App(root, vcu)
    else:
        Launcher(root)
    root.mainloop()


if __name__ == '__main__':
    main()
