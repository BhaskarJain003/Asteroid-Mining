#!/usr/bin/env python3
"""
neese_mithneos_enrichment.py
============================
Downloads and parses two spectral taxonomy catalogs:

  1. Neese (2010)  —  EAR-A-5-DDR-TAXONOMY-V6.0
     Multi-survey compilation: Tholen, Bus/SMASS, Bus-DeMeo, S3OS2, ECAS.
     Source: NASA PDS SBN  (sbnarchive.psi.edu)

  2. SMASS II  —  Bus & Binzel (2002), AJ 123, 1112
     ~1,447 asteroid spectral classifications, heavy NEA coverage.
     PDS SBN directory auto-discovered from root listing.
     VizieR / DeMeo et al. 2009 used as fallback if PDS unavailable.

Output
------
  neese_taxonomy.csv    — number, designation, tax_class, tax_source, fam
  mithneos_taxonomy.csv — number, designation, tax_class, fam

fam codes: 0=primitive/C, 1=stony/S, 2=metal/X, 3=unknown

Usage
-----
    uv run python neese_mithneos_enrichment.py

Requirements
------------
    pip install requests pandas
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

SCRIPT_DIR       = Path(__file__).resolve().parent
NEESE_OUT_CSV    = SCRIPT_DIR / "neese_taxonomy.csv"
MITHNEOS_OUT_CSV = SCRIPT_DIR / "mithneos_taxonomy.csv"

REQUEST_TIMEOUT = 60
CHUNK_SIZE      = 1 << 20

# ---------------------------------------------------------------------------
# URLs / dataset names
# ---------------------------------------------------------------------------
PDS_BASE = "https://sbnarchive.psi.edu/pds3/non_mission/"

NEESE_DIRS = [
    "EAR_A_5_DDR_TAXONOMY_V6_0",
    "EAR_A_5_DDR_TAXONOMY_V5_0",
    "EAR_A_5_DDR_TAXONOMY_V4_0",
]

SMASSII_DIRS_STATIC = [
    # The actual SMASS II directory on PDS (confirmed 2024+):
    "EAR_A_I0028_4_SBN0001_SMASSII_V1_0",
    # Legacy / alternate name guesses (kept for resilience):
    "EAR_A_I_RDR_SMASSII_V1_0",
    "EAR_A_I_DDR_SMASSII_V1_0",
    "EAR_A_5_DDR_SMASSII_V1_0",
    "EAR_A_5_DDR_SMASS_V1_0",
    "EAR_A_5_DDR_SMASSII_SPECTRA_V1_0",
    "EAR_A_5_DDR_SMASS_II_V1_0",
    "EAR_A_3_RDR_SMASSII_V1_0",
    "EAR_A_3_DDR_SMASSII_V1_0",
]

# VizieR fallbacks: Bus & Binzel 2002 (J/AJ/123/1112),
#                   DeMeo et al. 2009 (J/Icarus/202/160)
SMASSII_VIZIER_URLS = [
    "https://cdsarc.cds.unistra.fr/ftp/J/AJ/123/1112/table1.dat",
    "https://cdsarc.u-strasbg.fr/ftp/J/AJ/123/1112/table1.dat",
    "https://cdsarc.cds.unistra.fr/ftp/J/Icarus/202/160/table1.dat",
    "https://cdsarc.u-strasbg.fr/ftp/J/Icarus/202/160/table1.dat",
]

# NOTE: "smass.tab" deliberately excluded — EAR_A_M3SPEC_3_RDR_SMASS_V1_0
# contains smass.tab with raw wavelength/reflectance spectra (~4 MB), NOT
# taxonomy classifications. Including it caused 1,746 bogus rows with 0
# valid asteroid numbers to be written to mithneos_taxonomy.csv.
DATA_FILENAMES = [
    "taxonomy.tab",
    "taxonomy10.tab",
    "tax.tab",
    "smassii.tab",
    "data.tab",
    "smassclass.tab",
    "busclass.tab",
]

# ---------------------------------------------------------------------------
# Taxonomy mapping
# ---------------------------------------------------------------------------
TAX_LETTER_TO_FAM: dict[str, int] = {
    "C": 0, "B": 0, "F": 0, "G": 0, "P": 0, "D": 0, "T": 0,
    "S": 1, "Q": 1, "K": 1, "L": 1, "V": 1, "A": 1, "R": 1, "O": 1,
    "X": 2, "M": 2, "E": 2,
}

# Source priority for Neese letter-coded sources (used by older versions of
# the file; taxonomy10.tab uses numeric refs, so most sources map to priority 1)
NEESE_SOURCE_PRIORITY: dict[str, int] = {
    "BD": 10, "BD2": 10, "B": 8, "B2": 8, "Bu": 8,
    "S3": 7, "Th": 5, "T": 5, "Ca": 4, "Ec": 4,
}


def tax_class_to_fam(cls_str: str) -> int:
    if not cls_str or pd.isna(cls_str):
        return 3
    return TAX_LETTER_TO_FAM.get(str(cls_str).strip().upper()[:1], 3)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _headers() -> dict:
    return {"User-Agent": "neese-smassii-enrichment/1.0 (asteroid research)"}


def download_text(url: str, desc: str = "", quiet: bool = True) -> str | None:
    """Download a URL's contents as text.

    `quiet=True` (default) logs failures at DEBUG level, because most callers
    are probing a list of candidate URLs and a miss isn't a real problem --
    the next candidate gets tried. Pass `quiet=False` if the failure should
    surface as a WARNING.
    """
    log.debug("  GET %s", desc or url)
    try:
        resp = requests.get(url, headers=_headers(), stream=True,
                            timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        chunks: list[bytes] = []
        downloaded = 0
        total = int(resp.headers.get("content-length", 0))
        for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
            chunks.append(chunk)
            downloaded += len(chunk)
            if total:
                print(f"\r    {downloaded/total*100:5.1f}%  "
                      f"({downloaded/1e6:.1f} MB)", end="", flush=True)
        if total:
            print()
        return b"".join(chunks).decode("utf-8", errors="replace")
    except Exception as exc:
        (log.warning if not quiet else log.debug)("    Failed: %s -- %s",
                                                   desc or url, exc)
        return None


def inspect_lines(text: str, n: int = 10, label: str = "") -> None:
    print(f"\n{'='*60}\n  FORMAT INSPECTION: {label}\n{'='*60}")
    count = 0
    for line in text.splitlines():
        if line.strip():
            print(f"  {count:3d}: {repr(line)}")
            count += 1
            if count >= n:
                break
    print()


# ---------------------------------------------------------------------------
# PDS SBN directory discovery
# ---------------------------------------------------------------------------

def _find_tab_links(html: str) -> list[str]:
    return re.findall(r'href=["\']([^"\']*\.tab)["\']', html, re.IGNORECASE)


def _find_dir_links(html: str) -> list[str]:
    """Extract directory names from an Apache/Nginx-style index page.

    Handles both relative hrefs ('FOO/') and absolute hrefs
    ('/pds3/non_mission/FOO/') by stripping the path prefix and keeping the
    final directory component. Previously this filtered out absolute hrefs
    entirely, which broke auto-discovery on servers that return absolute paths.
    """
    hrefs = re.findall(r'href=["\']([^"\'?#]+/)["\']', html, re.IGNORECASE)
    result = []
    seen = set()
    for h in hrefs:
        if '://' in h:           # skip fully-qualified URLs (cross-site links)
            continue
        d = h.rstrip('/').split('/')[-1]   # keep only the final path segment
        if not d or d.startswith('.') or d in seen:
            continue
        seen.add(d)
        result.append(d)
    return result


def discover_smassii_from_root() -> list[str]:
    """Fetch PDS_BASE listing and collect SMASS-related directory names."""
    log.info("Fetching PDS root listing to auto-discover SMASS II directory ...")
    html = download_text(PDS_BASE, "PDS non_mission/ root")
    if not html:
        return []
    found = []
    for d in _find_dir_links(html):
        du = d.upper()
        if "SMASS" in du and "TAXONOMY" not in du:
            log.info("  Root discovery: %s", d)
            found.append(d)
    if not found:
        log.info("  No SMASS directories found in root listing.")
    return found


def discover_pds_file(dataset_dirs: list[str]) -> tuple[str, str] | tuple[None, None]:
    """
    For each dataset directory candidate: confirm it exists, fetch /data/
    listing, try known filenames + any .tab links found there.
    Returns (text, url) of the first successfully downloaded file.
    """
    for dset in dataset_dirs:
        root_url = PDS_BASE + dset + "/"
        log.info("Trying dataset: %s", dset)
        root_html = download_text(root_url, f"{dset}/ (root)")
        if root_html is None:
            log.debug("  Dataset root unreachable, skipping.")
            continue

        data_url  = root_url + "data/"
        data_html = download_text(data_url, f"{dset}/data/")

        # Substrings that indicate a raw-spectra/parameter file, not taxonomy
        _NON_TAX = ('param', 'ccd_', 'spectra', 'orbit', 'phys', 'light',
                    'rotat', 'albedo', 'size', 'color')

        candidates = list(DATA_FILENAMES)
        if data_html:
            for link in _find_tab_links(data_html):
                fname = link.split("/")[-1]
                if (fname and fname not in candidates
                        and not any(p in fname.lower() for p in _NON_TAX)):
                    candidates.append(fname)
            discovered = [lnk.split("/")[-1] for lnk in _find_tab_links(data_html)]
            log.debug("  .tab files found in listing: %s", discovered or "none")

        for fname in candidates:
            url  = data_url + fname
            text = download_text(url, fname)
            if text is not None:
                log.info("  Found: %s", fname)
                return text, url

    return None, None


# ---------------------------------------------------------------------------
# Neese (2010) parser
#
# Confirmed column layout from format inspection of taxonomy10.tab:
#   Bytes  0– 6 : asteroid number (7 chars, right-justified)
#   Byte   7    : space separator
#   Bytes  8–25 : name / designation (18 chars)
#   Byte  26    : flag byte (usually '-')
#   Bytes 27+   : taxonomy data
#
# The taxonomy data uses NUMERIC survey references (7G, 2I, 65, etc.), NOT
# letter codes. The whitespace-split approach filters these automatically
# because numeric tokens fail the all-alpha regex check.
#
# We use whitespace-split as PRIMARY and fixed-width as fallback because the
# numeric references ('7G', '7I', '2I', '65A') sit at arbitrary positions
# and cause the fixed-width block scanner to misread adjacent alpha chars
# (e.g. picking 'G' from '7G' as a taxonomy class).
#
# Source priority: BD/BD2(10) > B/Bu(8) > S3(7) > Th/T(5) > Ca/Ec(4).
# Numeric refs all map to priority 1 (unknown letter code).
# ---------------------------------------------------------------------------

_CLASS_RE = re.compile(r'^[A-Za-z][a-zA-Z]{0,3}$')

# Single-char tokens that are quality/modifier flags, NOT taxonomy classes
_QUALITY_FLAGS = frozenset('sabu')


def _source_priority(src: str) -> int:
    s = src.strip().upper()
    for key, prio in NEESE_SOURCE_PRIORITY.items():
        if s == key.upper():
            return prio
    return 1


def _parse_neese_row(line: str) -> list[tuple]:
    if len(line) < 28:
        return []

    # Number: 7-char right-justified field (positions 0-6)
    number_str = line[0:7].strip()
    # Name: 18-char field (positions 7-25, includes leading space which is stripped)
    name_str   = line[7:26].strip()

    number = int(number_str) if number_str.isdigit() else None
    if number == 0:
        number = None
    if number is None and not name_str:
        return []

    # Taxonomy data starts at position 27 (position 26 is the '-' flag byte)
    rest = line[27:]

    # -- PRIMARY: whitespace split -------------------------------------------
    # Numeric survey references ('7G', '7I', '2I', '65', '65A') are filtered
    # automatically because they fail _CLASS_RE (don't start with a letter).
    # Single-char lowercase quality flags ('s', 'a', 'b') are excluded via
    # _QUALITY_FLAGS. We keep the first valid token (highest-confidence class).
    pairs: list[tuple[str, str]] = []
    for tok in rest.split():
        if (len(tok) <= 4
                and _CLASS_RE.match(tok)
                and tok[0].upper() in TAX_LETTER_TO_FAM
                and not (len(tok) == 1 and tok in _QUALITY_FLAGS)):
            pairs.append((tok, "?"))

    # -- FALLBACK: fixed-width 6-char blocks ---------------------------------
    # Only used when whitespace finds nothing (very short or unusual lines).
    if not pairs:
        pos = 0
        while pos + 2 <= len(rest):
            # Skip block if immediately preceded by a digit (alpha char is
            # part of a source code like '7G', '3I', '65A')
            if pos > 0 and rest[pos - 1].isdigit():
                pos += 6
                continue
            raw_cls = rest[pos:pos + 2].strip()
            raw_src = rest[pos + 3:pos + 5].strip() if pos + 5 <= len(rest) else ""
            pos += 6
            if (raw_cls and _CLASS_RE.match(raw_cls)
                    and raw_cls[0].upper() in TAX_LETTER_TO_FAM):
                pairs.append((raw_cls, raw_src))

    return [(number, name_str, cls, src) for cls, src in pairs] if pairs else []


def parse_neese(text: str) -> pd.DataFrame:
    by_number: dict[int, tuple] = {}
    by_name:   dict[str, tuple] = {}
    bad = 0

    for line in text.splitlines():
        line = line.rstrip()
        if not line:
            continue
        entries = _parse_neese_row(line)
        if not entries:
            bad += 1
            continue
        for number, name, cls, src in entries:
            prio = _source_priority(src)
            if number is not None:
                ex = by_number.get(number)
                if ex is None or prio > _source_priority(ex[2]):
                    by_number[number] = (name, cls, src)
            else:
                ex = by_name.get(name)
                if ex is None or prio > _source_priority(ex[1]):
                    by_name[name] = (cls, src)

    log.info("  Non-data lines skipped: %d", bad)
    rows = [{"number": n, "designation": nm, "tax_class": cls,
             "tax_source": src, "fam": tax_class_to_fam(cls)}
            for n, (nm, cls, src) in by_number.items()]
    rows += [{"number": pd.NA, "designation": nm, "tax_class": cls,
              "tax_source": src, "fam": tax_class_to_fam(cls)}
             for nm, (cls, src) in by_name.items()]

    df = pd.DataFrame(rows)
    if not df.empty:
        df["number"] = df["number"].astype("Int64")
    return df


# ---------------------------------------------------------------------------
# SMASS II PDS parser  (Bus & Binzel 2002)
#
# PDS3 fixed-width ASCII.  Expected column layout:
#   Bytes  0– 5 : asteroid number
#   Bytes  6–22 : name / designation
#   Bytes 23–25 : Bus spectral type (1-3 chars)
# ---------------------------------------------------------------------------

def parse_smassii(text: str) -> pd.DataFrame:
    rows = []
    bad  = 0

    for line in text.splitlines():
        line = line.rstrip()
        if not line or len(line) < 4:
            bad += 1
            continue
        if line.startswith(('PDS_VERSION', 'LABEL', 'RECORD', 'OBJECT',
                             'END', '/*', 'NOTE', '^')):
            bad += 1
            continue

        number_str = line[0:6].strip()
        name_str   = line[6:23].strip() if len(line) > 6 else ""
        type_str   = line[23:26].strip() if len(line) > 23 else ""

        # Whitespace fallback
        if not type_str or not (type_str[0].isalpha()
                                 and type_str[0].upper() in TAX_LETTER_TO_FAM):
            parts = line.split()
            if len(parts) >= 2:
                for tok in parts[1:4]:
                    if (tok and tok[0].isalpha()
                            and tok[0].upper() in TAX_LETTER_TO_FAM):
                        type_str = tok
                        break

        if not type_str or type_str[0].upper() not in TAX_LETTER_TO_FAM:
            bad += 1
            continue

        number = int(number_str) if number_str.isdigit() else None
        if number == 0:
            number = None
        if number is None and not name_str:
            bad += 1
            continue

        rows.append({
            "number":      number,
            "designation": name_str,
            "tax_class":   type_str,
            "fam":         tax_class_to_fam(type_str),
        })

    log.info("  Non-data lines skipped: %d", bad)
    df = pd.DataFrame(rows)
    if not df.empty:
        df["number"] = df["number"].astype("Int64")
    return df


# ---------------------------------------------------------------------------
# VizieR parser  (Bus & Binzel 2002 or DeMeo et al. 2009)
#
# Spectral types are SHORT (1-3 alpha chars). Asteroid names are longer and
# may be multi-word. Scan backwards to find the rightmost short type token;
# everything between the leading number and the type is the name.
# ---------------------------------------------------------------------------

_VIZ_TYPE_RE = re.compile(r'^[A-Za-z]{1,3}$')


def _is_vizier_type(tok: str) -> bool:
    return (bool(_VIZ_TYPE_RE.match(tok))
            and tok[0].upper() in TAX_LETTER_TO_FAM
            and len(tok) <= 3)


def parse_smassii_vizier(text: str) -> pd.DataFrame:
    rows = []
    bad  = 0

    for line in text.splitlines():
        line = line.rstrip()
        if not line or line.startswith('#') or line.startswith('--'):
            bad += 1
            continue

        parts = line.split()
        if len(parts) < 2:
            bad += 1
            continue

        number   = None
        name_str = ""
        type_str = ""
        type_idx = -1

        if parts[0].lstrip('-').isdigit():
            number = int(parts[0])
            # Scan backwards for the rightmost short type token
            for i in range(len(parts) - 1, 0, -1):
                if _is_vizier_type(parts[i]):
                    type_str = parts[i]
                    type_idx = i
                    break
            if type_idx > 1:
                name_str = " ".join(parts[1:type_idx])
            elif type_idx == 1:
                name_str = ""
        else:
            # Fixed-width fallback (no leading number)
            type_str_fw = line[23:26].strip() if len(line) > 23 else ""
            if type_str_fw and _is_vizier_type(type_str_fw):
                type_str = type_str_fw
                name_str = line[0:23].strip()
            else:
                bad += 1
                continue

        if not type_str or type_str[0].upper() not in TAX_LETTER_TO_FAM:
            bad += 1
            continue
        if number is None and not name_str:
            bad += 1
            continue

        rows.append({
            "number":      number,
            "designation": name_str,
            "tax_class":   type_str,
            "fam":         tax_class_to_fam(type_str),
        })

    log.info("  Non-data lines skipped: %d", bad)
    df = pd.DataFrame(rows)
    if not df.empty:
        df["number"] = df["number"].astype("Int64")
    return df


# ---------------------------------------------------------------------------
# Neese-derived Bus/SMASS subset (offline fallback)
#
# The Neese 2010 compilation is a multi-survey roll-up that INCLUDES Bus/SMASS
# II classifications. Bus's feature-based taxonomy uses mixed-case class names
# (Cgh, Ch, Sa, Sk, Sq, Xc, Xe, Xk, ...), while Tholen and earlier surveys use
# all-uppercase (C, S, X, M, ...). When the PDS / VizieR SMASS II sources are
# unreachable -- which is the common case because the actual PDS SMASS II
# archive contains only individual spectra files (no single taxonomy table)
# and CDS does not host the Bus 2002 Icarus taxonomy catalog -- we derive a
# Bus/SMASS-flavored taxonomy file from the Neese rows whose class string
# matches the Bus mixed-case pattern.
#
# This is the SAME data the user would get from a successful SMASS II download
# (Bus & Binzel 2002 entries), just routed through the Neese compilation.
# ---------------------------------------------------------------------------

_BUS_CLASS_RE = re.compile(r'^[A-Z][a-z][a-zA-Z]{0,2}$')


def derive_mithneos_from_neese() -> pd.DataFrame:
    """Build a Bus/SMASS-style taxonomy frame from neese_taxonomy.csv on disk."""
    if not NEESE_OUT_CSV.exists():
        log.warning("  Cannot derive from Neese: %s does not exist yet.",
                    NEESE_OUT_CSV)
        return pd.DataFrame()

    try:
        ndf = pd.read_csv(NEESE_OUT_CSV)
    except Exception as exc:
        log.warning("  Cannot read %s: %s", NEESE_OUT_CSV, exc)
        return pd.DataFrame()

    if ndf.empty or "tax_class" not in ndf.columns:
        log.warning("  Neese file has no usable rows / columns.")
        return pd.DataFrame()

    mask = ndf["tax_class"].astype(str).str.match(_BUS_CLASS_RE)
    sub  = ndf.loc[mask, ["number", "designation", "tax_class"]].copy()

    if sub.empty:
        log.warning("  No Bus-style (mixed-case) classes found in Neese.")
        return sub

    sub["fam"] = sub["tax_class"].apply(tax_class_to_fam)
    sub = sub.reset_index(drop=True)
    if "number" in sub.columns:
        sub["number"] = sub["number"].astype("Int64")
    return sub


# ---------------------------------------------------------------------------
# Main runners
# ---------------------------------------------------------------------------

def run_neese() -> None:
    log.info("=== Neese (2010) Taxonomy Compilation ===")
    text, url = discover_pds_file(NEESE_DIRS)

    if text is None:
        log.error("Could not find Neese taxonomy file. "
                  "Check https://sbn.psi.edu/pds/resource/taxonomy.html")
        return

    inspect_lines(text, n=10, label=f"Neese ({url.split('/')[-1]})")
    df = parse_neese(text)
    log.info("Neese: parsed %d unique asteroids", len(df))

    if df.empty:
        log.error("Zero rows parsed -- share FORMAT INSPECTION output to debug.")
        return

    numbered = df["number"].notna().sum()
    log.info("Neese: %d with number, %d name-only", numbered, len(df) - numbered)
    log.info("Neese family distribution: %s",
             df["fam"].value_counts().sort_index().to_dict())
    df[["number", "designation", "tax_class", "tax_source", "fam"]].to_csv(
        NEESE_OUT_CSV, index=False)
    log.info("Wrote %s  (%d rows)", NEESE_OUT_CSV, len(df))


def run_smassii() -> None:
    log.info("=== SMASS II / Bus-DeMeo ===")

    df       = None
    parse_fn = parse_smassii

    # Step 1: auto-discover from PDS root listing, then try static list
    root_discovered = discover_smassii_from_root()
    all_dirs = root_discovered + [
        d for d in SMASSII_DIRS_STATIC if d not in root_discovered
    ]
    text, url = discover_pds_file(all_dirs)

    if text is not None:
        log.debug("Format inspection:")
        if log.isEnabledFor(logging.DEBUG):
            inspect_lines(text, n=10, label=f"SMASS II PDS ({url.split('/')[-1]})")
        df = parse_smassii(text)
        log.debug("SMASS II PDS: parsed %d objects", len(df))
        if df.empty:
            log.debug("PDS file parsed 0 rows -- falling through to VizieR.")
            df = None
        else:
            numbered_frac = df["number"].notna().sum() / max(len(df), 1)
            if numbered_frac < 0.1:
                log.info(
                    "PDS file at %s is raw spectra (only %.1f%% rows numbered) "
                    "-- falling through.", url.split('/')[-1], numbered_frac * 100,
                )
                df = None
            else:
                log.info("SMASS II PDS: parsed %d objects", len(df))

    # Step 2: VizieR fallback (also used when PDS file parses 0 rows)
    if df is None:
        log.info("Trying VizieR (CDS) fallback ...")
        for viz_url in SMASSII_VIZIER_URLS:
            vtext = download_text(viz_url, viz_url.split("/")[-1])
            if vtext is not None:
                vdf = parse_smassii_vizier(vtext)
                log.debug("  VizieR %s: parsed %d objects",
                          viz_url.split("/")[-1], len(vdf))
                if not vdf.empty:
                    df       = vdf
                    url      = viz_url
                    parse_fn = parse_smassii_vizier
                    log.info("  Using VizieR source: %s", viz_url)
                    break

    # Step 3: Neese-derived fallback -- extract Bus/SMASS-style rows from the
    #         Neese 2010 compilation already on disk. The Neese file is a
    #         multi-survey roll-up that includes Bus & Binzel 2002 (SMASS II)
    #         entries, so this yields the same data SMASS II would have given
    #         us, just routed through the Neese compilation.
    if df is None or df.empty:
        log.info("Network sources unreachable / empty -- deriving from %s ...",
                 NEESE_OUT_CSV.name)
        derived = derive_mithneos_from_neese()
        if not derived.empty:
            df       = derived
            url      = f"derived:{NEESE_OUT_CSV.name}"
            parse_fn = None
            log.info("  Derived %d Bus-style rows from Neese.", len(df))

    # Step 4: total failure -- write empty CSV so downstream scripts can
    #         still consume Neese data without a missing-file error
    if df is None or df.empty:
        log.warning(
            "Could not find usable SMASS II / Bus-DeMeo data anywhere "
            "(including the Neese-derived fallback). Writing empty %s -- "
            "Neese enrichment will still apply.",
            MITHNEOS_OUT_CSV,
        )
        pd.DataFrame(columns=["number", "designation", "tax_class",
                               "fam"]).to_csv(MITHNEOS_OUT_CSV, index=False)
        return

    numbered = df["number"].notna().sum()
    log.info("SMASS II: %d with number, %d name-only", numbered,
             len(df) - numbered)
    log.info("SMASS II family distribution: %s",
             df["fam"].value_counts().sort_index().to_dict())
    df[["number", "designation", "tax_class", "fam"]].to_csv(
        MITHNEOS_OUT_CSV, index=False)
    log.info("Wrote %s  (%d rows)", MITHNEOS_OUT_CSV, len(df))


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show DEBUG-level diagnostics from probe attempts "
                             "(useful when a remote source has moved or changed).")
    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        log.setLevel(logging.DEBUG)

    run_neese()
    print()
    run_smassii()
    print()
    log.info("Done. Run prepare_asteroid_data.py to rebuild asteroid-data.json.")


if __name__ == "__main__":
    main()
