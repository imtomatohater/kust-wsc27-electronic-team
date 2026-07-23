function P = vehicle_params()
%VEHICLE_PARAMS  KUST WSC27 Car No.82 parameter set for the SILS digital twin.
%
% Values marked "GIVEN" come directly from the team spec. Values marked
% "ASSUMED" are typical-value placeholders because the team spec did not
% include them (or only gave a rounded figure) -- override the fields in
% this file once real datasheet / CFD / test numbers are available.

P = struct();

%% ---- Environment / physical constants ----
P.env.g       = 9.81;      % m/s^2
P.env.rho_air = 1.184;     % kg/m^3, ASSUMED (20 degC, sea level; adjust for Darwin-Adelaide route altitude/temp)
P.env.G_ref   = 1000;      % W/m^2, STC irradiance reference
P.env.T_ref_C = 25;        % degC, STC cell reference temperature

%% ---- Solar array (SunPower Maxeon Gen7 C60, 3 x 129S1P strings) ----
P.solar.cell_type      = 'SunPower Maxeon Gen7 C60';
P.solar.n_strings      = 3;        % GIVEN: 3 independent sets, 1 MPPT each
P.solar.n_series       = 129;      % GIVEN: 129S1P per string
P.solar.n_parallel     = 1;        % GIVEN
P.solar.area_total_m2  = 6.0;      % GIVEN: WSC hard limit, full array uses it
P.solar.area_string_m2 = P.solar.area_total_m2 / P.solar.n_strings;   % 2.0 m^2/string
P.solar.cell_area_m2   = P.solar.area_total_m2 / (P.solar.n_strings * P.solar.n_series); % ~0.0155 m^2 (154 cm^2, matches real C60 cell)

P.solar.Voc_cell       = 0.7;      % V,  GIVEN
P.solar.Isc_cell       = 6.0;      % A,  GIVEN
P.solar.eff_stc        = 0.24;     % -,  GIVEN (~24%)
P.solar.P_rated_field_W = 1200;    % W,  GIVEN: team-declared max array output (all 3 sets, real-world/curved-panel conditions)

% ASSUMED: current split at MPP for a high-FF IBC cell (not given by team).
% Pmp_cell is fixed by eff_stc*G_ref*cell_area; Imp/Isc sets how that power
% splits between Vmp and Imp. Override P.solar.Imp_Isc_ratio if the real
% Maxeon Gen7 C60 datasheet Vmp/Imp becomes available.
P.solar.Imp_Isc_ratio  = 0.95;
P.solar.Imp_cell       = P.solar.Imp_Isc_ratio * P.solar.Isc_cell;
P.solar.Pmp_cell_stc   = P.solar.eff_stc * P.env.G_ref * P.solar.cell_area_m2;  % W, STC
P.solar.Vmp_cell_stc   = P.solar.Pmp_cell_stc / P.solar.Imp_cell;               % V, STC
P.solar.FF_derived     = P.solar.Pmp_cell_stc / (P.solar.Voc_cell * P.solar.Isc_cell); % sanity check, ~0.87-0.90 expected for Maxeon IBC

% String-level STC electrical points (all cells in series per string)
P.solar.Voc_string_stc = P.solar.n_series * P.solar.Voc_cell;
P.solar.Vmp_string_stc = P.solar.n_series * P.solar.Vmp_cell_stc;
P.solar.Imp_string_stc = P.solar.Imp_cell;   % series -> current unchanged
P.solar.Pmp_string_stc = P.solar.Vmp_string_stc * P.solar.Imp_string_stc;
P.solar.P_array_stc_W  = P.solar.n_strings * P.solar.Pmp_string_stc;  % flat-plate STC total (~1.44 kW)

% Field derate: curvature, cell mismatch, soiling, non-normal incidence,
% wiring loss NOT already in eff_stc. Calibrated so STC total matches the
% team-declared field rating of 1200 W.
P.solar.field_derate = P.solar.P_rated_field_W / P.solar.P_array_stc_W;

% Temperature coefficients, ASSUMED typical for high-eff mono-Si IBC cells
P.solar.tempco_Pmax_pct_per_C = -0.30;   % %/degC on power
P.solar.tempco_Voc_pct_per_C  = -0.20;   % %/degC on voltage
P.solar.NOCT_C                = 45;      % degC, nominal operating cell temp at 800 W/m^2, 20 degC ambient, ASSUMED

%% ---- MPPT ----
P.mppt.count            = P.solar.n_strings;   % 1 MPPT per string, GIVEN
P.mppt.eff_table_csv     = fullfile(fileparts(mfilename('fullpath')), '..', 'data', 'mppt_efficiency_table.csv');
P.mppt.source_note       = 'Efficiency surface digitised from team test report "Efficiency_test_worlds_BEST_RACE_MPPT.xlsx" (576 measured points, Vin 5-140V / Iin 0.5-7A / Vout 25-160V).';

