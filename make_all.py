#!/usr/bin/env python3
"""
make_all.py
===========
One-command driver for the full asteroid-mining data pipeline.

Order of operations:

  1. neese_mithneos_enrichment.py
        Downloads / refreshes Neese (2010) taxonomy + derives MITHNEOS subset.
        Produces neese_taxonomy.csv, mithneos_taxonomy.csv.

  2. enrich_taxonomy.py
        Joins Neese + MITHNEOS + SDSS + Lowell IRAS into
        sbdb_lowell_merged.csv, adding best_tax_class / best_tax_source.
        Priority: MITHNEOS > Neese > SDSS > Lowell IRAS.

  3. resource_slider_bounds.py
        Rebuilds resource_slider_output/interactive_resource_slider_plot.html
        plus asteroid_resource_bounds.csv and asteroid_resource_summary.csv.

  4. composition_plots.py
        Regenerates composition_plots/*.png (bar / pie / scatter).

  5. pre_plotting.py
        Regenerates pre_plotting/*.png (histograms / boxplots / taxonomy counts).

Each step prints a banner, shows elapsed seconds, and bails on first failure
unless --keep-going is passed. Bytecode caches are cleared up-front so edits
to the .py files are always picked up.

Usage
-----
    uv run python make_all.py                 # full pipeline
    uv run python make_all.py --skip-download # skip Neese/MITHNEOS HTTP fetch
                                               # (uses existing CSVs on disk)
    uv run python make_all.py --skip plots    # only refresh taxonomy + merge
    uv run python make_all.py --only enrich   # only re-run the merge step
    uv run python make_all.py --keep-going    # continue past failing steps
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Step definitions: (key, script, friendly description)
STEPS: list[tuple[str, str, str]] = [
    ("merge",    "merge_sbdb_lowell.py",
     "Rebuild sbdb_lowell_merged.csv from SBDB + Lowell sources"),
    ("download", "neese_mithneos_enrichment.py",
     "Refresh Neese + MITHNEOS taxonomy catalogs"),
    ("mithneos", "mithneos_vizier_pull.py",
     "Pull genuine MITHNEOS NEA taxonomy (Marsset+2022, IOP MRT)"),
    ("enrich",   "enrich_taxonomy.py",
     "Join all taxonomies into sbdb_lowell_merged.csv"),
    ("slider",   "resource_slider_bounds.py",
     "Rebuild interactive resource slider HTML"),
    ("plots",    "composition_plots.py",
     "Regenerate composition plots"),
    ("pre",      "pre_plotting.py",
     "Regenerate pre-plotting histograms / boxplots"),
    ("cost",     "cost_model.py",
     "Rebuild Tier-1 $/kg interactive cost model"),
    ("website",  "build_website_data.py",
     "Publish asteroid-data.json to the bhaskar-jain website"),
]

STEP_KEYS = {key for key, _, _ in STEPS}


def banner(msg: str) -> None:
    bar = "=" * 72
    print(f"\n{bar}\n  {msg}\n{bar}", flush=True)


def clear_pycache() -> None:
    """Wipe __pycache__ to force Python to recompile from edited .py files.

    This avoids the stale-bytecode trap where a .pyc from before recent
    edits gets loaded instead of the updated source.
    """
    cache = HERE / "__pycache__"
    if cache.exists():
        try:
            shutil.rmtree(cache)
            print(f"  Cleared {cache.name}/")
        except Exception as exc:
            print(f"  Could not clear {cache.name}/: {exc}")


def run_step(script: str, description: str, *, keep_going: bool) -> bool:
    """Run one pipeline step. Returns True on success."""
    banner(f"{description}\n  ({script})")
    script_path = HERE / script
    if not script_path.exists():
        print(f"  ERROR: {script} not found in {HERE}")
        return False

    start = time.time()
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=HERE,
            check=False,
        )
    except KeyboardInterrupt:
        print("\n  Interrupted by user.")
        raise

    elapsed = time.time() - start
    if result.returncode == 0:
        print(f"\n  OK ({elapsed:.1f}s)")
        return True

    print(f"\n  FAILED with exit code {result.returncode}  ({elapsed:.1f}s)")
    if not keep_going:
        print("  Stopping. Pass --keep-going to continue past failures.")
    return False


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--skip", action="append", default=[], metavar="STEP",
                   choices=sorted(STEP_KEYS),
                   help="Skip a step (may be passed multiple times). "
                        f"Choices: {', '.join(sorted(STEP_KEYS))}.")
    p.add_argument("--only", action="append", default=[], metavar="STEP",
                   choices=sorted(STEP_KEYS),
                   help="Run only the named step(s). Overrides --skip.")
    p.add_argument("--skip-download", action="store_true",
                   help="Alias for --skip download.")
    p.add_argument("--keep-going", action="store_true",
                   help="Continue running later steps even if one fails.")
    p.add_argument("--no-clear-cache", action="store_true",
                   help="Don't wipe __pycache__ before running.")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    skip = set(args.skip)
    if args.skip_download:
        skip.add("download")
    only = set(args.only)

    if not args.no_clear_cache:
        banner("Clearing bytecode cache")
        clear_pycache()

    selected = [(k, s, d) for (k, s, d) in STEPS
                if (not only or k in only) and k not in skip]

    if not selected:
        print("No steps selected. Use --only / --skip carefully.")
        return 1

    print(f"\nWill run {len(selected)} step(s): "
          f"{', '.join(k for k, _, _ in selected)}\n")

    overall_start = time.time()
    failures: list[str] = []

    for key, script, desc in selected:
        ok = run_step(script, desc, keep_going=args.keep_going)
        if not ok:
            failures.append(key)
            if not args.keep_going:
                break

    overall_elapsed = time.time() - overall_start

    banner("Pipeline summary")
    print(f"  Total time: {overall_elapsed:.1f}s")
    if failures:
        print(f"  Failed steps: {', '.join(failures)}")
        return 1
    print("  All steps completed successfully.")
    print("\n  Refreshed outputs:")
    print("    - neese_taxonomy.csv, mithneos_taxonomy.csv")
    print("    - sbdb_lowell_merged.csv  (with best_tax_class / best_tax_source)")
    print("    - resource_slider_output/interactive_resource_slider_plot.html")
    print("    - resource_slider_output/asteroid_resource_bounds.csv")
    print("    - resource_slider_output/asteroid_resource_summary.csv")
    print("    - composition_plots/*.png")
    print("    - pre_plotting/*.png")
    print("    - cost_model_output/cost_model_tier1.html  (interactive $/kg)")
    print("    - cost_model_output/fleet_cost_defaults.csv")
    print("    - ../bhaskar-jain/public/thoughts/asteroid-mining/asteroid-data.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
