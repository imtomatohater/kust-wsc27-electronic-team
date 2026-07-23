function [P_elec_W, Tw_next_C, iRMS_A, P_loss_W] = motor_model(tau_Nm, omega_rad_s, Tw_C, Tamb_C, dt_s, P)
%MOTOR_MODEL  MARAND in-wheel PMSM electrical + thermal model, one step.
%
%   [P_elec_W, Tw_next_C, iRMS_A, P_loss_W] = motor_model(tau_Nm, omega_rad_s, Tw_C, Tamb_C, dt_s, P)
%
%   tau_Nm      : commanded torque, per motor [Nm] (+drive / -regen)
%   omega_rad_s : motor angular speed = v / r_wheel [rad/s] (in-wheel, no gearbox)
%   Tw_C        : winding temperature state at start of step [degC]
%   Tamb_C      : ambient temperature [degC]
%   dt_s        : time step [s]
%   P           : struct from vehicle_params()
%
%   P_elec_W is the electrical power drawn FROM the bus (positive =
%   motoring load, negative = regen power returned to bus). Sign follows
%   tau_Nm: P_elec = tau*omega + P_loss, so tau<0 (braking) with omega>0
%   naturally gives negative P_elec once |tau*omega| > P_loss.
%
% Equations per the team's MARAND thermal/electrical model
% (steady-state + transient worksheets; constants in P.motor.*):
%   Tm  = (Tamb + Tw) / 2                        [K, averaged magnet temp]
%   B   = Ba + B_tempco*(Tm - 293)
%   iRMS= ki * B * |tau|
%   Rw  = Rw0 * (1 + Rw_tempco*(Tw - 293))
%   Pc  = 3 * iRMS^2 * Rw                        [copper loss]
%   Pe  = eddy_k * (B*omega)^2                   [eddy current loss]
%   dTw/dt = (Pc+Pe - (Tw-Tamb)/RTherm) / CTherm  [lumped RC thermal model]

Tm_K = ((Tamb_C + 273.15) + (Tw_C + 273.15)) / 2;
B    = P.motor.Ba_T + P.motor.B_tempco_T_per_K * (Tm_K - 293);

iRMS_A = P.motor.ki_A_per_NmT * B * abs(tau_Nm);

Tw_K = Tw_C + 273.15;
Rw   = P.motor.Rw0_ohm * (1 + P.motor.Rw_tempco_per_K * (Tw_K - 293));

Pc = 3 * iRMS_A.^2 * Rw;
Pe = P.motor.eddy_k * (B * omega_rad_s).^2;
P_loss_W = Pc + Pe;

P_mech_W = tau_Nm .* omega_rad_s;
P_elec_W = P_mech_W + P_loss_W;

dTw = (P_loss_W - (Tw_C - Tamb_C) / P.motor.RTherm_K_per_W) / P.motor.CTherm_J_per_K;
Tw_next_C = Tw_C + dTw * dt_s;

end
