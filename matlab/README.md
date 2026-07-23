# KUST WSC27 Car No.82 — SILS Digital Twin (MATLAB)

Plain-MATLAB (no Simulink toolbox required) implementation of the block
diagram from the team's SILS/디지털 트윈 목적 정의 문서:

```
Solar array --> MPPT (x3) --> DC bus / battery --> Inverter --> In-wheel motor --> Vehicle dynamics
                                                                                          |
                                                                                    speed feedback
```

Each block is a standalone function in `models/`, matching the diagram
1:1 so it can be dropped into a Simulink `MATLAB Function` block later if
the team wants a full `.slx` model. `sim/run_sils_sim.m` wires them
together into a time-stepped loop.

## Layout

```
matlab/
  params/vehicle_params.m     master parameter struct (Car No.82 spec)
  models/pv_array_model.m     solar string MPP power/V/I vs irradiance & temp
  models/mppt_model.m         boost converter output using the measured efficiency map
  models/battery_model.m      lumped OCV+R pack model, 1 time-step update
  models/motor_model.m        MARAND in-wheel PMSM electrical + thermal model
  models/vehicle_dynamics.m   inverse longitudinal dynamics (speed/accel/grade -> torque)
  sim/run_sils_sim.m          top-level SILS loop
  data/mppt_efficiency_table.csv   576 measured (Vin,Iin,Vout)->efficiency points
```

## Quick start

```matlab
cd matlab
addpath('params'); addpath('models'); addpath('sim');

% Built-in demo: 1 hour @ 80 km/h, wobbling irradiance around 900 W/m^2, 35 degC
results = run_sils_sim();
plot(results.t_s, results.SOC*100); ylabel('SOC (%)'); xlabel('t (s)');

% Or drive it with your own profiles:
t   = (0:1:3600)';
v   = 88*ones(size(t));       % km/h
G   = 850*ones(size(t));      % W/m^2
Ta  = 40*ones(size(t));       % degC
results = run_sils_sim(t, v, G, Ta, 0, 0.9);  % last two args: grade=0 flat, SOC0=0.9
```

`results` contains `SOC`, `V_batt`, `I_batt`, `P_solar_W`, `P_motor_bus_W`,
`P_net_batt_W`, `Tw_C` (motor winding temp), `tau_motor_Nm`, `mppt_eff`.

Calling `run_sils_sim` with `nargout==0` also plots SOC, power flow and
motor winding temperature automatically.

## Where the numbers came from

| Block | Source |
|---|---|
| Solar array (SunPower Maxeon Gen7 C60, 3x129S1P, 6 m², 1200 W field rating) | Team spec, direct input |
| MPPT efficiency surface | Digitised from `2021029_Efficiency_test_worlds_BEST_RACE_MPPT.xlsx` (576 measured points, Vin 5-140V / Iin 0.5-7A / Vout 25-160V) → `data/mppt_efficiency_table.csv` |
| Battery (Samsung SDI INR21700-50S, 32S5P) | Team spec, direct input. Pack energy computed via WSC §2.5.2 formula: **9.95 MJ**, under the 11 MJ Challenger cap with **1.05 MJ margin** |
| Motor (MARAND in-wheel PMSM) | Numeric constants (`ki`, `Ba`, `Rw0`, `RTherm`, `CTherm`, temperature coefficients) transcribed from the team's MARAND thermal/electrical model spreadsheet. The vendor spreadsheet itself (Copyright © MARAND) is **not** redistributed in this repo — only the physical constants and the equations, which are transcribed in the `motor_model.m` header comment. Rated power (1.8 kW @ τ=16.2 Nm, ω=111.2 rad/s) verified against the source sheet's own worked example. |
| Vehicle mass, CdA, wheel radius | Team spec, direct input |

## Assumptions that need team confirmation (flagged `ASSUMED` in `vehicle_params.m`)

These were not in the spec you gave me — the model runs with sensible
placeholders, but accuracy of the digital twin depends on replacing them:

1. **`P.veh.Crr = 0.0030`** — rolling resistance coefficient (you said "unknown, use typical"). Typical WSC-class tyre-on-tarmac range is 0.0025–0.0040; confirm with tyre datasheet or coast-down test.
2. **`P.motor.n_motors = 1`** — number of driven in-wheel motors. Confirm 1 vs 2.
3. **`P.inverter.eff = 0.97`** — flat inverter efficiency placeholder (you said no map exists). This is currently the least-verified block in the chain — replace with a real efficiency curve/map as soon as one is measured or datasheet-available.
4. **`P.solar.Imp_Isc_ratio = 0.95`** — used to split the array's known MPP power (from 24% eff × cell area) into Vmp/Imp, since the Maxeon Gen7 C60 datasheet Vmp/Imp weren't given. Derived fill factor comes out to ~0.886, plausible for a high-end IBC cell but worth checking against the real datasheet.
5. **`P.solar.tempco_Pmax/Voc`, `NOCT`** — typical mono-Si IBC coefficients, not the exact Maxeon Gen7 datasheet values.
6. **`P.batt.R_cell_ohm = 0.025`**, and the **OCV-SOC curve shape** (`P.batt.soc_bp` / `ocv_frac`) — generic Li-ion rest-curve shape scaled to your cutoff/nominal/full voltages, not the real INR21700-50S discharge curve.
7. **`P.env.rho_air`** — sea-level 20°C default; adjust for actual Stuart Highway conditions if doing route-specific runs.

## Known simplifications (first-version scope)

- All 3 solar strings currently see the same irradiance/temperature input in `run_sils_sim.m` (no per-panel partial shading). To model shading, call `pv_array_model` separately per string with different `G_Wm2`.
- No Monte Carlo weather-scenario layer yet (per the project doc's "몬테카를로로 스크리닝" step) — this is a deterministic single-run SILS core that such a layer would wrap.
- Battery model is a single lumped pack (no per-cell balancing/mismatch).
- No partial-shading multi-MPP search — MPPT is assumed to always find the true single-peak MPP (reasonable given the per-string architecture and INC-style algorithm assumption).
