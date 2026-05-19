#!/usr/bin/env python3
"""
cost_model.py
=============
Tier-1 asteroid-mining cost-per-kg interactive model.

Implements the equation chain in cost_model_reference.md Section 3.1:

    Step 1 — resource mass per asteroid (volume * density * fraction * eta_ext)
    Step 2 — propellant mass via Tsiolkovsky rocket equation
    Step 3 — wet mass = dry + propellant
    Step 4 — launch cost = wet mass * $/kg_to_LEO
    Step 5 — operations cost = $/day * mission duration
    Step 6 — development cost amortized over fleet size N
    Step 7 — total mission cost
    Step 8 — delivered mass = resource * return efficiency
    Step 9 — fleet-aggregate $/kg = (N * C_mission) / sum(delivered_i)

Inputs:
    sbdb_lowell_merged.csv  (with best_tax_class from enrich_taxonomy.py)

Outputs:
    cost_model_output/cost_model_tier1.html      — interactive page
    cost_model_output/fleet_cost_summary.csv     — defaults snapshot

Usage:
    uv run python cost_model.py
"""

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

from bokeh.layouts import column, row, Spacer
from bokeh.models import (
    ColumnDataSource, CustomJS, Div, Select, Slider as BokehSlider,
)
from bokeh.plotting import figure, output_file, save


SCRIPT_DIR  = Path(__file__).resolve().parent
DEFAULT_INPUT  = "sbdb_lowell_merged.csv"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "cost_model_output"
MAX_PLOT_POINTS = 1500


# ---------------------------------------------------------------------------
# Defaults from cost_model_reference.md (Section 5)
# ---------------------------------------------------------------------------

# Mission architecture sliders (Section 5.2)
DELTA_V_DEFAULT_KMPS  = 6.0     # range 3 – 12
ISP_DEFAULT_S         = 320     # range 300 – 3000
M_DRY_DEFAULT_KG      = 2000    # range 500 – 10,000
MISSION_DAYS_DEFAULT  = 730     # range 180 – 1,825
SURFACE_DAYS_DEFAULT  = 90      # range 30 – 365
FLEET_SIZE_DEFAULT    = 5       # range 1 – 50
ETA_EXT_DEFAULT       = 0.50    # range 0.10 – 0.95
ETA_RET_DEFAULT       = 0.80    # range 0.50 – 0.99

# Cost sliders (Section 5.3)
LAUNCH_USD_PER_KG_DEFAULT = 2_000        # range 100 – 10,000
OPS_USD_PER_DAY_DEFAULT   = 50_000       # range 5,000 – 500,000
DEV_USD_TOTAL_DEFAULT     = 500_000_000  # range 50M – 5B

# Heliocentric / Earth constants for Tier-2 orbital Δv (Section 3.2)
MU_SUN_KM3_S2 = 1.327_124_400_18e11    # km^3 / s^2
AU_KM         = 1.495_978_707e8        # km
V_EARTH_KMS   = 29.7847                # km/s, Earth's mean orbital speed
V_LEO_KMS     = 7.73                   # km/s, circular ~400 km LEO
V_ESC_KMS     = math.sqrt(2.0) * V_LEO_KMS  # km/s, escape from LEO altitude (√2 × V_LEO ≈ 10.927)


# Earth benchmarks (Section 2)
EARTH_BENCHMARKS = [
    ("Iron / steel",       0.12),
    ("Nickel",             16.0),
    ("Cobalt",             32.0),
    ("Platinum",           31000.0),
    ("Palladium",          45000.0),
    ("Gold",               60000.0),
    ("Iridium",            50000.0),
    ("Water — LEO depot",  1250.0),
    ("LOX/LH2 — LEO",      900.0),
]


# ---------------------------------------------------------------------------
# Family classification (mirrors resource_slider_bounds.py for consistency)
# ---------------------------------------------------------------------------

def clean_taxonomy(value):
    if pd.isna(value):
        return None
    s = str(value).strip().upper()
    if s in {"", "NAN", "NONE", "NULL"}:
        return None
    s = re.sub(r"[^A-Z0-9]+", "", s)
    return s or None


def first_tax_letter(value):
    if value is None:
        return None
    m = re.search(r"[A-Z]", value)
    return m.group(0) if m else None


def classify_material_family(tax):
    t = clean_taxonomy(tax)
    letter = first_tax_letter(t)
    if letter in {"C", "B", "F", "G", "P", "D", "T"}:
        return "primitive_carbonaceous_like"
    if letter in {"S", "Q", "K", "L", "V", "A", "R"}:
        return "ordinary_stony_chondritic_like"
    if letter in {"M", "X", "E"}:
        return "metal_rich_iron_meteorite_like"
    return "other_or_unknown"


