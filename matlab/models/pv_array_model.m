function [P_str, V_str, I_str, Tcell_C] = pv_array_model(G_Wm2, Tamb_C, P)
%PV_ARRAY_MODEL  Per-string MPP power/voltage/current for the solar array.
%
%   [P_str, V_str, I_str, Tcell_C] = pv_array_model(G_Wm2, Tamb_C, P)
%
%   G_Wm2   : plane-of-array irradiance [W/m^2] (scalar or vector)
%   Tamb_C  : ambient temperature [degC]        (scalar or vector, same size as G)
%   P       : struct from vehicle_params()
%
%   Returns per-string (129S1P) values; multiply by P.solar.n_strings for
%   whole-array totals, or feed each string's [V_str,I_str] into a
%   separate mppt_model() call (1 MPPT per string, as built).
%
% Model: NOCT cell temperature -> linear Pmax/Voc temperature derating ->
% STC MPP scaled by irradiance ratio -> field derate factor applied so the
% array totals match the team-declared 1200 W field rating.

G_Wm2  = G_Wm2(:);
Tamb_C = Tamb_C(:);

% Cell temperature (NOCT model)
Tcell_C = Tamb_C + (G_Wm2 / 800) * (P.solar.NOCT_C - 20);

dT = Tcell_C - P.env.T_ref_C;
pmax_factor = 1 + (P.solar.tempco_Pmax_pct_per_C / 100) * dT;
voc_factor  = 1 + (P.solar.tempco_Voc_pct_per_C  / 100) * dT;

irr_ratio = G_Wm2 / P.env.G_ref;

% MPP power per string: STC value * irradiance ratio * temp derate * field derate
P_str = P.solar.Pmp_string_stc .* irr_ratio .* pmax_factor .* P.solar.field_derate;
P_str = max(P_str, 0);

% MPP voltage: STC value * temp derate only (Vmp ~ weakly dependent on G near MPP)
V_str = P.solar.Vmp_string_stc .* voc_factor;
V_str = max(V_str, 1e-3);

% MPP current from P = V*I
I_str = P_str ./ V_str;

end