%% ---- Battery pack (Samsung SDI INR21700-50S, 32S5P) ----
P.batt.cell_type   = 'Samsung SDI INR21700-50S';
P.batt.n_series    = 32;       % GIVEN
P.batt.n_parallel  = 5;        % GIVEN
P.batt.V_nom_cell  = 3.6;      % V, GIVEN
P.batt.V_min_cell  = 3.0;      % V, GIVEN (cutoff)
P.batt.V_max_cell  = 4.2;      % V, GIVEN (full charge)
P.batt.Q_cell_max_Ah = 5.0;    % Ah, GIVEN
P.batt.Q_cell_min_Ah = 4.8;    % Ah, GIVEN (use as conservative capacity)
P.batt.Q_cell_Ah   = P.batt.Q_cell_min_Ah;  % conservative pack sizing basis

P.batt.V_nom_pack  = P.batt.n_series * P.batt.V_nom_cell;    % 115.2 V
P.batt.V_min_pack  = P.batt.n_series * P.batt.V_min_cell;    % 96.0 V
P.batt.V_max_pack  = P.batt.n_series * P.batt.V_max_cell;    % 134.4 V
P.batt.Q_pack_Ah   = P.batt.n_parallel * P.batt.Q_cell_Ah;   % 24.0 Ah (conservative)
P.batt.E_pack_J    = 3600 * P.batt.V_nom_pack * P.batt.Q_pack_Ah;  % WSC formula, §2.5.2
P.batt.E_pack_MJ   = P.batt.E_pack_J / 1e6;
P.batt.WSC_energy_limit_MJ = 11.0;
P.batt.WSC_energy_margin_MJ = P.batt.WSC_energy_limit_MJ - P.batt.E_pack_MJ;

% ASSUMED: not given by team -- typical DC internal resistance for a
% 21700 5000 mAh high-energy cell at mid-SOC, 25 degC.
P.batt.R_cell_ohm  = 0.025;
P.batt.R_pack_ohm  = (P.batt.n_series * P.batt.R_cell_ohm) / P.batt.n_parallel;

% ASSUMED: normalised OCV-SOC shape (generic Li-ion NMC/NCA rest curve),
% scaled to the given cutoff/nominal/full cell voltages. Replace with the
% real INR21700-50S datasheet discharge curve if available.
P.batt.soc_bp      = [0 0.05 0.10 0.20 0.40 0.60 0.80 0.90 0.95 1.00];
P.batt.ocv_frac     = [0.000 0.30 0.42 0.55 0.62 0.68 0.76 0.86 0.93 1.00]; % fraction of (Vmax-Vmin) above Vmin
P.batt.ocv_cell_bp  = P.batt.V_min_cell + P.batt.ocv_frac * (P.batt.V_max_cell - P.batt.V_min_cell);
P.batt.ocv_pack_bp  = P.batt.n_series * P.batt.ocv_cell_bp;

%% ---- Motor: MARAND in-wheel PMSM (BWSC27 project reference model) ----
% NOTE: constants below are the numeric outputs of the team's MARAND
% motor thermal/electrical model (steady-state + transient sheets). The
% original vendor spreadsheet is Copyright (c) MARAND and is not
% redistributed here -- only the physical constants and equation forms
% (standard PMSM copper/eddy-loss + lumped RC thermal model) are used.
P.motor.name         = 'MARAND in-wheel PMSM';
P.motor.n_motors      = 1;          % ASSUMED: not specified by team -- confirm 1 vs 2 driven wheels
P.motor.P_rated_W     = 1800;       % GIVEN: rated 1.8 kW (verified against source model @ tau=16.2 Nm, w=111.2 rad/s)
P.motor.tau_rated_Nm  = 16.2;       % from source transient sheet, continuous-rated reference point
P.motor.w_rated_rad_s = 111.2;      % from source transient sheet

P.motor.ki_A_per_NmT  = 0.561;      % current constant: iRMS = ki * B * tau
P.motor.Ba_T          = 1.32;       % magnetic remanence @ 20 degC (293 K)
P.motor.B_tempco_T_per_K = -1.2e-3; % B(Tm) = Ba - 1.2e-3*(Tm - 293)
P.motor.Rw0_ohm       = 0.0575;     % winding resistance/phase @ 20 degC (293 K)
P.motor.Rw_tempco_per_K = 0.0039;   % Rw(Tw) = Rw0*(1 + 0.0039*(Tw-293))
P.motor.eddy_k        = 9.602e-6;   % Pe = eddy_k * (B*w)^2
P.motor.RTherm_K_per_W = 0.455;     % winding-to-ambient lumped thermal resistance
P.motor.CTherm_J_per_K = 1435;      % lumped thermal capacitance
P.motor.tau_therm_s    = P.motor.RTherm_K_per_W * P.motor.CTherm_J_per_K; % ~653 s

%% ---- Inverter (motor drive) ----
% ASSUMED: team has no inverter efficiency map. Flat efficiency placeholder
% typical of a modern SiC 3-phase motor drive at WSC power levels (~1-2 kW).
% Replace with a real efficiency map (vs. torque/speed or bus current) as
% soon as one exists -- this is currently the single least-verified block.
P.inverter.eff = 0.97;

%% ---- Vehicle / dynamics ----
P.veh.mass_kg      = 250;          % GIVEN
P.veh.CdA_m2       = 0.0747354;    % GIVEN, quoted at 80 km/h reference speed
P.veh.Crr          = 0.0030;       % ASSUMED: typical WSC-class low-rolling-resistance tyre on tarmac (team said "unknown, use typical")
P.veh.r_wheel_m    = 0.275;        % GIVEN

end
