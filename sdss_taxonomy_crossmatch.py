#!/usr/bin/env python3
"""
sdss_taxonomy_crossmatch.py
===========================
Tier 2 taxonomy enrichment: cross-matches the local asteroid catalog against
the Carvano et al. (2010) SDSS-based taxonomy dataset (hosted on NASA PDS SBN)
to assign photometric taxonomic family codes to asteroids that lack spectral
classifications in the JPL SBDB (spec_B / spec_T columns).

References
----------
- Carvano et al. (2010), A&A 510, A43
  "SDSS-based taxonomic classification and orbital distribution of main belt
  asteroids"
  https://www.aanda.org/articles/aa/full_html/2010/02/aa13322-09/aa13322-09.html

- Ivezic et al. (2001), AJ 122, 2749
  "Solar System Objects Observed in the Sloan Digital Sky Survey
  Commissioning Data"
  Defines the a* color index used for C/S separation.

- SDSS MOC4 (Ivezic et al. 2007)
  "The 4th Release of the Sloan Digital Sky Survey Moving Object Catalog"
  https://faculty.washington.edu/ivezic/sdssmoc/sdssmoc.html

- PDS SBN SDSS-based Asteroid Taxonomy dataset
  EAR-A-I0035-5-SDSSTAX-V1.1
  https://sbn.psi.edu/pds/resource/sdsstax.html

Taxonomy mapping
----------------
Carvano et al. (2010) define nine photometric classes that map to our family
codes as follows:

  Carvano class  -> Bus-DeMeo complex  -> fam code
  --------------------------------------------------
  Cp             -> C-complex          -> 0  (primitive / carbonaceous)
  Dp             -> D-complex          -> 0  (primitive / featureless red)
  Xp             -> X-complex          -> 2  (metal-rich / ambiguous)
  Sp             -> S-complex          -> 1  (stony / chondritic)
  Ap             -> A-type             -> 1
  Lp             -> L-type             -> 1
  Qp             -> Q-type             -> 1
  Op             -> O-type             -> 1
  Vp             -> V-type (basaltic)  -> 1

X-complex caveat: SDSS colors cannot separate E (enstatite, high-albedo),
M (metallic), and P (primitive, low-albedo) within the X-complex. All X-types
are assigned fam=2 here. Adding WISE albedo as Tier 1 enrichment would refine
this further.

a* color index (fallback from raw SDSS MOC4 photometry)
--------------------------------------------------------
When the pre-computed Carvano class is unavailable, we compute:

  a* = 0.89*(g-r) + 0.45*(r-i) - 0.57   (Ivezic et al. 2001)

  a* < 0.0               -> C-complex -> fam = 0
  a* >= 0.0, i-z < 0.1   -> S-complex -> fam = 1
  a* >= 0.0, i-z >= 0.1  -> X-complex -> fam = 2

Usage
-----
    python3 sdss_taxonomy_crossmatch.py

Output
------
    sdss_taxonomy.csv  (same directory as this script)
    Columns: number, designation, sdss_fam, sdss_tax_class, a_star, sdss_n_obs

Requirements
------------
    pip install requests pandas numpy
"""

from __future__ import annotations

import gzip
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

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
OUT_CSV    = SCRIPT_DIR / "sdss_taxonomy.csv"

# ---------------------------------------------------------------------------
# Remote data sources
# ---------------------------------------------------------------------------

# PRIMARY — Carvano et al. (2010) per-asteroid taxonomy table on PDS SBN
PDS_SDSSTAX_BASE  = (
    "https://sbnarchive.psi.edu/pds3/non_mission/"
    "EAR_A_I0035_5_SDSSTAX_V1_1/data/"
)
PDS_AST_TABLE_URL = PDS_SDSSTAX_BASE + "sdsstax_ast_table.tab"

# FALLBACK — Raw SDSS MOC4 (~64 MB compressed)
MOC4_URL_GZ = "https://faculty.washington.edu/ivezic/sdssmoc/ADR4.dat.gz"

REQUEST_TIMEOUT = 120   # seconds
CHUNK_SIZE      = 1 << 20  # 1 MB

