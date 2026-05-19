#!/usr/bin/env python3
"""
mithneos_real_pull.py
=====================
Pulls the GENUINE MITHNEOS (MIT-Hawaii Near-Earth Object Spectroscopic Survey)
classifications via the `space-classy` package, replacing the
Neese-derived placeholder that the earlier `neese_mithneos_enrichment.py`
fell back to producing.

Inputs:
    None (downloads the MITHNEOS spectra index on first run; cached after).

Outputs:
    mithneos_taxonomy.csv
        Schema: number, designation, tax_class, fam
        One row per unique asteroid in MITHNEOS with a successful Bus-DeMeo
        classification. If multiple spectra exist for the same asteroid,
        the most-common classification wins.

Why this exists:
    The neese_mithneos_enrichment.py script tried to download SMASS II
    from PDS, found only raw spectra (no taxonomy table), tried VizieR
    fallbacks (also unavailable), and ended up deriving "MITHNEOS" data
    by filtering Bus-extended classifications out of the Neese (2010)
    main-belt compilation. The result was 388 main-belt rows mislabelled
    as MITHNEOS — not the NEA-targeted survey the name implies.

    This script uses `classy` (Mahlke 2024) to load the real
    Binzel et al. 2019, Marsset et al. 2022, DeMeo et al. 2019, and
    Polishook et al. 2014 NEA spectroscopy, classify each spectrum in
    the Bus-DeMeo system, and write a proper mithneos_taxonomy.csv.

Setup (one-time):
    uv pip install space-classy

Usage:
    uv run python mithneos_real_pull.py
    uv run python mithneos_real_pull.py --quick   # skip classification, write
                                                   # just the asteroid index
"""

from __future__ import annotations

import argparse
import logging
import sys
import warnings
from collections import Counter
from pathlib import Path

import pandas as pd

# Silence chatty scientific-library warnings; we surface real errors via log.
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_CSV = SCRIPT_DIR / "mithneos_taxonomy.csv"

# Bus-DeMeo → fam-code mapping, identical to neese_mithneos_enrichment.py
# (0 = primitive/C-complex, 1 = stony/S-complex, 2 = metal/X-complex, 3 = ?)
TAX_LETTER_TO_FAM: dict[str, int] = {
    "C": 0, "B": 0, "F": 0, "G": 0, "P": 0, "D": 0, "T": 0,
    "S": 1, "Q": 1, "K": 1, "L": 1, "V": 1, "A": 1, "R": 1, "O": 1,
    "X": 2, "M": 2, "E": 2,
}


