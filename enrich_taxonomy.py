#!/usr/bin/env python3
"""
enrich_taxonomy.py
==================
Joins the three external taxonomy catalogs into sbdb_lowell_merged.csv,
producing two new columns:

  * best_tax_class   — chosen spectral class string (e.g. "S", "Cgh", "X")
  * best_tax_source  — which catalog the class came from
                       {"mithneos", "neese", "sdss", "lowell", ""}

Priority cascade (first hit wins):
    MITHNEOS  >  Neese  >  SDSS  >  Lowell IRAS

Rationale:
- MITHNEOS / Bus-SMASS spectroscopy is the highest-confidence per-object source.
- Neese 2010 is a hand-curated multi-survey compilation (largest main-belt set).
- SDSS is broad but photometric, lower confidence per object.
- Lowell IRAS is the legacy fallback already in the merged file.

Match strategy:
- Numbered asteroids: join on integer asteroid number.
  The main dataset's `pdes` column stores the number for numbered asteroids;
  `lowell_number` is used as a secondary key when pdes is non-integer.
- Unnumbered: not matched (the catalogs we have are dominated by numbered
  asteroids; name-only matching would be lossy and is left as a future option).

Usage:
    uv run python enrich_taxonomy.py
    uv run python enrich_taxonomy.py --input sbdb_lowell_merged.csv \
                                     --output sbdb_lowell_merged.csv
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent

DEFAULT_INPUT  = SCRIPT_DIR / "sbdb_lowell_merged.csv"
DEFAULT_OUTPUT = SCRIPT_DIR / "sbdb_lowell_merged.csv"   # mutates in place

NEESE_CSV    = SCRIPT_DIR / "neese_taxonomy.csv"
MITHNEOS_CSV = SCRIPT_DIR / "mithneos_taxonomy.csv"
SDSS_CSV     = SCRIPT_DIR / "sdss_taxonomy.csv"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_int_or_none(v):
    """Parse a value as integer, returning None for blanks / non-integers."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _load_lookup(path: Path, class_col: str) -> dict[int, str]:
    """Load a taxonomy CSV into a {number: tax_class} dict, dropping blanks."""
    if not path.exists():
        log.warning("  %s not found -- skipping.", path.name)
        return {}
    df = pd.read_csv(path)
    if "number" not in df.columns or class_col not in df.columns:
        log.warning("  %s missing required columns -- skipping.", path.name)
        return {}
    lookup: dict[int, str] = {}
    for n, c in zip(df["number"], df[class_col]):
        n_int = _to_int_or_none(n)
        if n_int is None:
            continue
        if pd.isna(c):
            continue
        s = str(c).strip()
        if not s or s.lower() == "nan":
            continue
        # First entry wins if duplicates exist
        lookup.setdefault(n_int, s)
    log.info("  %-25s %5d numbered entries", path.name, len(lookup))
    return lookup


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",  type=Path, default=DEFAULT_INPUT,
                        help="Input merged CSV (default: sbdb_lowell_merged.csv)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="Output CSV path (default: in-place overwrite)")
    args = parser.parse_args()

    log.info("Loading taxonomy catalogs:")
    mith_lookup  = _load_lookup(MITHNEOS_CSV, "tax_class")
    neese_lookup = _load_lookup(NEESE_CSV,    "tax_class")
    sdss_lookup  = _load_lookup(SDSS_CSV,     "sdss_tax_class")

    log.info("Loading main dataset: %s", args.input.name)
    df = pd.read_csv(args.input, low_memory=False)
    log.info("  %d rows, %d columns", len(df), len(df.columns))

    # Resolve each row's integer asteroid number from pdes ONLY.
    #
    # NOTE: lowell_number is intentionally *not* used as a fallback. The
    # upstream merge_sbdb_lowell.py has a pre-existing bug where, for
    # unnumbered asteroids with provisional designations like "1990 UN",
    # the year (1990) was extracted and stored as lowell_number. Those
    # values then collide with unrelated permanent asteroid numbers
    # (e.g. #1990 Pilcher), causing thousands of spurious taxonomy joins.
    # pdes alone is the trustworthy permanent-number column.
    def _row_number(row) -> int | None:
        return _to_int_or_none(row.get("pdes"))

    numbers = df.apply(_row_number, axis=1)
    n_numbered = numbers.notna().sum()
    log.info("  %d / %d rows resolve to a numbered asteroid", n_numbered, len(df))

    # Apply priority cascade
    best_class:  list[str | None] = [None] * len(df)
    best_source: list[str]        = [""]   * len(df)

    for i, n in enumerate(numbers):
        if n is None or pd.isna(n):
            continue
        n_int = int(n)
        if n_int in mith_lookup:
            best_class[i]  = mith_lookup[n_int]
            best_source[i] = "mithneos"
        elif n_int in neese_lookup:
            best_class[i]  = neese_lookup[n_int]
            best_source[i] = "neese"
        elif n_int in sdss_lookup:
            best_class[i]  = sdss_lookup[n_int]
            best_source[i] = "sdss"

    # Final fallback: Lowell IRAS column already in the merged dataset
    lowell_col = "lowell_iras_tax_class"
    if lowell_col in df.columns:
        for i, v in enumerate(df[lowell_col]):
            if best_class[i] is not None:
                continue
            if pd.isna(v):
                continue
            s = str(v).strip()
            if not s or s.lower() == "nan":
                continue
            best_class[i]  = s
            best_source[i] = "lowell"

    df["best_tax_class"]  = best_class
    df["best_tax_source"] = best_source

    # Report
    source_counts = pd.Series(best_source).replace("", "(none)").value_counts()
    log.info("Coverage by source:")
    for src, count in source_counts.items():
        log.info("  %-10s %6d  (%.2f%%)", src, count, count / len(df) * 100)

    n_classified = sum(1 for c in best_class if c is not None)
    log.info("Total classified: %d / %d  (%.2f%%)",
             n_classified, len(df), n_classified / len(df) * 100)

    # Write output
    df.to_csv(args.output, index=False)
    log.info("Wrote %s  (%d rows, %d columns)",
             args.output.name, len(df), len(df.columns))


if __name__ == "__main__":
    main()
