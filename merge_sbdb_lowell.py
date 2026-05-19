# merge_sbdb_lowell.py
#
# Guarantees:
# - Output keeps ONLY the original SBDB/SDD rows
# - No Lowell-only rows are introduced
# - Each SBDB row appears exactly once
# - Output row count == input SBDB row count
#
# Usage:
#   python merge_sbdb_lowell.py
#   python merge_sbdb_lowell.py --sbdb SDD_API_test_cleaned.csv --lowell lowell_astorb.csv --output sbdb_lowell_merged.csv

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Optional

import pandas as pd


DEFAULT_SBDB_CANDIDATES = [
    "SDD_API_test_cleaned.csv",
    "SSD_API_test_cleaned.csv",
    "SDD_API_test.csv",
    "SSD_API_test.csv",
]

DEFAULT_LOWELL_CANDIDATES = [
    "lowell_astorb.csv",
    "Lowell.csv",
    "lowell.csv",
]

DEFAULT_OUTPUT = "sbdb_lowell_merged.csv"

LOWELL_KEEP_COLS = [
    "number",
    "name_or_designation",
    "H",
    "G",
    "B_V",
    "iras_diameter_km",
    "iras_tax_class",
    "orbital_arc_days",
    "n_observations",
    "epoch_yyyymmdd",
    "mean_anomaly_deg",
    "arg_perihelion_deg",
    "long_asc_node_deg",
    "inclination_deg",
    "eccentricity",
    "semimajor_axis_au",
    "ceu_arcsec",
    "ceu_rate_arcsec_per_day",
    "ceu_date_yyyymmdd",
    "next_peu_arcsec",
    "next_peu_date_yyyymmdd",
    "max_peu_10y_arcsec",
    "max_peu_10y_date_yyyymmdd",
    "post_obs_max_peu_10y_arcsec",
    "post_obs_max_peu_10y_date_yyyymmdd",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge SBDB NEA CSV with Lowell CSV safely.")
    parser.add_argument("--sbdb", type=str, default=None, help="Path to SBDB CSV")
    parser.add_argument("--lowell", type=str, default=None, help="Path to Lowell CSV")
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT, help="Output merged CSV path")
    return parser.parse_args()


def find_file(explicit_path: Optional[str], candidates: list[str], label: str) -> Path:
    if explicit_path:
        p = Path(explicit_path)
        if not p.exists():
            raise FileNotFoundError(f"{label} file not found: {p}")
        return p

    for c in candidates:
        p = Path(c)
        if p.exists():
            return p

    raise FileNotFoundError(f"Could not find {label} file. Tried: {', '.join(candidates)}")


def normalize_text(value: object) -> str:
    if pd.isna(value):
        return ""
    s = str(value).strip().upper()
    if s in {"", "NAN", "NONE", "NULL"}:
        return ""

    s = s.replace("(", " ").replace(")", " ")
    s = s.replace("_", " ").replace("-", " ")
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Provisional asteroid designation pattern (after normalize_text strips parens):
#   YYYY + space + 1–3 uppercase letters + optional 1+ digits
# e.g. "1979 XB", "2024 AB1", "1992 YD3". These must NOT be treated as
# numbered asteroids — the leading 4-digit field is a year, not the
# asteroid's permanent number.
_PROVISIONAL_DESIG_RE = re.compile(r"^\d{4}\s+[A-Z]{1,3}\d*$")


def extract_leading_number(text: object) -> Optional[int]:
    """Extract the permanent asteroid number from an SBDB full_name string.

    SBDB convention:
      - Numbered asteroids:   "433 Eros (A898 PA)"
      - Unnumbered asteroids: "(1979 XB)"  ← leading "(" is the giveaway

    Returns None for unnumbered asteroids, otherwise the asteroid number.
    Two guards are stacked because the older single-regex version silently
    pulled the YEAR out of unnumbered designations (e.g. extracting 1979
    from "(1979 XB)") and that collided with unrelated permanent
    asteroid #1979 during the Lowell merge.
    """
    if pd.isna(text):
        return None
    raw = str(text).strip()
    # Guard 1: SBDB wraps unnumbered designations in parentheses.
    if raw.startswith("("):
        return None
    s = normalize_text(text)
    if not s:
        return None
    # Guard 2: even after normalisation, a bare "YYYY LL[N]" pattern is a
    # provisional designation rather than a numbered asteroid.
    if _PROVISIONAL_DESIG_RE.match(s):
        return None
    m = re.match(r"^(\d+)\b", s)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def extract_designation_candidate(text: object) -> str:
    s = normalize_text(text)
    if not s:
        return ""

    # Remove leading asteroid number if present
    s_wo_num = re.sub(r"^\d+\s*", "", s).strip()
    return s_wo_num or s