# ---------------------------------------------------------------------------
# Taxonomy class -> fam code mapping
#
# The PDS file uses plain letter codes (no trailing 'p'):
#   single-letter: C, S, X, L, D, B, V, A, Q, O, M, E, P, T, K, R, F, G
#   compound:      CX, DL, XD, LS, XD, etc. — use first letter's family
# ---------------------------------------------------------------------------
TAX_LETTER_TO_FAM: dict[str, int] = {
    # primitive / carbonaceous
    "C": 0, "B": 0, "F": 0, "G": 0, "P": 0, "D": 0, "T": 0,
    # stony / chondritic
    "S": 1, "Q": 1, "K": 1, "L": 1, "V": 1, "A": 1, "R": 1, "O": 1,
    # metal-rich / X-complex (E=enstatite, M=metallic, X=ambiguous)
    "X": 2, "M": 2, "E": 2,
}


def tax_class_to_fam(cls_str: str) -> int:
    """Map a taxonomy class string to a family code (0/1/2/3)."""
    if not cls_str or pd.isna(cls_str):
        return 3
    first = str(cls_str).strip().upper()[:1]
    return TAX_LETTER_TO_FAM.get(first, 3)


# ---------------------------------------------------------------------------
# a* color index helpers (MOC4 fallback path)
# ---------------------------------------------------------------------------

def compute_a_star(g_r: pd.Series, r_i: pd.Series) -> pd.Series:
    """Ivezic et al. (2001): a* = 0.89*(g-r) + 0.45*(r-i) - 0.57"""
    return 0.89 * g_r + 0.45 * r_i - 0.57


def a_star_to_fam(a_star: float, i_z: float = 0.0) -> int:
    if np.isnan(a_star):
        return 3
    if a_star < 0.0:
        return 0   # C-complex
    if i_z >= 0.1:
        return 2   # X-complex
    return 1       # S-complex


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def download_bytes(url: str, desc: str = "") -> bytes:
    label = f"{desc} " if desc else ""
    log.info("Downloading %s%s", label, url)
    headers = {"User-Agent": "sdss_taxonomy_crossmatch/1.0 (asteroid mining research)"}
    resp = requests.get(url, headers=headers, stream=True, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0))
    chunks: list[bytes] = []
    downloaded = 0
    for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
        chunks.append(chunk)
        downloaded += len(chunk)
        if total:
            pct = downloaded / total * 100
            print(f"\r  {pct:5.1f}%  ({downloaded/1e6:.1f} / {total/1e6:.1f} MB)", end="", flush=True)
    print()
    return b"".join(chunks)


# ---------------------------------------------------------------------------
# PDS Carvano per-asteroid table parser
#
# EAR-A-I0035-5-SDSSTAX-V1.1 sdsstax_ast_table.tab
# Fixed-width ASCII; whitespace-delimited in practice.
# Columns (from PDS label and Carvano 2010):
#   1  ASTEROID_NUMBER  integer   MPC number (0 if unnumbered)
#   2  DESIGNATION      string    MPC provisional designation
#   3  N_OBS            integer   Number of SDSS detections averaged
#   4  TAX_CLASS        string    Best-fit Carvano class (e.g. "Sp")
#   5-13  class probs   real      Probability for each of the 9 classes
# ---------------------------------------------------------------------------

def parse_pds_table(raw: bytes) -> pd.DataFrame:
    """
    Parse the fixed-width sdsstax_ast_table.tab file.

    Actual column layout (0-indexed byte positions, confirmed from file inspection):
      0– 5 : asteroid number (right-justified integer; blank/0 = unnumbered)
      6    : space
      7–23 : name or designation (17 chars, left-justified, space-padded)
     24–35 : provisional designation (12 chars; '-' means none — use name field)
     36–37 : taxonomy class (1–2 chars: 'C', 'S', 'X', 'CX', 'DL', etc.)
     39–40 : confidence percentage (2 digits)
     43    : n_obs-ish count (single digit; used only loosely as weight)

    We use fixed-width slicing rather than whitespace splitting because
    provisional designations can contain spaces (e.g. "2001 QH142"), which
    causes split() to misalign every subsequent column.
    """
    text = raw.decode("ascii", errors="replace")
    rows = []
    bad  = 0
    for line in text.splitlines():
        line = line.rstrip()
        if not line or len(line) < 38:
            if line:
                bad += 1
            continue
        try:
            number_str = line[0:6].strip()
            name_str   = line[7:24].strip()   # proper name (numbered) or desig (unnumbered)
            prov_str   = line[24:36].strip()  # '-' when there is no provisional desig
            tax_raw    = line[36:38].strip()  # taxonomy class, 1–2 chars

            # Skip lines where the class field is not alphabetic — these are
            # headers, label blocks, or blank separators in the PDS3 file.
            if not tax_raw or not tax_raw[0].isalpha():
                bad += 1
                continue

            # Convert number; 0 means unnumbered in this catalog
            number = int(number_str) if number_str.isdigit() else 0

            # Best designation for cross-matching:
            #   numbered → use asteroid number (primary key); name is a bonus
            #   unnumbered → use provisional designation from either field
            desig = name_str if (prov_str == "-" or not prov_str) else prov_str

        except (ValueError, IndexError):
            bad += 1
            continue

        rows.append([
            number if number > 0 else pd.NA,
            desig,
            1,       # n_obs placeholder; not critical for taxonomy assignment
            tax_raw,
        ])

    if bad:
        log.warning("  Skipped %d non-data lines (headers / blank / label)", bad)

    df = pd.DataFrame(rows, columns=["number", "designation", "n_obs", "tax_class"])
    df["designation"] = df["designation"].str.replace("_", " ", regex=False).str.strip()
    df["number"]      = df["number"].astype("Int64")
    return df