def tax_class_to_fam(cls_str: str | None) -> int:
    if not cls_str or pd.isna(cls_str):
        return 3
    return TAX_LETTER_TO_FAM.get(str(cls_str).strip().upper()[:1], 3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true",
                        help="Skip Bus-DeMeo classification; just dump the "
                             "asteroid index from MITHNEOS (faster, but tax_class "
                             "will be blank — useful for sanity checks).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap the number of spectra processed (for testing).")
    args = parser.parse_args()

    try:
        import classy  # type: ignore
    except ImportError:
        log.warning("space-classy package not installed — skipping real-MITHNEOS pull.")
        log.warning("The Neese-derived mithneos_taxonomy.csv from the previous step")
        log.warning("will remain in place. To enable this step:")
        log.warning("  uv pip install \"space-classy>=0.8\"")
        return 0   # best effort — don't halt the pipeline

    log.info("Loading classy spectra index ...")
    idx = classy.index.load()
    log.info("  Total spectra in classy: %d", len(idx))

    # First-run bootstrap: classy ships without spectra. If the index is empty,
    # run `classy status` programmatically with stdin "2" to trigger the
    # public-data download. This is documented as the one-time setup step at
    # https://classy.readthedocs.io/en/latest/getting_started.html
    if len(idx) == 0:
        log.warning("classy spectra cache is empty — bootstrapping it now.")
        log.warning("This downloads ~68,000 spectra (a few hundred MB) and may")
        log.warning("take 5–15 minutes on a slow connection. One-time only; the")
        log.warning("cache is reused on subsequent runs.")

        import os
        import subprocess
        # Find the `classy` CLI inside the active venv. On Windows it's
        # .venv\Scripts\classy.exe; on POSIX it's .venv/bin/classy. Fall back
        # to whatever's on PATH if neither exists.
        candidates = [
            SCRIPT_DIR / ".venv" / "Scripts" / "classy.exe",
            SCRIPT_DIR / ".venv" / "bin" / "classy",
        ]
        classy_cmd: str | Path = next((c for c in candidates if c.exists()), "classy")

        # Wire up an SSL CA bundle so urllib (which classy uses internally)
        # can verify HTTPS downloads from cdsarc / smass.mit.edu / etc.
        # uv-installed Pythons on Windows don't have a CA bundle hooked up,
        # which causes "CERTIFICATE_VERIFY_FAILED" errors otherwise.
        env = os.environ.copy()
        try:
            import certifi
            ca = certifi.where()
            env["SSL_CERT_FILE"] = ca
            env["REQUESTS_CA_BUNDLE"] = ca
            log.info("  Using CA bundle: %s", ca)
        except ImportError:
            log.warning("  certifi not installed; SSL verification may fail. "
                        "Run: uv pip install certifi")

        log.info("  Invoking %s status ...", classy_cmd)
        try:
            result = subprocess.run(
                [str(classy_cmd), "status"],
                input="2\n", text=True, check=False, timeout=1800, env=env,
            )
        except FileNotFoundError:
            log.warning("Could not find the `classy` CLI executable. Skipping real-MITHNEOS pull.")
            log.warning("Manual fix when ready:  uv run classy status   (choose option 2)")
            return 0   # best effort
        except subprocess.TimeoutExpired:
            log.warning("classy status timed out after 30 minutes. Skipping real-MITHNEOS pull.")
            log.warning("Run it manually when convenient:")
            log.warning('  $env:SSL_CERT_FILE = (uv run python -c "import certifi; print(certifi.where())")')
            log.warning("  uv run classy status")
            return 0

        # `classy status` is brittle — its bulk downloader can fail on individual
        # sources (broken CDS archives, Gaia network blips) and exit non-zero
        # even though most of the data made it onto disk. So instead of trusting
        # the exit code, we re-check whether the cache has anything useful.
        if result.returncode != 0:
            log.warning("`classy status` exited with code %d — partial download "
                        "is common; checking what made it to disk anyway.",
                        result.returncode)

        # Re-import to pick up the freshly-populated index
        import importlib
        importlib.reload(classy.index)
        try:
            idx = classy.index.load()
        except Exception as exc:
            log.warning("Could not load classy index after bootstrap: %s", exc)
            log.warning("Skipping real-MITHNEOS pull. The Neese-derived placeholder")
            log.warning("will remain in mithneos_taxonomy.csv. Retry later with:")
            log.warning("  uv run classy status   (choose option 2)")
            return 0
        log.info("After bootstrap: %d spectra in classy cache.", len(idx))
        if len(idx) == 0:
            log.warning("Cache is still empty after bootstrap — classy's bulk")
            log.warning("downloader is fragile and probably aborted before writing")
            log.warning("the index file. Skipping real-MITHNEOS pull for now; the")
            log.warning("Neese-derived placeholder remains in place.")
            log.warning("To retry by hand:  uv run classy status   (choose option 2)")
            return 0

        # Quick visibility into what sources made it
        try:
            sources_seen = idx["source"].value_counts().to_dict()
            log.info("  Sources in cache: %s", sources_seen)
        except Exception:
            pass

    mithneos_idx = idx[idx["source"].astype(str).str.upper() == "MITHNEOS"].copy()
    log.info("  MITHNEOS spectra:       %d", len(mithneos_idx))

    if mithneos_idx.empty:
        log.warning("classy cache exists but contains no MITHNEOS rows.")
        log.warning("Keeping the Neese-derived mithneos_taxonomy.csv from the")
        log.warning("previous pipeline step. Retry once classy is healthy:")
        log.warning("  uv run classy status   (choose option 2)")
        return 0

    # `classy` typically tracks target number + name on each row. Column
    # names have evolved across releases, so probe defensively.
    num_col = next((c for c in ("number", "asteroid_number", "target_number")
                    if c in mithneos_idx.columns), None)
    nam_col = next((c for c in ("name", "asteroid_name", "target_name", "designation")
                    if c in mithneos_idx.columns), None)
    if num_col is None or nam_col is None:
        log.error("Could not find number/name columns in classy index. "
                  "Columns present: %s", list(mithneos_idx.columns))
        return 1
    log.info("  Using columns: number=%s, name=%s", num_col, nam_col)

    if args.limit:
        mithneos_idx = mithneos_idx.head(args.limit)
        log.info("  Limited to %d rows for testing", len(mithneos_idx))

    # -- Classify (unless --quick) -----------------------------------------
    rows: list[dict] = []

    if args.quick:
        log.info("--quick: skipping classification, emitting index only")
        seen_keys: set = set()
        for _, r in mithneos_idx.iterrows():
            num = r.get(num_col)
            nam = r.get(nam_col)
            key = (num, nam)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            rows.append({"number": num, "designation": nam,
                         "tax_class": "", "fam": 3})
    else:
        log.info("Classifying %d MITHNEOS spectra in Bus-DeMeo taxonomy ...",
                 len(mithneos_idx))
        log.info("(First run downloads ~50–100 MB of spectra; subsequent runs use cache.)")

        # Group spectra by asteroid — when an asteroid has multiple spectra,
        # we want one row per asteroid (taking the most-frequent Bus-DeMeo
        # class across its spectra).
        by_target: dict[tuple, list[str]] = {}

        try:
            specs = classy.Spectra(mithneos_idx)
        except Exception as exc:
            log.error("Failed to load Spectra batch: %s", exc)
            return 1

        n_processed = 0
        n_classified = 0
        n_failed = 0

        for spec in specs:
            n_processed += 1
            if n_processed % 100 == 0:
                log.info("  ... %d / %d processed (%d classified, %d failed)",
                         n_processed, len(mithneos_idx), n_classified, n_failed)
            try:
                num = getattr(spec.target, "number", None)
                nam = getattr(spec.target, "name", None)
                spec.classify(taxonomy="demeo")
                cls = (getattr(spec, "class_demeo", "") or "").strip()
                if not cls:
                    # DeMeo requires NIR coverage; not every spectrum qualifies.
                    # Fall back to Mahlke 22 (which works on any wavelength range)
                    cls = (getattr(spec, "class_", "") or "").strip()
                if cls:
                    n_classified += 1
                    by_target.setdefault((num, nam), []).append(cls)
                else:
                    n_failed += 1
            except Exception as exc:
                n_failed += 1
                log.debug("    spectrum failed: %s", exc)

        log.info("Processed %d spectra: %d classified, %d failed, %d unique asteroids",
                 n_processed, n_classified, n_failed, len(by_target))

        # Most-frequent classification per asteroid wins
        for (num, nam), classes in by_target.items():
            counter = Counter(classes)
            winner, _ = counter.most_common(1)[0]
            rows.append({"number": num, "designation": nam,
                         "tax_class": winner, "fam": tax_class_to_fam(winner)})

    if not rows:
        log.error("No rows produced.")
        return 1

    df = pd.DataFrame(rows)
    # Normalise types and drop empty rows
    df["number"] = pd.to_numeric(df["number"], errors="coerce").astype("Int64")
    df["designation"] = df["designation"].fillna("").astype(str).str.strip()
    df = df[(df["number"].notna()) | (df["designation"] != "")]
    df = df.drop_duplicates(subset=["number", "designation"]).reset_index(drop=True)

    fam_counts = df["fam"].value_counts().sort_index().to_dict()
    n_numbered = int(df["number"].notna().sum())
    log.info("Final rows: %d total, %d with numbers, family distribution: %s",
            len(df), n_numbered, fam_counts)

    df = df[["number", "designation", "tax_class", "fam"]]
    df.to_csv(OUTPUT_CSV, index=False)
    log.info("Wrote %s  (%d rows)", OUTPUT_CSV.name, len(df))
    return 0


if __name__ == "__main__":
    sys.exit(main())