def make_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def dedupe_lowell_by_key(lowell: pd.DataFrame, key_col: str) -> pd.DataFrame:
    """
    Keep one Lowell row per join key using a quality heuristic:
      1) highest n_observations
      2) highest orbital_arc_days
      3) lowest ceu_arcsec
    Uses sort_values + drop_duplicates to preserve all columns including the key.
    """
    usable = lowell[lowell[key_col].notna() & (lowell[key_col].astype(str) != "")].copy()
    if usable.empty:
        return usable

    # Use prefixed column names (lowell_*) since rename happens before dedupe
    sort_cols: list[str] = []
    ascending: list[bool] = []
    for col, asc in [
        ("lowell_n_observations", False),
        ("lowell_orbital_arc_days", False),
        ("lowell_ceu_arcsec", True),
    ]:
        if col in usable.columns:
            usable[col] = pd.to_numeric(usable[col], errors="coerce")
            sort_cols.append(col)
            ascending.append(asc)

    if sort_cols:
        usable = usable.sort_values(sort_cols, ascending=ascending, na_position="last")

    return usable.drop_duplicates(subset=[key_col], keep="first").reset_index(drop=True)


def main() -> None:
    args = parse_args()

    sbdb_path = find_file(args.sbdb, DEFAULT_SBDB_CANDIDATES, "SBDB")
    lowell_path = find_file(args.lowell, DEFAULT_LOWELL_CANDIDATES, "Lowell")
    output_path = Path(args.output)

    print(f"Loading SBDB file:   {sbdb_path}")
    print(f"Loading Lowell file: {lowell_path}")

    sbdb = pd.read_csv(sbdb_path, low_memory=False)
    lowell = pd.read_csv(lowell_path, low_memory=False)

    original_sbdb_len = len(sbdb)

    print(f"SBDB shape:   {sbdb.shape}")
    print(f"Lowell shape: {lowell.shape}")

    # Keep only useful Lowell columns that exist
    lowell_keep = [c for c in LOWELL_KEEP_COLS if c in lowell.columns]
    lowell = lowell[lowell_keep].copy()

    # -----------------------------
    # Build SBDB helper columns
    # -----------------------------
    sbdb = sbdb.copy()
    sbdb["sbdb_row_id"] = range(len(sbdb))

    if "pdes" in sbdb.columns:
        sbdb["sbdb_number_from_pdes"] = make_numeric(sbdb["pdes"]).astype("Int64")
        sbdb["sbdb_pdes_norm"] = sbdb["pdes"].map(normalize_text)
    else:
        sbdb["sbdb_number_from_pdes"] = pd.Series([pd.NA] * len(sbdb), dtype="Int64")
        sbdb["sbdb_pdes_norm"] = ""

    if "full_name" in sbdb.columns:
        sbdb["sbdb_number_from_full_name"] = sbdb["full_name"].map(extract_leading_number).astype("Int64")
        sbdb["sbdb_full_name_norm"] = sbdb["full_name"].map(normalize_text)
        sbdb["sbdb_full_name_key"] = sbdb["full_name"].map(extract_designation_candidate)
    else:
        sbdb["sbdb_number_from_full_name"] = pd.Series([pd.NA] * len(sbdb), dtype="Int64")
        sbdb["sbdb_full_name_norm"] = ""
        sbdb["sbdb_full_name_key"] = ""

    if "name" in sbdb.columns:
        sbdb["sbdb_name_norm"] = sbdb["name"].map(normalize_text)
        sbdb["sbdb_name_key"] = sbdb["name"].map(extract_designation_candidate)
    else:
        sbdb["sbdb_name_norm"] = ""
        sbdb["sbdb_name_key"] = ""

    sbdb["sbdb_number_key"] = sbdb["sbdb_number_from_pdes"].fillna(sbdb["sbdb_number_from_full_name"])

    # Priority text key: pdes, then full_name-derived, then name-derived
    sbdb["sbdb_text_key"] = sbdb["sbdb_pdes_norm"]
    mask = sbdb["sbdb_text_key"].eq("")
    sbdb.loc[mask, "sbdb_text_key"] = sbdb.loc[mask, "sbdb_full_name_key"]
    mask = sbdb["sbdb_text_key"].eq("")
    sbdb.loc[mask, "sbdb_text_key"] = sbdb.loc[mask, "sbdb_name_key"]

    # -----------------------------
    # Build Lowell helper columns
    # -----------------------------
    if "number" in lowell.columns:
        lowell["lowell_number_key"] = make_numeric(lowell["number"]).astype("Int64")
    else:
        lowell["lowell_number_key"] = pd.Series([pd.NA] * len(lowell), dtype="Int64")

    if "name_or_designation" in lowell.columns:
        lowell["lowell_text_key"] = lowell["name_or_designation"].map(extract_designation_candidate)
    else:
        lowell["lowell_text_key"] = ""

    # Prefix Lowell data columns so provenance is preserved
    rename_map = {}
    for col in lowell.columns:
        if col not in {"lowell_number_key", "lowell_text_key"}:
            rename_map[col] = f"lowell_{col}"
    lowell = lowell.rename(columns=rename_map)

    # -----------------------------
    # Create one-row Lowell lookups
    # -----------------------------
    lowell_number_lookup = dedupe_lowell_by_key(lowell, "lowell_number_key")
    lowell_text_lookup = dedupe_lowell_by_key(lowell, "lowell_text_key")

    print(f"Lowell unique number keys: {len(lowell_number_lookup):,}")
    print(f"Lowell unique text keys:   {len(lowell_text_lookup):,}")

    # -----------------------------
    # Pass 1: left merge by number
    # -----------------------------
    merged = sbdb.merge(
        lowell_number_lookup,
        left_on="sbdb_number_key",
        right_on="lowell_number_key",
        how="left",
        suffixes=("", "_dup"),
    )

    # Mark number matches
    number_matched = merged["lowell_number_key"].notna()
    merged["lowell_match_method"] = pd.NA
    merged.loc[number_matched, "lowell_match_method"] = "number"

    # -----------------------------
    # Pass 2: fill unmatched by text
    # -----------------------------
    unmatched_mask = merged["lowell_match_method"].isna()
    unmatched = merged.loc[unmatched_mask, ["sbdb_row_id", "sbdb_text_key"]].copy()

    text_fill = unmatched.merge(
        lowell_text_lookup,
        left_on="sbdb_text_key",
        right_on="lowell_text_key",
        how="left",
        suffixes=("", "_dup"),
    )

    # Lowell columns available to fill
    lowell_prefixed_cols = [c for c in lowell.columns if c.startswith("lowell_") and c not in {"lowell_number_key", "lowell_text_key"}]

    # Fill only unmatched rows, and only Lowell columns
    if not text_fill.empty:
        text_fill = text_fill.set_index("sbdb_row_id")
        merged = merged.set_index("sbdb_row_id")

        for col in lowell_prefixed_cols:
            if col in text_fill.columns:
                merged.loc[unmatched_mask.values, col] = merged.loc[unmatched_mask.values, col].combine_first(text_fill[col])

        # Also fill the helper key columns so provenance can show a text match happened
        for col in ["lowell_number_key", "lowell_text_key"]:
            if col in text_fill.columns:
                merged.loc[unmatched_mask.values, col] = merged.loc[unmatched_mask.values, col].combine_first(text_fill[col])

        # Set match method for text matches
        text_matched_ids = text_fill.index[text_fill["lowell_text_key"].notna()] if "lowell_text_key" in text_fill.columns else []
        merged.loc[text_matched_ids, "lowell_match_method"] = "designation"

        merged = merged.reset_index()

    # -----------------------------
    # Final provenance columns
    # -----------------------------
    merged["lowell_match_found"] = merged["lowell_match_method"].notna()
    merged["lowell_source_file"] = lowell_path.name

    merged["has_lowell_physical"] = False
    for col in ["lowell_iras_diameter_km", "lowell_iras_tax_class", "lowell_B_V", "lowell_H", "lowell_G"]:
        if col in merged.columns:
            merged["has_lowell_physical"] = merged["has_lowell_physical"] | merged[col].notna()

    # -----------------------------
    # Sanity checks
    # -----------------------------
    final_len = len(merged)
    if final_len != original_sbdb_len:
        raise RuntimeError(
            f"Row-count mismatch after merge: input SBDB had {original_sbdb_len}, output has {final_len}."
        )

    if merged["sbdb_row_id"].duplicated().any():
        dup_count = int(merged["sbdb_row_id"].duplicated().sum())
        raise RuntimeError(f"Duplicate SBDB rows found in output: {dup_count}")

    # -----------------------------
    # Drop internal helper columns
    # -----------------------------
    drop_cols = [
        "sbdb_number_from_pdes",
        "sbdb_number_from_full_name",
        "sbdb_full_name_norm",
        "sbdb_name_norm",
        "sbdb_pdes_norm",
        "sbdb_full_name_key",
        "sbdb_name_key",
        "sbdb_number_key",
        "sbdb_text_key",
        "lowell_number_key",
        "lowell_text_key",
    ]
    merged = merged.drop(columns=[c for c in drop_cols if c in merged.columns], errors="ignore")

    merged = merged.sort_values("sbdb_row_id").reset_index(drop=True)
    merged.to_csv(output_path, index=False)

    matched_rows = int(merged["lowell_match_found"].sum())
    matched_by_number = int((merged["lowell_match_method"] == "number").sum())
    matched_by_designation = int((merged["lowell_match_method"] == "designation").sum())

    print("\nMerge complete.")
    print(f"Output file: {output_path.resolve()}")
    print(f"Input SBDB rows:      {original_sbdb_len:,}")
    print(f"Output merged rows:   {len(merged):,}")
    print(f"Matched to Lowell:    {matched_rows:,}")
    print(f"  - by number:        {matched_by_number:,}")
    print(f"  - by designation:   {matched_by_designation:,}")
    print(f"Unmatched SBDB rows:  {len(merged) - matched_rows:,}")

    print("\nSuggested columns to inspect:")
    for col in [
        "pdes",
        "full_name",
        "lowell_number",
        "lowell_name_or_designation",
        "lowell_match_method",
        "lowell_iras_tax_class",
        "lowell_iras_diameter_km",
        "lowell_B_V",
        "lowell_orbital_arc_days",
        "lowell_ceu_arcsec",
    ]:
        if col in merged.columns:
            print(f"  - {col}")


if __name__ == "__main__":
    main()