# ---------------------------------------------------------------------------
# SDSS MOC4 raw file parser (fallback)
#
# ADR4.dat: space-separated ASCII, one observation per line.
# Token indices (0-based, from MOC4 documentation / Juric et al. 2002):
#   10: u,  11: g,  12: r,  13: i,  14: z   (PSF asinh magnitudes)
#   34: asteroid number,  35: designation
# ---------------------------------------------------------------------------

_MOC4_NUM = 34
_MOC4_DES = 35
_MOC4_G   = 11
_MOC4_R   = 12
_MOC4_I   = 13
_MOC4_Z   = 14
_MAG_MIN  = 10.0
_MAG_MAX  = 23.5


def parse_moc4(raw: bytes) -> pd.DataFrame:
    log.info("  Parsing MOC4 (this may take 30-60 s)...")
    text = raw.decode("ascii", errors="replace")
    rows: list[tuple] = []
    skip = 0
    need = max(_MOC4_NUM, _MOC4_Z) + 1
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < need:
            skip += 1
            continue
        try:
            num = int(parts[_MOC4_NUM])
            des = parts[_MOC4_DES]
            g   = float(parts[_MOC4_G])
            r   = float(parts[_MOC4_R])
            i_  = float(parts[_MOC4_I])
            z   = float(parts[_MOC4_Z])
        except (ValueError, IndexError):
            skip += 1
            continue
        if not (_MAG_MIN < g < _MAG_MAX and _MAG_MIN < r < _MAG_MAX
                and _MAG_MIN < i_ < _MAG_MAX and _MAG_MIN < z < _MAG_MAX):
            skip += 1
            continue
        rows.append((num, des, g, r, i_, z))

    log.info("  Parsed %d valid observations, skipped %d", len(rows), skip)
    df = pd.DataFrame(rows, columns=["number", "designation", "g", "r", "i_mag", "z"])
    df["number"] = df["number"].astype("Int64")
    df.loc[df["designation"].isin(["0", "-", "---"]), "designation"] = ""
    return df


def aggregate_moc4(df_obs: pd.DataFrame) -> pd.DataFrame:
    """Average per-observation MOC4 data to one row per asteroid."""
    df_obs = df_obs.copy()
    df_obs["g_r"]    = df_obs["g"]     - df_obs["r"]
    df_obs["r_i"]    = df_obs["r"]     - df_obs["i_mag"]
    df_obs["i_z"]    = df_obs["i_mag"] - df_obs["z"]
    df_obs["a_star"] = compute_a_star(df_obs["g_r"], df_obs["r_i"])

    def _agg(grp: pd.DataFrame) -> pd.Series:
        return pd.Series({
            "a_star":     grp["a_star"].median(),
            "i_z_med":    grp["i_z"].median(),
            "sdss_n_obs": len(grp),
        })

    numbered   = df_obs[df_obs["number"] > 0]
    unnumbered = df_obs[df_obs["number"] <= 0]

    parts = []
    if not numbered.empty:
        agg = numbered.groupby("number", as_index=True).apply(_agg).reset_index()
        agg["designation"] = ""
        parts.append(agg)
    if not unnumbered.empty:
        agg2 = unnumbered.groupby("designation", as_index=True).apply(_agg).reset_index()
        agg2["number"] = pd.NA
        parts.append(agg2)

    if not parts:
        return pd.DataFrame(columns=["number", "designation", "sdss_fam",
                                     "sdss_tax_class", "a_star", "sdss_n_obs"])

    result = pd.concat(parts, ignore_index=True)
    result["sdss_fam"] = result.apply(
        lambda r: a_star_to_fam(r["a_star"], r.get("i_z_med", 0.0)), axis=1
    )
    result["sdss_tax_class"] = result["sdss_fam"].map(
        {0: "C", 1: "S", 2: "X", 3: "?"}
    ).fillna("?")
    return result[["number", "designation", "sdss_fam", "sdss_tax_class", "a_star", "sdss_n_obs"]]


