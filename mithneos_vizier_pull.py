#!/usr/bin/env python3
"""
mithneos_vizier_pull.py
=======================
Direct pull of the GENUINE MITHNEOS Bus-DeMeo classifications from the
machine-readable Table 7 published alongside Marsset et al. 2022,
"The Debiased Compositional Distribution of MITHNEOS: Global Match between
the Near-Earth and Main-belt Asteroid Populations, and Excess of D-type
Near-Earth Objects" (Astronomical Journal, 163, 165, J/AJ/163/165).

This script replaces the failing classy-based path:
  * No classy dependency, no spectra cache, no GUI.
  * Single HTTPS request to the IOP CDN.
  * ~50 KB file, fixed-width text we can parse with stdlib.

The Marsset+2022 table covers 491 spectra of 420 NEAs — exactly the NEA-
focused taxonomy data the Neese-derived placeholder was missing. After
this runs, prepare_asteroid_data.py / build_website_data.py will show a
non-zero `mithneos=` count in their source breakdown for the first time.

Inputs:
    None (downloads the table over HTTPS).

Outputs:
    mithneos_taxonomy.csv
        Schema: number, designation, tax_class, fam
        One row per unique asteroid in the Marsset+2022 catalog. When an
        asteroid has multiple spectra with different classifications, the
        most-frequent classification wins.

Usage:
    uv run python mithneos_vizier_pull.py
    uv run python mithneos_vizier_pull.py --keep-placeholder  # don't overwrite
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_CSV = SCRIPT_DIR / "mithneos_taxonomy.csv"

# IOP hosts the machine-readable Table 7 directly. CDS/VizieR also mirrors
# this dataset under J/AJ/163/165 — IOP is the simpler endpoint.
SOURCE_URL = (
    "https://content.cld.iop.org/journals/1538-3881/163/4/"
    "165/revision1/ajac532ft7_mrt.txt"
)

# Fixed-width column positions from the file's byte-by-byte header.
# Note: positions are 1-indexed in the header; Python slicing is 0-indexed.
COL_NUMBER_START = 33   # bytes 34-39, I6
COL_NUMBER_END   = 39
COL_DESIG_START  = 40   # bytes 41-50, A10
COL_DESIG_END    = 50
COL_NAME_START   = 51   # bytes 52-64, A13
COL_NAME_END     = 64
COL_TAXON_START  = 123  # bytes 124-129, A6
COL_TAXON_END    = 129

# Bus-DeMeo first-letter → fam-code, identical to the rest of the pipeline.
TAX_LETTER_TO_FAM: dict[str, int] = {
    "C": 0, "B": 0, "F": 0, "G": 0, "P": 0, "D": 0, "T": 0,
    "S": 1, "Q": 1, "K": 1, "L": 1, "V": 1, "A": 1, "R": 1, "O": 1,
    "X": 2, "M": 2, "E": 2,
}


def tax_class_to_fam(cls_str: str | None) -> int:
    if not cls_str:
        return 3
    return TAX_LETTER_TO_FAM.get(str(cls_str).strip().upper()[:1], 3)


# Cleanup map for Marsset's taxonomy strings. The published table includes
# composite labels ("S_comp"), uncertainty separators ("Sq;Q"), and trailing
# slashes; we collapse each to a single primary class so the downstream
# enrichment can match by class letter cleanly.
_CLEAN_SUFFIX_RE = re.compile(r"_comp\b|_complex\b|/.*$|\?+$", re.IGNORECASE)


def clean_taxon(raw: str) -> str:
    """Normalise a Marsset taxon cell to a single Bus-DeMeo class string."""
    s = (raw or "").strip()
    if not s:
        return ""
    # "Sq;Q" -> "Sq" (prefer the first listed alternative)
    if ";" in s:
        s = s.split(";", 1)[0].strip()
    # "S_comp" / "S_complex" -> "S";   "X/Cb" -> "X";   "Sq?" -> "Sq"
    s = _CLEAN_SUFFIX_RE.sub("", s).strip()
    return s


def fetch_mrt(url: str = SOURCE_URL, timeout: int = 60) -> str:
    log.info("Fetching MITHNEOS machine-readable Table 7 ...")
    log.info("  GET %s", url)
    headers = {
        "User-Agent": "mithneos-vizier-pull/1.0 (asteroid taxonomy research)"
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    text = resp.text
    log.info("  Downloaded %d bytes / %d lines", len(text), text.count("\n"))
    return text


def parse_mrt(text: str) -> pd.DataFrame:
    """Walk the MRT body row-by-row, extracting (number, name, taxon) tuples."""
    rows: list[dict] = []
    in_data = False
    skipped_header = 0
    skipped_blank = 0

    for line in text.splitlines():
        # MRT bodies follow a section that ends with a dashed rule. The byte
        # description header in this file ends with the dashed Note(1) line,
        # then the first data row begins. We detect the data section by
        # finding the first line that has digits in the asteroid-number
        # column AND a non-empty taxonomy column.
        if not in_data:
            stripped = line.strip()
            if not stripped or stripped.startswith(
                ("Title:", "Authors:", "Table:", "Byte-by-byte",
                 "Bytes", "Note", "Reference", "==", "--")
            ):
                skipped_header += 1
                continue
            # Try parsing as data; if it looks like a header row, skip
            if len(line) < COL_TAXON_END:
                skipped_header += 1
                continue
            # First data row: starts with an "a*.txt" or similar filename
            if line[0:1] == "a" and "." in line[:32]:
                in_data = True
            else:
                skipped_header += 1
                continue

        if not line.strip():
            skipped_blank += 1
            continue
        if len(line) < COL_TAXON_END:
            continue

        # Slice by byte positions
        num_raw   = line[COL_NUMBER_START:COL_NUMBER_END].strip()
        desig_raw = line[COL_DESIG_START:COL_DESIG_END].strip()
        name_raw  = line[COL_NAME_START:COL_NAME_END].strip()
        taxon_raw = line[COL_TAXON_START:COL_TAXON_END].strip()

        try:
            number = int(num_raw) if num_raw else None
        except ValueError:
            number = None

        taxon = clean_taxon(taxon_raw)
        if not taxon:
            continue  # skip rows with no usable taxonomy

        rows.append({
            "number":      number,
            "designation": name_raw or desig_raw,
            "taxon_raw":   taxon_raw,
            "tax_class":   taxon,
        })

    log.info("  Parsed %d spectrum rows (skipped %d header, %d blank)",
             len(rows), skipped_header, skipped_blank)
    return pd.DataFrame(rows)


def collapse_to_one_per_asteroid(df: pd.DataFrame) -> pd.DataFrame:
    """Multiple spectra per asteroid → keep the most-common Bus-DeMeo class.

    Tiebreaker: lexicographic ordering on the class string, so behaviour is
    deterministic across runs even when two classes are equally common.
    """
    if df.empty:
        return df

    # Build a stable key per asteroid. Numbered asteroids are keyed by number;
    # unnumbered ones by their designation/name string.
    def _key(row):
        return ("N", int(row["number"])) if pd.notna(row["number"]) \
               else ("D", str(row["designation"]).strip())

    df["_key"] = df.apply(_key, axis=1)

    out_rows = []
    for key, group in df.groupby("_key", sort=False):
        counter = Counter(group["tax_class"].dropna().astype(str).tolist())
        if not counter:
            continue
        # Most common, ties broken alphabetically for determinism
        winner = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        first = group.iloc[0]
        out_rows.append({
            "number":      int(first["number"]) if pd.notna(first["number"]) else None,
            "designation": str(first["designation"]).strip(),
            "tax_class":   winner,
            "fam":         tax_class_to_fam(winner),
        })

    out = pd.DataFrame(out_rows)
    if not out.empty:
        out["number"] = pd.to_numeric(out["number"], errors="coerce").astype("Int64")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-placeholder", action="store_true",
        help="If set, do not overwrite mithneos_taxonomy.csv. "
             "Useful when iterating on the script without losing the "
             "Neese-derived fallback.",
    )
    parser.add_argument(
        "--output", type=Path, default=OUTPUT_CSV,
        help="Output CSV path (default: %(default)s)",
    )
    args = parser.parse_args()

    try:
        text = fetch_mrt()
    except requests.RequestException as exc:
        log.error("Failed to download Marsset+2022 table: %s", exc)
        log.error("The Neese-derived placeholder in %s remains in place.",
                  OUTPUT_CSV.name)
        return 0   # best effort — don't halt the pipeline

    raw = parse_mrt(text)
    if raw.empty:
        log.error("No data rows parsed. The remote file format may have "
                  "changed; inspect the byte positions in the script.")
        return 0

    df = collapse_to_one_per_asteroid(raw)
    log.info("Unique asteroids after dedup: %d", len(df))
    fam_counts = df["fam"].value_counts().sort_index().to_dict()
    log.info("Family distribution: %s", fam_counts)
    log.info("Top Bus-DeMeo classes: %s",
             dict(df["tax_class"].value_counts().head(10)))

    if args.keep_placeholder:
        log.info("--keep-placeholder set; not overwriting %s.",
                 args.output.name)
        return 0

    df = df[["number", "designation", "tax_class", "fam"]]
    df.to_csv(args.output, index=False)
    log.info("Wrote %s  (%d rows)", args.output.name, len(df))
    return 0


if __name__ == "__main__":
    sys.exit(main())
