function [tau_per_motor_Nm, F_total_N, F_aero_N, F_roll_N, F_grade_N] = vehicle_dynamics(v_mps, accel_mps2, grade_rad, P)
%VEHICLE_DYNAMICS  Inverse longitudinal dynamics: speed/accel/grade -> required wheel torque.
%
%   [tau_per_motor_Nm, F_total_N, F_aero_N, F_roll_N, F_grade_N] = ...
%       vehicle_dynamics(v_mps, accel_mps2, grade_rad, P)
%
%   v_mps      : vehicle speed [m/s]
%   accel_mps2 : longitudinal acceleration dv/dt [m/s^2] (0 for steady cruise)
%   grade_rad  : road grade angle [rad] (0 = flat)
%   P          : struct from vehicle_params()
%
%   tau_per_motor_Nm is the propulsive torque required PER MOTOR
%   (F_total_N * r_wheel / P.motor.n_motors): positive = drive torque,
%   negative = regen/braking torque (e.g. decelerating faster than
%   aero+roll drag alone would slow the vehicle).

v_mps = v_mps(:);
accel_mps2 = accel_mps2(:) .* ones(size(v_mps));
grade_rad  = grade_rad(:)  .* ones(size(v_mps));

F_aero_N  = 0.5 * P.env.rho_air * P.veh.CdA_m2 * v_mps.^2 .* sign(v_mps);
F_roll_N  = P.veh.Crr * P.veh.mass_kg * P.env.g * cos(grade_rad) .* sign(v_mps);
F_grade_N = P.veh.mass_kg * P.env.g * sin(grade_rad);
F_inertia_N = P.veh.mass_kg * accel_mps2;

F_total_N = F_aero_N + F_roll_N + F_grade_N + F_inertia_N;

tau_wheel_Nm = F_total_N * P.veh.r_wheel_m;
tau_per_motor_Nm = tau_wheel_Nm / P.motor.n_motors;

end
