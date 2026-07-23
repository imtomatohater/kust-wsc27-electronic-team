function results = run_sils_sim(t_s, v_kmh, G_Wm2, Tamb_C, grade_rad, SOC0)
%RUN_SILS_SIM  Top-level SILS loop: Solar -> MPPT -> Battery -> Inverter/Motor -> Vehicle.
%
%   results = run_sils_sim(t_s, v_kmh, G_Wm2, Tamb_C, grade_rad, SOC0)
%
%   t_s       : time vector [s], uniform or non-uniform, length N
%   v_kmh     : target vehicle speed profile [km/h], length N
%   G_Wm2     : plane-of-array irradiance profile [W/m^2], length N (or scalar)
%   Tamb_C    : ambient temperature profile [degC], length N (or scalar)
%   grade_rad : road grade profile [rad], length N (or scalar), default 0
%   SOC0      : initial battery state of charge [0-1], default 0.8
%
%   With no input arguments, runs a short built-in demo profile so the
%   whole SILS chain can be sanity-checked without external drive-cycle
%   data (run: `results = run_sils_sim();`).
%
%   This mirrors the block diagram in the project's SILS summary doc:
%   solar array -> per-string MPPT -> shared DC bus/battery -> inverter ->
%   in-wheel motor -> vehicle longitudinal dynamics -> speed feedback.
%   It is a plain time-stepped MATLAB implementation (no Simulink
%   toolbox required); each function under matlab/models/ maps 1:1 to a
%   block and can be dropped into a Simulink MATLAB Function block later.

if nargin == 0
    t_s   = (0:1:3600)';                      % 1 hour, 1 s steps
    v_kmh = 80 * ones(size(t_s));              % steady 80 km/h cruise
    G_Wm2 = 900 + 100*sin(2*pi*t_s/1800);       % irradiance wobble (clouds)
    Tamb_C = 35 * ones(size(t_s));
    grade_rad = zeros(size(t_s));
    SOC0 = 0.8;
end
if nargin < 5 || isempty(grade_rad), grade_rad = 0; end
if nargin < 6 || isempty(SOC0), SOC0 = 0.8; end

P = vehicle_params();

N = numel(t_s);
t_s   = t_s(:);
v_kmh = v_kmh(:) .* ones(N,1);
G_Wm2 = G_Wm2(:)  .* ones(N,1);
Tamb_C = Tamb_C(:) .* ones(N,1);
grade_rad = grade_rad(:) .* ones(N,1);

v_mps = v_kmh / 3.6;
accel_mps2 = [0; diff(v_mps) ./ diff(t_s)];

% state
SOC = SOC0;
Tw_C = Tamb_C(1);   % motor winding starts at ambient

results.t_s        = t_s;
results.v_kmh       = v_kmh;
results.SOC         = nan(N,1);
results.V_batt      = nan(N,1);
results.I_batt      = nan(N,1);
results.P_solar_W    = nan(N,1);
results.P_motor_bus_W = nan(N,1);
results.P_net_batt_W  = nan(N,1);
results.Tw_C        = nan(N,1);
results.tau_motor_Nm = nan(N,1);
results.mppt_eff     = nan(N, P.solar.n_strings);

for k = 1:N
    dt_s = 0; if k < N, dt_s = t_s(k+1) - t_s(k); end

    % --- Solar array + MPPT (per string, then summed) ---
    [P_str, V_str, I_str] = pv_array_model(G_Wm2(k), Tamb_C(k), P);
    P_str_out = zeros(1, P.solar.n_strings);
    eff_row = zeros(1, P.solar.n_strings);
    for s = 1:P.solar.n_strings
        [P_str_out(s), eff_row(s)] = mppt_model(V_str, I_str, P.batt.V_nom_pack, P);
    end
    P_solar_bus_W = sum(P_str_out);

    % --- Vehicle dynamics -> required motor torque ---
    omega_rad_s = v_mps(k) / P.veh.r_wheel_m;
    tau_per_motor_Nm = vehicle_dynamics(v_mps(k), accel_mps2(k), grade_rad(k), P);

    % --- Motor + inverter ---
    [P_elec_motor_W, Tw_C, ~, ~] = motor_model(tau_per_motor_Nm, omega_rad_s, Tw_C, Tamb_C(k), max(dt_s,1e-6), P);
    P_elec_motor_total_W = P.motor.n_motors * P_elec_motor_W;
    if P_elec_motor_total_W >= 0
        P_motor_bus_W = P_elec_motor_total_W / P.inverter.eff;   % motoring: extra draw from bus
    else
        P_motor_bus_W = P_elec_motor_total_W * P.inverter.eff;   % regen: reduced return to bus
    end

    % --- Battery ---
    P_net_batt_W = P_solar_bus_W - P_motor_bus_W;
    [SOC_next, V_term, I_batt] = battery_model(SOC, P_net_batt_W, max(dt_s,1e-6), P);

    % --- log ---
    results.SOC(k)          = SOC;
    results.V_batt(k)        = V_term;
    results.I_batt(k)        = I_batt;
    results.P_solar_W(k)     = P_solar_bus_W;
    results.P_motor_bus_W(k) = P_motor_bus_W;
    results.P_net_batt_W(k)  = P_net_batt_W;
    results.Tw_C(k)          = Tw_C;
    results.tau_motor_Nm(k)  = tau_per_motor_Nm;
    results.mppt_eff(k,:)    = eff_row;

    SOC = SOC_next;
end

results.params = P;

if nargout == 0
    figure;
    subplot(3,1,1); plot(t_s, results.SOC*100); ylabel('SOC (%)'); grid on;
    subplot(3,1,2); plot(t_s, results.P_solar_W, t_s, results.P_motor_bus_W); ...
        legend('P_{solar->bus}','P_{motor<-bus}'); ylabel('W'); grid on;
    subplot(3,1,3); plot(t_s, results.Tw_C); ylabel('Motor winding T (degC)'); xlabel('t (s)'); grid on;
end

end