def estimate_diameter_km(df):
    for col in ["diameter", "lowell_iras_diameter_km"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "diameter" in df.columns and "lowell_iras_diameter_km" in df.columns:
        return df["diameter"].combine_first(df["lowell_iras_diameter_km"])
    if "diameter" in df.columns:
        return df["diameter"]
    if "lowell_iras_diameter_km" in df.columns:
        return df["lowell_iras_diameter_km"]
    return pd.Series(np.nan, index=df.index)


def spherical_volume_m3(d_km):
    if d_km is None or pd.isna(d_km) or d_km <= 0:
        return 0.0
    d_m = float(d_km) * 1000.0
    return (math.pi / 6.0) * (d_m ** 3)


# ---------------------------------------------------------------------------
# Tier-2 orbital Δv (simplified Shoemaker-Helin, per cost_model_reference §3.2)
#
# Assumes a Hohmann-like transfer between Earth at 1 AU and the asteroid's
# perihelion q. Round-trip cost = departure burn (Oberth from LEO) +
# rendezvous burn + plane-change penalty + return (taken as 80% of departure
# to credit a free-return / aerobraked re-entry trajectory).
#
# Returns (Δv_total km/s, one-way transfer days). Both NaN if orbit data
# is missing or degenerate.
# ---------------------------------------------------------------------------

def orbital_delta_v_and_transit(a_au, e, i_deg, q_au=None):
    """Estimate outbound Δv (km/s) and one-way transit time (days).

    Returns only the *outbound* Δv (departure burn + arrival rendezvous +
    plane change).  The return Δv is handled in the JS callback so it can be
    varied interactively via the ret_factor slider.
    """
    try:
        a = float(a_au)
        ecc = float(e)
        inc = float(i_deg)
    except (TypeError, ValueError):
        return float("nan"), float("nan")
    if any(pd.isna(x) for x in (a, ecc, inc)):
        return float("nan"), float("nan")
    if a <= 0 or ecc < 0 or ecc >= 1:
        return float("nan"), float("nan")

    if q_au is None or pd.isna(q_au):
        q = a * (1.0 - ecc)
    else:
        try:
            q = float(q_au)
        except (TypeError, ValueError):
            q = a * (1.0 - ecc)
    if q <= 0:
        return float("nan"), float("nan")

    # Transfer ellipse: perihelion = min(1, q) AU, aphelion = max(1, q) AU
    a_t_au = (1.0 + q) / 2.0
    if a_t_au <= 0:
        return float("nan"), float("nan")

    # Transfer-ellipse speed when crossing 1 AU (where Earth lives).
    # Derived from vis-viva: sqrt(μ * (2/r - 1/a_t)), simplified to
    # v_earth * sqrt(2q / (1 + q)) when r = 1 AU.
    v_t_at_earth = V_EARTH_KMS * math.sqrt(2.0 * q / (1.0 + q))
    v_inf_dep    = abs(v_t_at_earth - V_EARTH_KMS)

    # Departure burn from LEO with Oberth boost
    dv_dep = math.sqrt(v_inf_dep ** 2 + V_ESC_KMS ** 2) - V_LEO_KMS

    # Asteroid heliocentric speed at its perihelion
    inner_term = 2.0 / q - 1.0 / a
    if inner_term <= 0:
        return float("nan"), float("nan")
    v_ast_peri = V_EARTH_KMS * math.sqrt(inner_term)

    # Transfer-ellipse speed at the asteroid's perihelion
    v_t_at_q = V_EARTH_KMS * math.sqrt(max(2.0 / q - 2.0 / (1.0 + q), 0.0))

    dv_arr = abs(v_ast_peri - v_t_at_q)

    # Plane change is cheapest at the slowest point of the transfer
    v_slowest = min(v_t_at_earth, v_t_at_q)
    dv_plane  = 2.0 * v_slowest * math.sin(math.radians(inc) / 2.0)

    dv_out = dv_dep + dv_arr + dv_plane

    # One-way transit time = π * sqrt(a_t^3 / μ_sun) (Kepler's 3rd law / 2)
    a_t_km     = a_t_au * AU_KM
    t_transit_s = math.pi * math.sqrt(a_t_km ** 3 / MU_SUN_KM3_S2)
    t_transit_d = t_transit_s / 86400.0

    return dv_out, t_transit_d


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",      type=str, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        input_path = SCRIPT_DIR / args.input
    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {args.input}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    html_path = args.output_dir / "cost_model_tier1.html"

    print(f"Loaded input: {input_path}")
    df = pd.read_csv(input_path, low_memory=False)
    print(f"Rows: {len(df):,}")

    # Use the enriched taxonomy column if present.
    tax_col = "best_tax_class" if "best_tax_class" in df.columns else "lowell_iras_tax_class"
    print(f"Using taxonomy column: {tax_col}")

    df["material_family"] = df[tax_col].map(classify_material_family)
    df["diameter_km"]     = estimate_diameter_km(df)
    df["volume_m3"]       = df["diameter_km"].apply(spherical_volume_m3)

    # Per-asteroid Tier-2 orbital Δv and transit time
    for col in ("a", "e", "i", "q"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    delta_v_results = df.apply(
        lambda r: orbital_delta_v_and_transit(r.get("a"), r.get("e"),
                                              r.get("i"), r.get("q")),
        axis=1,
    )
    df["dv_out_kmps"] = [v[0] for v in delta_v_results]
    df["transit_days"]  = [v[1] for v in delta_v_results]

    # Drop rows with zero/missing volume — they can't host a mission.
    valid = df[df["volume_m3"] > 0].copy()
    print(f"Rows with usable diameter: {len(valid):,}")

    # How many of those also have a computable Δv (Tier 2 eligible)?
    tier2_ok = valid["dv_out_kmps"].notna().sum()
    print(f"Tier-2-eligible (full orbit data): {tier2_ok:,}")
    if tier2_ok > 0:
        print(f"  Median Δv: {valid['dv_out_kmps'].median():.2f} km/s   "
              f"5th–95th pct: {valid['dv_out_kmps'].quantile(0.05):.2f}"
              f" – {valid['dv_out_kmps'].quantile(0.95):.2f} km/s")
        print(f"  Median one-way transit: {valid['transit_days'].median():.0f} days")

    # Family codes for the JS-side lookup
    fam_order = [
        "primitive_carbonaceous_like",
        "ordinary_stony_chondritic_like",
        "metal_rich_iron_meteorite_like",
        "other_or_unknown",
    ]
    fam_labels = ["Primitive", "Stony", "Metal-rich", "Other"]
    family_codes = {fam: i for i, fam in enumerate(fam_order)}
    valid["family_code"] = valid["material_family"].map(
        lambda x: family_codes.get(x, 3)
    )

    volumes      = valid["volume_m3"].astype(float).tolist()
    family_codes_list = valid["family_code"].astype(int).tolist()
    names        = valid["name"].fillna(valid.get("pdes", "")).astype(str).tolist() \
                   if "name" in valid.columns else [""] * len(valid)
    pdes         = valid.get("pdes", pd.Series(["?"] * len(valid))).astype(str).tolist()
    # Tier-2 Δv and transit time per asteroid. NaN encoded as -1 for the
    # JS callback (which uses sentinel checks).
    dv_out       = valid["dv_out_kmps"].fillna(-1.0).astype(float).tolist()
    transit_days = valid["transit_days"].fillna(-1.0).astype(float).tolist()

    raw = ColumnDataSource(data={
        "volume_m3":    volumes,
        "family_code":  family_codes_list,
        "name":         names,
        "pdes":         pdes,
        "dv_out":       dv_out,
        "transit_days": transit_days,
    })

    # Sorted-curve plot source (top-N fleet cumulative delivered mass)
    plot_source = ColumnDataSource(data={"rank": [], "cum_kg": []})

    # ---- Widgets -----------------------------------------------------------
    resource_select = Select(
        title="Resource mode",
        value="metal",
        options=[("metal", "Metal feedstock"), ("water", "Water / propellant")],
        width=300,
    )

    # Tier-2 toggle: when "tier2", each mission uses its asteroid's specific
    # Δv and transit time. When "tier1", every mission uses the global Δv
    # and mission-duration sliders.
    tier_select = Select(
        title="Model tier",
        value="tier2",
        options=[
            ("tier1", "Tier 1 — uniform Δv slider"),
            ("tier2", "Tier 2 — per-asteroid Δv (orbital)"),
        ],
        width=300,
    )

    # Per-family density and resource-fraction sliders
    dens_p  = BokehSlider(start=1500, end=2500, value=2000, step=50,   title="Primitive density (kg/m³)")
    dens_s  = BokehSlider(start=3000, end=3800, value=3400, step=50,   title="Stony density (kg/m³)")
    dens_m  = BokehSlider(start=7000, end=8000, value=7800, step=50,   title="Metal-rich density (kg/m³)")
    dens_o  = BokehSlider(start=2500, end=3500, value=3000, step=50,   title="Other density (kg/m³)")

    metal_p = BokehSlider(start=0.00, end=0.05,  value=0.025, step=0.001, title="Primitive metal fraction")
    metal_s = BokehSlider(start=0.05, end=0.20,  value=0.12,  step=0.001, title="Stony metal fraction")
    metal_m = BokehSlider(start=0.60, end=0.95,  value=0.80,  step=0.005, title="Metal-rich metal fraction")
    metal_o = BokehSlider(start=0.00, end=0.20,  value=0.10,  step=0.001, title="Other metal fraction")

    water_p = BokehSlider(start=0.00, end=0.15,  value=0.08,  step=0.001, title="Primitive water fraction")
    water_s = BokehSlider(start=0.00, end=0.02,  value=0.005, step=0.0005, title="Stony water fraction")
    water_m = BokehSlider(start=0.00, end=0.005, value=0.001, step=0.0001, title="Metal-rich water fraction")
    water_o = BokehSlider(start=0.00, end=0.05,  value=0.01,  step=0.001, title="Other water fraction")

    # Mission architecture sliders (Section 5.2)
    # Tier 1: uniform Δv. Tier 2: ignored (per-asteroid Δv used instead).
    delta_v   = BokehSlider(start=3.0,  end=12.0,  value=DELTA_V_DEFAULT_KMPS, step=0.1, title="Tier-1 Δv (km/s)")
    # Tier 2: multiplier on the Hohmann-derived Δv (1.0 = pure Hohmann,
    # >1.0 adds margin for non-optimal launch windows / mid-course corrections).
    dv_scale  = BokehSlider(start=1.0,  end=2.0,   value=1.15, step=0.05, title="Tier-2 Δv scale factor")
    isp       = BokehSlider(start=300,  end=3000,  value=ISP_DEFAULT_S,        step=10,  title="Specific impulse Iₛₚ (s)")
    m_dry     = BokehSlider(start=500,  end=10000, value=M_DRY_DEFAULT_KG,     step=100, title="Spacecraft dry mass (kg)")
    # Tier 1: uniform mission duration. Tier 2: only the surface-time slider
    # matters (transit time is computed from each asteroid's orbit).
    mission_d = BokehSlider(start=180,  end=1825,  value=MISSION_DAYS_DEFAULT, step=10,  title="Tier-1 mission duration (days)")
    surface_d = BokehSlider(start=30,   end=365,   value=SURFACE_DAYS_DEFAULT, step=10,  title="Tier-2 surface time (days)")
    fleet_n   = BokehSlider(start=1,    end=50,    value=FLEET_SIZE_DEFAULT,   step=1,   title="Fleet size N")
    eta_ext   = BokehSlider(start=0.10, end=0.95,  value=ETA_EXT_DEFAULT,     step=0.01, title="Extraction efficiency ηₑₓₜ")
    eta_ret   = BokehSlider(start=0.50, end=0.99,  value=ETA_RET_DEFAULT,     step=0.01, title="Return efficiency η_ret")
    ret_factor = BokehSlider(start=0.0,  end=1.0,   value=0.60, step=0.05, title="Return Δv factor (fraction of outbound)")
    # Tier-2 Δv cap: filter out asteroids requiring more than this to avoid
    # polluting the fleet with un-reachable targets.
    dv_cap    = BokehSlider(start=5.0,  end=25.0,  value=15.0, step=0.5, title="Tier-2 Δv ceiling (km/s)")

    # Cost sliders (Section 5.3)
    launch_usd = BokehSlider(start=100,        end=10000,        value=LAUNCH_USD_PER_KG_DEFAULT, step=100,    title="Launch price ($/kg to LEO)")
    ops_usd    = BokehSlider(start=5000,       end=500000,       value=OPS_USD_PER_DAY_DEFAULT,   step=1000,   title="Operations cost ($/day)")
    dev_usd    = BokehSlider(start=50_000_000, end=5_000_000_000,value=DEV_USD_TOTAL_DEFAULT,     step=10_000_000, title="Development cost (USD total)")

    # Output displays
    title_div   = Div(text="<h1 style='margin-bottom:4px'>Asteroid Mining — Cost Model</h1>"
                            "<p style='color:#555;margin-top:0'>"
                            "Fleet-aggregate cost per kilogram delivered to LEO, with Tier-2 per-asteroid "
                            "Δv computed from orbital elements. "
                            "Powered by 41,171-asteroid dataset with Neese + MITHNEOS + SDSS + Lowell taxonomies.</p>")
    headline    = Div(width=900)
    breakdown   = Div(width=900)
    benchmarks  = Div(width=900)

    # ---- CustomJS callback -------------------------------------------------
    benchmarks_json = "[" + ",".join(
        f'{{"name":"{n}","price":{p}}}' for n, p in EARTH_BENCHMARKS
    ) + "]"

    callback = CustomJS(
        args=dict(
            raw=raw, source=plot_source,
            resource_select=resource_select, tier_select=tier_select,
            dens_p=dens_p, dens_s=dens_s, dens_m=dens_m, dens_o=dens_o,
            metal_p=metal_p, metal_s=metal_s, metal_m=metal_m, metal_o=metal_o,
            water_p=water_p, water_s=water_s, water_m=water_m, water_o=water_o,
            delta_v=delta_v, dv_scale=dv_scale, dv_cap=dv_cap,
            isp=isp, m_dry=m_dry, mission_d=mission_d, surface_d=surface_d,
            fleet_n=fleet_n, eta_ext=eta_ext, eta_ret=eta_ret,
            ret_factor=ret_factor,
            launch_usd=launch_usd, ops_usd=ops_usd, dev_usd=dev_usd,
            headline=headline, breakdown=breakdown, benchmarks=benchmarks,
        ),
        code=f"""
const benchmarks = {benchmarks_json};
const MAX_PTS = {MAX_PLOT_POINTS};

const vol     = raw.data['volume_m3'];
const fam     = raw.data['family_code'];
const dvAst   = raw.data['dv_out'];        // km/s outbound per asteroid, -1 = unknown
const ttAst   = raw.data['transit_days'];  // one-way days per asteroid

const mode = resource_select.value;
const tier = tier_select.value;            // "tier1" | "tier2"

const density = [dens_p.value, dens_s.value, dens_m.value, dens_o.value];
const frac = (mode === "metal")
    ? [metal_p.value, metal_s.value, metal_m.value, metal_o.value]
    : [water_p.value, water_s.value, water_m.value, water_o.value];

const dVUniform = delta_v.value;           // km/s (Tier 1)
const dVScale   = dv_scale.value;          // multiplier (Tier 2)
const dVCap     = dv_cap.value;            // ceiling (Tier 2)
const ve        = isp.value * 9.80665e-3;  // km/s, g0 = 9.80665×10⁻³ km/s² (SI-defined)
const mDry      = m_dry.value;             // kg
const tUniform  = mission_d.value;         // days (Tier 1)
const tSurface  = surface_d.value;         // days (Tier 2)
const N         = Math.max(1, Math.floor(fleet_n.value));
const eExt      = eta_ext.value;
const eRet      = eta_ret.value;
const retFactor = ret_factor.value;        // return Δv = retFactor × outbound Δv

const launchPerKg = launch_usd.value;
const opsPerDay   = ops_usd.value;
const devTotal    = dev_usd.value;

// --- Per-asteroid mission cost + delivered mass ----
const n = vol.length;
const deliverable = new Array(n);
const cMission    = new Array(n);
const tierEligible = new Array(n);   // false if Tier 2 wants this asteroid filtered out

for (let i = 0; i < n; i++) {{
    const f = fam[i] ?? 3;
    const resource = vol[i] * density[f] * frac[f] * eExt;
    deliverable[i] = resource * eRet;

    let dV, tDays;
    if (tier === "tier2") {{
        const dVRaw = dvAst[i];
        if (dVRaw < 0 || dVRaw > dVCap) {{
            tierEligible[i] = false;
            cMission[i] = Infinity;
            continue;
        }}
        dV    = dVRaw * dVScale;
        tDays = (ttAst[i] > 0) ? (2.0 * ttAst[i] + tSurface) : tUniform;
        tierEligible[i] = true;
    }} else {{
        dV    = dVUniform;
        tDays = tUniform;
        tierEligible[i] = true;
    }}

    // Two-stage rocket equation: return stage (carries cargo) sized first,
    // then outbound stage (carries return propellant) sized on top.
    const dV_ret   = dV * retFactor;
    const cargo    = deliverable[i];
    const mPropRet = (mDry + cargo)    * (Math.exp(dV_ret / ve) - 1.0);
    const mPropOut = (mDry + mPropRet) * (Math.exp(dV     / ve) - 1.0);
    const mWet     = mDry + mPropRet + mPropOut;
    const cLau  = mWet * launchPerKg;
    const cOps  = opsPerDay * tDays;
    const cDev  = devTotal / N;
    cMission[i] = cLau + cOps + cDev;
}}

// --- Fleet selection ----
// Tier 1: cost is uniform, so just sort by deliverable mass (highest first).
// Tier 2: per-mission cost varies, so sort by cost-effectiveness
//         (delivered kg per $) — most $/kg-friendly missions first.
const idx = [];
for (let i = 0; i < n; i++) {{ if (tierEligible[i]) idx.push(i); }}
if (tier === "tier2") {{
    idx.sort((a, b) => (deliverable[b] / cMission[b]) - (deliverable[a] / cMission[a]));
}} else {{
    idx.sort((a, b) => deliverable[b] - deliverable[a]);
}}

const Nclamped = Math.min(N, idx.length);
let totalDeliveredKg = 0;
let totalFleetCost   = 0;
let famCount = [0, 0, 0, 0];
let famMass  = [0, 0, 0, 0];
let sumDv    = 0;
let sumDur   = 0;
for (let k = 0; k < Nclamped; k++) {{
    const i = idx[k];
    totalDeliveredKg += deliverable[i];
    totalFleetCost   += cMission[i];
    const f = fam[i] ?? 3;
    famCount[f] += 1;
    famMass[f]  += deliverable[i];
    if (tier === "tier2") {{
        sumDv  += dvAst[i] * dVScale;
        sumDur += (ttAst[i] > 0) ? (2.0 * ttAst[i] + tSurface) : tUniform;
    }} else {{
        sumDv  += dVUniform;
        sumDur += tUniform;
    }}
}}

const avgDv  = (Nclamped > 0) ? sumDv  / Nclamped : 0;
const avgDur = (Nclamped > 0) ? sumDur / Nclamped : 0;
const costPerKg = (totalDeliveredKg > 0) ? (totalFleetCost / totalDeliveredKg) : Infinity;

// --- Format outputs ---------------------------------------------------------
function fmtUsd(v) {{
    if (!isFinite(v)) return "∞";
    if (v >= 1e9) return "$" + (v / 1e9).toFixed(2) + " B";
    if (v >= 1e6) return "$" + (v / 1e6).toFixed(2) + " M";
    if (v >= 1e3) return "$" + (v / 1e3).toFixed(2) + " k";
    return "$" + v.toFixed(2);
}}
function fmtKg(v) {{
    if (!isFinite(v)) return "—";
    if (v >= 1e9) return (v / 1e9).toFixed(2) + " Bn kg";
    if (v >= 1e6) return (v / 1e6).toFixed(2) + " Mn kg";
    if (v >= 1e3) return (v / 1e3).toFixed(2) + " k kg";
    return v.toFixed(1) + " kg";
}}
function fmtPerKg(v) {{
    if (!isFinite(v)) return "∞";
    if (v >= 1e6) return "$" + (v / 1e6).toFixed(2) + " M/kg";
    if (v >= 1e3) return "$" + (v / 1e3).toFixed(1) + " k/kg";
    if (v >= 1)   return "$" + v.toFixed(2) + "/kg";
    return "$" + v.toFixed(4) + "/kg";
}}

const modeLabel = (mode === "metal") ? "metal feedstock" : "water / propellant";
const tierLabel = (tier === "tier2") ? "Tier 2 (orbital Δv)" : "Tier 1 (uniform Δv)";
headline.text =
  "<div style='font-family:system-ui,sans-serif;padding:14px 18px;background:#0b1d2a;color:#fff;border-radius:6px'>"
  + "<div style='font-size:13px;opacity:0.7'>Fleet-aggregate cost ("+modeLabel+", "+tierLabel+")</div>"
  + "<div style='font-size:54px;font-weight:600;letter-spacing:-1px'>" + fmtPerKg(costPerKg) + "</div>"
  + "<div style='font-size:14px;opacity:0.85'>"
  +   "<b>"+Nclamped+"</b> mission" + (Nclamped===1?"":"s") + " · "
  +   "Total fleet cost <b>" + fmtUsd(totalFleetCost) + "</b> · "
  +   "Total delivered <b>" + fmtKg(totalDeliveredKg) + "</b>"
  +   ((tier === "tier2") ? " · Avg Δv <b>" + avgDv.toFixed(2) + " km/s</b> · Avg duration <b>" + avgDur.toFixed(0) + " days</b>" : "")
  + "</div>"
  + "</div>";

const famLabels = ["Primitive","Stony","Metal-rich","Other"];
let famRows = "";
for (let f = 0; f < 4; f++) {{
    if (famCount[f] === 0) continue;
    famRows += "<tr><td style='padding:2px 12px'>"+famLabels[f]+"</td>"
            +  "<td style='text-align:right;padding:2px 12px'>"+famCount[f]+"</td>"
            +  "<td style='text-align:right;padding:2px 12px'>"+fmtKg(famMass[f])+"</td></tr>";
}}

// Average per-mission cost details across the selected fleet
const avgPerMissionCost = (Nclamped > 0) ? totalFleetCost / Nclamped : 0;
const repDv  = (tier === "tier2") ? avgDv  : dVUniform;
const repDur = (tier === "tier2") ? avgDur : tUniform;
const repDvRet    = repDv * retFactor;
const repPropRet  = mDry * (Math.exp(repDvRet / ve) - 1.0);  // representative: no cargo (worst-case lean)
const repPropOut  = (mDry + repPropRet) * (Math.exp(repDv / ve) - 1.0);
const repProp     = repPropRet + repPropOut;
const repWet      = mDry + repProp;
const repLau  = repWet * launchPerKg;
const repOps  = opsPerDay * repDur;
const repDev  = devTotal / N;

const eligibilityNote = (tier === "tier2")
    ? "<br>Tier-2 eligibility: <b>" + idx.length.toLocaleString() + "</b> asteroids reachable under the Δv ceiling of " + dVCap.toFixed(1) + " km/s."
    : "";
const sortRule = (tier === "tier2")
    ? "cost-effectiveness (kg delivered per dollar)"
    : "raw deliverable mass";

breakdown.text =
  "<div style='font-family:system-ui,sans-serif;padding:10px 14px;background:#f7f7f7;border-radius:6px'>"
  + "<b>Representative per-mission breakdown</b> "
  +   "(" + ((tier==="tier2") ? "averaged across the " + Nclamped + " selected asteroids" : "uniform across the fleet") + "): "
  +   "Δv " + repDv.toFixed(2) + " km/s (out) + " + repDvRet.toFixed(2) + " km/s (ret) · "
  +   "prop (out) " + fmtKg(repPropOut) + " · prop (ret) " + fmtKg(repPropRet) + " · "
  +   "wet mass "   + fmtKg(repWet)  + " · "
  +   "launch "     + fmtUsd(repLau) + " · "
  +   "ops "        + fmtUsd(repOps) + " · "
  +   "dev/mission "+ fmtUsd(repDev) + " · "
  +   "total "      + fmtUsd(avgPerMissionCost)
  +   eligibilityNote
  + "<br><br>"
  + "<b>Top-"+Nclamped+" asteroids selected by "+sortRule+":</b>"
  + "<table style='border-collapse:collapse;margin-top:6px'>"
  + "<tr style='background:#e0e0e0'><th style='padding:2px 12px;text-align:left'>Family</th>"
  +   "<th style='padding:2px 12px;text-align:right'>Missions</th>"
  +   "<th style='padding:2px 12px;text-align:right'>Total delivered</th></tr>"
  + famRows
  + "</table>"
  + "</div>";

// --- Earth benchmark comparison ----
let benchRows = "";
for (let b = 0; b < benchmarks.length; b++) {{
    const bm = benchmarks[b];
    const ratio = costPerKg / bm.price;
    const verdict = ratio <= 1
        ? "<span style='color:#0a7d1a;font-weight:600'>BEATS by " + (1/ratio).toFixed(1) + "×</span>"
        : "<span style='color:#a33'>" + ratio.toFixed(1) + "× more expensive</span>";
    benchRows += "<tr><td style='padding:2px 12px'>"+bm.name+"</td>"
              +  "<td style='text-align:right;padding:2px 12px'>"+fmtPerKg(bm.price)+"</td>"
              +  "<td style='padding:2px 12px'>"+verdict+"</td></tr>";
}}
benchmarks.text =
  "<div style='font-family:system-ui,sans-serif;padding:10px 14px;background:#fffbe6;border:1px solid #f0e2a0;border-radius:6px'>"
  + "<b>Earth-market benchmarks</b> (per kg)"
  + "<table style='border-collapse:collapse;margin-top:6px'>"
  + "<tr style='background:#f4ecc4'><th style='padding:2px 12px;text-align:left'>Commodity</th>"
  +   "<th style='padding:2px 12px;text-align:right'>Earth price</th>"
  +   "<th style='padding:2px 12px;text-align:left'>vs your model</th></tr>"
  + benchRows
  + "</table>"
  + "</div>";

// --- Cumulative delivered-mass curve ----
const maxRank = Math.min(n, 1000);
const stride  = Math.max(1, Math.floor(maxRank / MAX_PTS));
const xs = [];
const cum = [];
let running = 0;
for (let k = 0; k < maxRank; k++) {{
    running += deliverable[idx[k]];
    if (k % stride === 0 || k === maxRank - 1) {{
        xs.push(k + 1);
        cum.push(running);
    }}
}}
source.data = {{rank: xs, cum_kg: cum}};
source.change.emit();
"""
    )

    # Wire callback to every slider/select
    for w in [resource_select, tier_select,
              dens_p, dens_s, dens_m, dens_o,
              metal_p, metal_s, metal_m, metal_o,
              water_p, water_s, water_m, water_o,
              delta_v, dv_scale, dv_cap,
              isp, m_dry, mission_d, surface_d,
              fleet_n, eta_ext, eta_ret, ret_factor,
              launch_usd, ops_usd, dev_usd]:
        w.js_on_change("value", callback)

    # Cumulative delivered-mass figure
    fig = figure(width=900, height=320, title="Cumulative delivered mass — top 1,000 ranked asteroids",
                 x_axis_label="Asteroid rank (by deliverable mass)",
                 y_axis_label="Cumulative kg delivered",
                 y_axis_type="log",
                 tools="pan,wheel_zoom,reset,save")
    fig.line(x="rank", y="cum_kg", source=plot_source, line_width=2)

    # Layout
    arch_col = column(
        Div(text="<h3 style='margin:8px 0 0'>Model tier</h3>"),
        tier_select,
        Div(text="<h3 style='margin:8px 0 0'>Mission architecture</h3>"),
        delta_v, dv_scale, dv_cap,
        isp, m_dry, mission_d, surface_d,
        fleet_n, eta_ext, eta_ret, ret_factor,
        width=320,
    )
    cost_col = column(
        Div(text="<h3 style='margin:8px 0 0'>Cost</h3>"),
        launch_usd, ops_usd, dev_usd,
        Div(text="<h3 style='margin:8px 0 0'>Resource mode</h3>"),
        resource_select,
        width=320,
    )
    res_col = column(
        Div(text="<h3 style='margin:8px 0 0'>Per-family densities</h3>"),
        dens_p, dens_s, dens_m, dens_o,
        Div(text="<h3 style='margin:8px 0 0'>Metal fractions</h3>"),
        metal_p, metal_s, metal_m, metal_o,
        Div(text="<h3 style='margin:8px 0 0'>Water fractions</h3>"),
        water_p, water_s, water_m, water_o,
        width=320,
    )

    layout = column(
        title_div,
        headline,
        Spacer(height=8),
        benchmarks,
        Spacer(height=8),
        breakdown,
        Spacer(height=8),
        fig,
        row(arch_col, cost_col, res_col),
    )

    output_file(str(html_path), title="Asteroid Mining Cost Model — Tier 1 + 2")
    save(layout)
    print(f"Wrote {html_path.name}")

    # Also dump per-asteroid Tier-2 Δv distribution as a CSV for inspection
    tier2_csv = args.output_dir / "tier2_per_asteroid_dv.csv"
    cols = [c for c in ("pdes", "name", "a", "e", "i", "q",
                        "dv_out_kmps", "transit_days", "material_family",
                        "diameter_km") if c in valid.columns]
    valid[cols].sort_values("dv_out_kmps").to_csv(tier2_csv, index=False)
    print(f"Wrote {tier2_csv.name}")

    # Defaults snapshot CSV (handy reference / regression check)
    snapshot = pd.DataFrame([{
        "delta_v_kmps":            DELTA_V_DEFAULT_KMPS,
        "isp_s":                   ISP_DEFAULT_S,
        "m_dry_kg":                M_DRY_DEFAULT_KG,
        "mission_duration_days":   MISSION_DAYS_DEFAULT,
        "fleet_size":              FLEET_SIZE_DEFAULT,
        "eta_extraction":          ETA_EXT_DEFAULT,
        "eta_return":              ETA_RET_DEFAULT,
        "launch_usd_per_kg_LEO":   LAUNCH_USD_PER_KG_DEFAULT,
        "ops_usd_per_day":         OPS_USD_PER_DAY_DEFAULT,
        "dev_usd_total":           DEV_USD_TOTAL_DEFAULT,
    }])
    snap_path = args.output_dir / "fleet_cost_defaults.csv"
    snapshot.to_csv(snap_path, index=False)
    print(f"Wrote {snap_path.name}")

    print()
    print(f"Output directory: {args.output_dir.resolve()}")
    print("Open cost_model_tier1.html in a browser to use the interactive model.")


if __name__ == "__main__":
    main()
)
    print(f"Wrote {tier2_csv.name}")

    # Defaults snapshot CSV (handy reference / regression check)
    snapshot = pd.DataFrame([{
        "delta_v_kmps":            DELTA_V_DEFAULT_KMPS,
        "isp_s":                   ISP_DEFAULT_S,
        "m_dry_kg":                M_DRY_DEFAULT_KG,
        "mission_duration_days":   MISSION_DAYS_DEFAULT,
        "fleet_size":              FLEET_SIZE_DEFAULT,
        "eta_extraction":          ETA_EXT_DEFAULT,
        "eta_return":              ETA_RET_DEFAULT,
        "launch_usd_per_kg_LEO":   LAUNCH_USD_PER_KG_DEFAULT,
        "ops_usd_per_day":         OPS_USD_PER_DAY_DEFAULT,
        "dev_usd_total":           DEV_USD_TOTAL_DEFAULT,
    }])
    snap_path = args.output_dir / "fleet_cost_defaults.csv"
    snapshot.to_csv(snap_path, index=False)
    print(f"Wrote {snap_path.name}")

    print()
    print(f"Output directory: {args.output_dir.resolve()}")
    print("Open cost_model_tier1.html in a browser to use the interactive model.")


if __name__ == "__main__":
    main()