# ---------------------------------------------------------------------------
# Download orchestration
# ---------------------------------------------------------------------------

def fetch_pds_carvano() -> pd.DataFrame | None:
    try:
        raw = download_bytes(PDS_AST_TABLE_URL, "PDS Carvano table")
    except Exception as exc:
        log.warning("PDS download failed (%s); trying MOC4 fallback.", exc)
        return None

    df = parse_pds_table(raw)
    log.info("  Parsed %d rows from PDS table", len(df))
    if df.empty:
        return None

    df["sdss_fam"]       = df["tax_class"].apply(tax_class_to_fam)
    df["sdss_tax_class"] = df["tax_class"].str.strip()
    df["a_star"]         = np.nan   # not computed from raw colors in this path
    df["sdss_n_obs"]     = df["n_obs"]
    return df[["number", "designation", "sdss_fam", "sdss_tax_class", "a_star", "sdss_n_obs"]]


def fetch_moc4_fallback() -> pd.DataFrame | None:
    try:
        raw_gz = download_bytes(MOC4_URL_GZ, "SDSS MOC4 (~64 MB)")
    except Exception as exc:
        log.error("MOC4 download failed (%s). Cannot continue.", exc)
        return None

    log.info("Decompressing MOC4...")
    try:
        raw = gzip.decompress(raw_gz)
    except Exception as exc:
        log.error("Decompression failed: %s", exc)
        return None

    df_obs = parse_moc4(raw)
    if df_obs.empty:
        return None
    df = aggregate_moc4(df_obs)
    log.info("  Aggregated to %d unique asteroids", len(df))
    return df


# ---------------------------------------------------------------------------
# Output validation and write
# ---------------------------------------------------------------------------

def validate_and_write(df: pd.DataFrame) -> None:
    df["number"]         = pd.to_numeric(df["number"],     errors="coerce").astype("Int64")
    df["sdss_fam"]       = pd.to_numeric(df["sdss_fam"],   errors="coerce").fillna(3).astype(int)
    df["sdss_n_obs"]     = pd.to_numeric(df["sdss_n_obs"], errors="coerce").fillna(0).astype(int)
    df["a_star"]         = pd.to_numeric(df["a_star"],     errors="coerce").round(5)
    df["sdss_tax_class"] = df["sdss_tax_class"].fillna("").str.strip()
    df["designation"]    = df["designation"].fillna("").str.strip()

    # Keep only rows with at least one usable identifier
    valid  = (df["number"].notna() & (df["number"] > 0)) | (df["designation"] != "")
    before = len(df)
    df     = df[valid].copy()
    log.info("Rows with valid identifier: %d / %d", len(df), before)

    # Dedup by number — keep row with highest n_obs
    numbered   = df[df["number"].notna() & (df["number"] > 0)].copy()
    unnumbered = df[~(df["number"].notna() & (df["number"] > 0))].copy()
    numbered   = (numbered
                  .sort_values("sdss_n_obs", ascending=False)
                  .drop_duplicates(subset="number", keep="first"))
    df = pd.concat([numbered, unnumbered], ignore_index=True)

    # Summary
    fam_names = {0: "primitive/C", 1: "stony/S", 2: "metal/X", 3: "unknown"}
    log.info("Family distribution in output:")
    for code, cnt in df["sdss_fam"].value_counts().sort_index().items():
        log.info("  fam=%d (%s): %d", code, fam_names.get(int(code), "?"), cnt)

    df = df[["number", "designation", "sdss_fam", "sdss_tax_class", "a_star", "sdss_n_obs"]]
    df.to_csv(OUT_CSV, index=False)
    size_kb = OUT_CSV.stat().st_size / 1024
    log.info("Wrote %s  (%.0f KB, %d rows)", OUT_CSV, size_kb, len(df))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("=== SDSS Taxonomy Crossmatch ===")

    df = fetch_pds_carvano()
    if df is None:
        log.info("Falling back to raw SDSS MOC4...")
        df = fetch_moc4_fallback()

    if df is None:
        log.error("All data sources failed. Check network connectivity.")
        sys.exit(1)

    validate_and_write(df)
    log.info("Done. Run prepare_asteroid_data.py to rebuild asteroid-data.json.")


if __name__ == "__main__":
    main()
