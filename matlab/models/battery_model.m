function [SOC_next, V_term, I_batt] = battery_model(SOC, P_net_W, dt_s, P)
%BATTERY_MODEL  One time-step update of a lumped OCV+R pack model.
%
%   [SOC_next, V_term, I_batt] = battery_model(SOC, P_net_W, dt_s, P)
%
%   SOC      : state of charge, 0-1, at the start of the step
%   P_net_W  : net power INTO the pack [W] (MPPT charge power minus motor
%              draw); positive = charging, negative = discharging
%   dt_s     : time step [s]
%   P        : struct from vehicle_params()
%
% Equivalent circuit: V_term = OCV(SOC) - I_batt * R_pack (discharge sign
% convention), solved from the quadratic P_net = V_term * I_batt.
% OCV(SOC) uses the ASSUMED generic curve in P.batt.soc_bp/ocv_pack_bp --
% replace with the real INR21700-50S datasheet curve when available.

SOC = min(max(SOC, 0), 1);
OCV = interp1(P.batt.soc_bp, P.batt.ocv_pack_bp, SOC, 'linear', 'extrap');
R   = P.batt.R_pack_ohm;

% P_net = OCV*I - I^2*R  (I>0 = charging into the pack against OCV... use
% consistent convention: I_batt>0 = charging (power flows OCV<-bus))
% Solve: R*I^2 - OCV*I + P_net = 0  for I (charging current, A)
a = R;
b = -OCV;
c = P_net_W;
disc = b^2 - 4*a*c;
disc = max(disc, 0);   % clamp: if demanded power exceeds what pack can source, current saturates at OCV/2R

if a > 0
    I1 = (-b + sqrt(disc)) / (2*a);
    I2 = (-b - sqrt(disc)) / (2*a);
    % pick the physically small-current root (the one closer to P_net/OCV)
    if abs(I1 - P_net_W/OCV) <= abs(I2 - P_net_W/OCV)
        I_batt = I1;
    else
        I_batt = I2;
    end
else
    I_batt = P_net_W / OCV;
end

V_term = OCV - I_batt * R;

Q_pack_As = P.batt.Q_pack_Ah * 3600;
SOC_next = SOC + (I_batt * dt_s) / Q_pack_As;
SOC_next = min(max(SOC_next, 0), 1);

end
