#!/usr/bin/env python3
"""
build_website_data.py
=====================
Single source of truth for the asteroid-data.json the website consumes.

This script lives in the Asteroid_Mining repo (alongside the merge,
taxonomy-enrichment, and cost-model code) so that the bhaskar-jain
website repo stays a pure presentation layer — it just renders the
JSON this script produces.

Inputs (all from this folder):
    sbdb_lowell_merged.csv     ← built by merge_sbdb_lowell.py + enrich_taxonomy.py
    sdss_taxonomy.csv          ← from sdss_taxonomy_crossmatch.py
    mithneos_taxonomy.csv      ← from mithneos_real_pull.py (or fallback)
    neese_taxonomy.csv         ← from neese_mithneos_enrichment.py

Output:
    ../bhaskar-jain/public/thoughts/asteroid-mining/asteroid-data.json
    (override the destination with --output)

Pipeline (mirrors what the website previously did in its own scripts/):
    1. Load merged catalog + four-tier taxonomy enrichment
    2. Resolve diameter (SBDB.diameter → Lowell IRAS fallback)
    3. Filter to NEAs with full orbital data + measured diameter
    4. Classify into the four compositional families
    5. Compute outbound Δv (Hohmann + Oberth + plane change)
    6. Compute one-way Hohmann transfer time
    7. Sort ascending by Δv, write compact JSON

Usage:
    uv run python build_website_data.py
    uv run python build_website_data.py --output some/other/path.json
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent

CSV_PATH         = SCRIPT_DIR / "sbdb_lowell_merged.csv"
SDSS_TAX_CSV     = SCRIPT_DIR / "sdss_taxonomy.csv"
NEESE_TAX_CSV    = SCRIPT_DIR / "neese_taxonomy.csv"
MITHNEOS_TAX_CSV = SCRIPT_DIR / "mithneos_taxonomy.csv"

# By convention the website lives as a sibling repo. Override with --output
# when running from elsewhere (CI, manual data dumps, etc.).
DEFAULT_OUTPUT = (
    SCRIPT_DIR.parent / "bhaskar-jain" / "public" / "thoughts"
    / "asteroid-mining" / "asteroid-data.json"
)


# ---------------------------------------------------------------------------
# Physical constants (heliocentric)
# ---------------------------------------------------------------------------
AU_KM       = 1.495_978_707e8       # km
MU_SUN      = 1.327_124_400_18e11   # km^3 / s^2
V_EARTH     = math.sqrt(MU_SUN / AU_KM)  # ≈ 29.78 km/s
V_LEO       = 7.727                 # km/s — circular ~400 km LEO
V_ESC       = math.sqrt(2.0) * V_LEO  # km/s — escape velocity from LEO altitude (√2 × V_LEO ≈ 10.927)
DV_MAX_KM_S = 20.0                  # accessibility ceiling for the model


# ---------------------------------------------------------------------------
# Taxonomy family classification
# ---------------------------------------------------------------------------
PRIMITIVE_LETTERS = set("CBFGPDT")
STONY_LETTERS     = set("SQKLVAR")
METAL_LETTERS     = set("MXE")


def _first_alpha(s: str) -> str | None:
    for ch in str(s).upper():
        if ch.isalpha():
            return ch
    return None


def classify_family(spec_b, spec_t,
                    mithneos_fam=None, neese_fam=None, sdss_fam=None) -> int:
    """Priority cascade: SBDB spec_B/spec_T → MITHNEOS → Neese → SDSS → unknown."""
    for val in (spec_b, spec_t):
        if pd.notna(val) and str(val).strip():
            letter = _first_alpha(str(val).strip())
            if letter in PRIMITIVE_LETTERS: return 0
            if letter in STONY_LETTERS:     return 1
            if letter in METAL_LETTERS:     return 2
    for fam in (mithneos_fam, neese_fam, sdss_fam):
        if fam is not None and pd.notna(fam):
            code = int(fam)
            if code in (0, 1, 2):
                return code
    return 3


# ---------------------------------------------------------------------------
# Geometry / orbital mechanics
# ---------------------------------------------------------------------------

def sphere_volume_m3(diameter_km: float) -> float:
    d_m = diameter_km * 1000.0
    return (math.pi / 6.0) * d_m ** 3


def compute_dv_out(a_au: float, e: float, i_deg: float) -> float | None:
    """Outbound Δv (km/s) = departure (Oberth) + arrival + inclination plane-change.

    Hohmann transfer between Earth (1 AU) and asteroid perihelion q = a(1-e).
    Returns None if orbital parameters are out of range or Δv exceeds the
    accessibility ceiling.
    """
    if not (0 < e < 1 and 0 < a_au < 4 and 0 <= i_deg <= 180):
        return None

    q_au = a_au * (1.0 - e)
    q_km = q_au * AU_KM
    a_km = a_au * AU_KM
    a_tr = ((1.0 + q_au) / 2.0) * AU_KM

    val = 2.0 / AU_KM - 1.0 / a_tr
    if val <= 0:
        return None
    v_tr_E = math.sqrt(MU_SUN * val)
    v_inf  = abs(v_tr_E - V_EARTH)
    dv_dep = math.sqrt(v_inf ** 2 + V_ESC ** 2) - V_LEO
    if dv_dep < 0:
        dv_dep = abs(dv_dep)

    val_ast = 2.0 / q_km - 1.0 / a_km
    val_tr  = 2.0 / q_km - 1.0 / a_tr
    if val_ast <= 0 or val_tr <= 0:
        return None
    dv_arr = abs(math.sqrt(MU_SUN * val_ast) - math.sqrt(MU_SUN * val_tr))

    r_aph_km = max(AU_KM, q_km)
    val_aph  = 2.0 / r_aph_km - 1.0 / a_tr
    if val_aph <= 0:
        val_aph = 1.0 / a_tr
    v_aph    = math.sqrt(MU_SUN * max(val_aph, 0.0))
    dv_plane = 2.0 * v_aph * math.sin(math.radians(i_deg) / 2.0)

    dv_out = dv_dep + dv_arr + dv_plane
    if not math.isfinite(dv_out) or dv_out <= 0 or dv_out > DV_MAX_KM_S:
        return None
    return round(dv_out, 4)


def transfer_time_days(a_au: float, e: float) -> float | None:
    q_au = a_au * (1.0 - e)
    a_tr = ((1.0 + q_au) / 2.0) * AU_KM
    try:
        return round(math.pi * math.sqrt(a_tr ** 3 / MU_SUN) / 86400.0, 1)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Enrichment merge helper (number → number with designation fallback)
# ---------------------------------------------------------------------------

def _merge_enrichment(df: pd.DataFrame, enrich_df: pd.DataFrame,
                      col_renames: dict[str, str]) -> pd.DataFrame:
    enrich_df = enrich_df.copy()
    enrich_df["number"] = pd.to_numeric(
        enrich_df["number"], errors="coerce"
    ).astype("Int64")
    enrich_df["designation"] = enrich_df["designation"].fillna("").str.strip()
    out_cols = list(col_renames.values())

    df["_pdes_num"] = pd.to_numeric(df["pdes"], errors="coerce").astype("Int64")
    by_num = (
        enrich_df[enrich_df["number"].notna() & (enrich_df["number"] > 0)]
        .rename(columns={**col_renames, "number": "_pdes_num"})[["_pdes_num"] + out_cols]
        .copy()
    )
    df = df.merge(by_num, on="_pdes_num", how="left")

    unmatched = df[out_cols[0]].isna()
    if unmatched.any():
        by_des = (
            enrich_df[enrich_df["designation"] != ""]
            .rename(columns={**col_renames, "designation": "_des_key"})[["_des_key"] + out_cols]
            .copy()
        )
        df["_des_key"] = df["pdes"].astype(str).str.strip()
        sub = df[unmatched][["_des_key"]].merge(by_des, on="_des_key", how="left")
        for col in out_cols:
            if col in sub.columns:
                df.loc[unmatched, col] = sub[col].values
        df.drop(columns=["_des_key"], inplace=True)

    df.drop(columns=["_pdes_num"], inplace=True)
    return df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="Destination JSON path (default: %(default)s)")
    args = parser.parse_args()

    if not CSV_PATH.exists():
        log.error("Merged CSV not found at %s — run make_all.py first.", CSV_PATH)
        return 1

    log.info("Reading: %s", CSV_PATH)
    df = pd.read_csv(CSV_PATH, low_memory=False)
    log.info("  Loaded %d rows, %d columns", len(df), len(df.columns))

    # --- Load taxonomy enrichment CSVs (graceful fallback if missing) ----
    if SDSS_TAX_CSV.exists():
        log.info("  Loading SDSS taxonomy: %s", SDSS_TAX_CSV.name)
        sdss_df = pd.read_csv(SDSS_TAX_CSV, low_memory=False)
        sdss_df["sdss_fam"] = pd.to_numeric(sdss_df["sdss_fam"], errors="coerce")
        df = _merge_enrichment(df, sdss_df, {
            "sdss_fam": "sdss_fam", "sdss_tax_class": "sdss_tax_class",
            "a_star": "a_star", "sdss_n_obs": "sdss_n_obs",
        })
        log.info("    SDSS matched: %d / %d",
                 df["sdss_fam"].notna().sum(), len(df))
    else:
        log.warning("  SDSS file not found — skipping that tier.")
        for col in ("sdss_fam", "a_star", "sdss_n_obs"):
            df[col] = np.nan
        df["sdss_tax_class"] = ""

    if MITHNEOS_TAX_CSV.exists():
        log.info("  Loading MITHNEOS taxonomy: %s", MITHNEOS_TAX_CSV.name)
        mith_df = pd.read_csv(MITHNEOS_TAX_CSV, low_memory=False)
        mith_df["fam"] = pd.to_numeric(mith_df["fam"], errors="coerce")
        df = _merge_enrichment(df, mith_df, {
            "fam": "mithneos_fam", "tax_class": "mithneos_tax_class",
        })
        log.info("    MITHNEOS matched: %d / %d",
                 df["mithneos_fam"].notna().sum(), len(df))
    else:
        log.warning("  MITHNEOS file not found — skipping.")
        df["mithneos_fam"] = np.nan
        df["mithneos_tax_class"] = ""

    if NEESE_TAX_CSV.exists():
        log.info("  Loading Neese taxonomy: %s", NEESE_TAX_CSV.name)
        neese_df = pd.read_csv(NEESE_TAX_CSV, low_memory=False)
        neese_df["fam"] = pd.to_numeric(neese_df["fam"], errors="coerce")
        df = _merge_enrichment(df, neese_df, {
            "fam": "neese_fam", "tax_class": "neese_tax_class",
            "tax_source": "neese_tax_source",
        })
        log.info("    Neese matched: %d / %d",
                 df["neese_fam"].notna().sum(), len(df))
    else:
        log.warning("  Neese file not found — skipping.")
        df["neese_fam"] = np.nan
        df["neese_tax_class"] = ""
        df["neese_tax_source"] = ""

    # --- Resolve diameter ----
    df["_diam_km"] = pd.to_numeric(df["diameter"], errors="coerce").combine_first(
        pd.to_numeric(df.get("lowell_iras_diameter_km"), errors="coerce")
    )

    # --- Orbital elements ----
    for col in ("a", "e", "i"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # --- Filter to usable NEAs ----
    mask = (
        df["e"].between(0.0, 0.9999, inclusive="neither")
        & df["a"].between(0.1, 4.0, inclusive="neither")
        & df["i"].between(0.0, 180.0)
        & df["_diam_km"].gt(0)
    )
    df = df[mask].copy()
    log.info("  After filter (e<1, 0.1<a<4 AU, diameter>0): %d rows", len(df))

    # --- Family assignment ----
    df["fam"] = df.apply(
        lambda r: classify_family(
            r.get("spec_B"), r.get("spec_T"),
            r.get("mithneos_fam"), r.get("neese_fam"), r.get("sdss_fam"),
        ),
        axis=1,
    )

    # Source-of-classification breakdown (for reporting + JSON meta block)
    has_spec  = (df["spec_B"].notna() & df["spec_B"].astype(str).str.strip().ne("")) | \
                (df["spec_T"].notna() & df["spec_T"].astype(str).str.strip().ne(""))
    has_mith  = df["mithneos_fam"].notna() & ~has_spec
    has_neese = df["neese_fam"].notna()    & ~has_spec & ~has_mith
    has_sdss  = df["sdss_fam"].notna()     & ~has_spec & ~has_mith & ~has_neese
    n_spec     = int(has_spec.sum())
    n_mithneos = int((has_mith  & df["fam"].ne(3)).sum())
    n_neese    = int((has_neese & df["fam"].ne(3)).sum())
    n_sdss     = int((has_sdss  & df["fam"].ne(3)).sum())
    n_unk      = int((df["fam"] == 3).sum())

    fam_counts = df["fam"].value_counts().sort_index().to_dict()
    log.info("  Family counts: %s", fam_counts)
    log.info("  Sources: spec=%d, mithneos=%d, neese=%d, sdss=%d, unknown=%d",
             n_spec, n_mithneos, n_neese, n_sdss, n_unk)

    # --- Volume, Δv, transfer time ----
    df["vol_m3"]          = df["_diam_km"].apply(sphere_volume_m3)
    log.info("  Computing outbound Δv ...")
    df["dv_out"]          = df.apply(lambda r: compute_dv_out(r["a"], r["e"], r["i"]), axis=1)
    df["t_transfer_days"] = df.apply(lambda r: transfer_time_days(r["a"], r["e"]), axis=1)
    df = df[df["dv_out"].notna()].copy()
    log.info("  Valid Δv (≤ %.0f km/s ceiling): %d", DV_MAX_KM_S, len(df))

    df = df.sort_values("dv_out").reset_index(drop=True)

    # --- Build compact records ----
    records = [
        [
            round(float(r["a"]),      4),
            round(float(r["e"]),      4),
            round(float(r["i"]),      3),
            round(float(r["vol_m3"]), 3),
            int(r["fam"]),
            float(r["dv_out"]),
            round(float(r["t_transfer_days"]), 1),
        ]
        for _, r in df.iterrows()
    ]

    fam_final = {str(k): int((df["fam"] == k).sum()) for k in range(4)}

    out_obj = {
        "meta": {
            "build_date": str(date.today()),
            "source":     "JPL SBDB + Lowell AstOrb (sbdb_lowell_merged.csv)",
            "filter":     "e in (0,1), a in (0.1,4) AU, diameter available",
            "count":      len(records),
            "schema":     ["a_au", "e", "i_deg", "vol_m3", "fam",
                           "dv_out_km_s", "t_transfer_days"],
            "sort_order": "ascending dv_out_km_s",
            "fam_legend": {
                "0": "primitive_carbonaceous_like",
                "1": "ordinary_stony_chondritic_like",
                "2": "metal_rich_iron_like",
                "3": "unknown_unclassified",
            },
            "fam_counts": fam_final,
            "taxonomy_sources": {
                "tier1_sbdb_spectral":     n_spec,
                "tier2_mithneos_nir":      n_mithneos,
                "tier3_neese_compilation": n_neese,
                "tier4_sdss_photometric":  n_sdss,
                "tier5_unknown":           n_unk,
                "mithneos_present":        MITHNEOS_TAX_CSV.exists(),
                "neese_present":           NEESE_TAX_CSV.exists(),
                "sdss_present":            SDSS_TAX_CSV.exists(),
            },
            "dv_method": (
                "Simplified Hohmann to asteroid perihelion. "
                "dv_out = dv_dep(Oberth LEO) + dv_arr(rendezvous) + dv_plane(inclination). "
                "Return dv handled by return_factor slider in the website model."
            ),
            "dv_stats_km_s": {
                "min":    round(df["dv_out"].min(),          3),
                "p10":    round(df["dv_out"].quantile(0.10), 3),
                "median": round(df["dv_out"].median(),       3),
                "p90":    round(df["dv_out"].quantile(0.90), 3),
                "max":    round(df["dv_out"].max(),          3),
            },
        },
        "asteroids": records,
    }

    out_path = args.output
    if not out_path.parent.exists():
        log.error("Destination folder does not exist: %s", out_path.parent)
        log.error("If your website repo lives elsewhere, pass --output <path>.")
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out_obj, f, separators=(",", ":"))

    size_kb = out_path.stat().st_size / 1024
    log.info("Wrote %s  (%.0f KB, %d asteroids)",
             out_path, size_kb, len(records))
    log.info("  Δv range: %.2f – %.2f km/s, median %.2f",
             out_obj["meta"]["dv_stats_km_s"]["min"],
             out_obj["meta"]["dv_stats_km_s"]["max"],
             out_obj["meta"]["dv_stats_km_s"]["median"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
"__main__":
    sys.exit(main())
