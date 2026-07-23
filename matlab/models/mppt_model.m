function [P_out, eff] = mppt_model(V_in, I_in, V_out, P)
%MPPT_MODEL  Boost-converter output power using the measured efficiency map.
%
%   [P_out, eff] = mppt_model(V_in, I_in, V_out, P)
%
%   V_in, I_in : MPP voltage/current delivered by one solar string [V, A]
%   V_out      : DC bus / battery terminal voltage the MPPT boosts into [V]
%   P          : struct from vehicle_params()
%
%   eff is looked up from P.mppt.eff_table_csv (576 measured points,
%   Vin 5-140V / Iin 0.5-7A / Vout 25-160V; boost topology so only
%   Vin < Vout combinations were tested/valid). Uses scattered linear
%   interpolation with nearest-neighbour extrapolation at the data edges.
%
% Persistent interpolant so the CSV is parsed only once per session.

persistent F csv_path_cached

if isempty(F) || ~strcmp(csv_path_cached, P.mppt.eff_table_csv)
    T = readtable(P.mppt.eff_table_csv);
    F = scatteredInterpolant(T.Vin_V, T.Iin_A, T.Vout_V, T.Efficiency_pct, ...
        'linear', 'nearest');
    csv_path_cached = P.mppt.eff_table_csv;
end

V_in  = V_in(:);
I_in  = I_in(:);
V_out = V_out(:) .* ones(size(V_in));

eff_pct = F(V_in, I_in, V_out);
eff_pct = min(max(eff_pct, 0), 100);
eff = eff_pct / 100;

P_in  = V_in .* I_in;
P_out = P_in .* eff;

end
