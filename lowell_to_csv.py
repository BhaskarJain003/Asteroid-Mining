# lowell_to_csv.py

from __future__ import annotations

import csv
import gzip
import io
import re
from pathlib import Path
from typing import Iterable

import requests


LOWELL_URL = "https://ftp.lowell.edu/pub/elgb/astorb.dat.gz"
OUTPUT_CSV = "lowell_astorb.csv"
TIMEOUT = 120


# Column positions are 1-based and inclusive in the Lowell documentation.
# Converted below to Python slice indices [start:end).
# Source: Lowell astorb.dat fixed-width record documentation.
FIELD_SPECS = [
    ("number", 1, 6),
    ("name_or_designation", 8, 25),
    ("orbit_computer", 27, 41),
    ("H", 43, 47),
    ("G", 49, 53),
    ("B_V", 55, 58),
    ("iras_diameter_km", 60, 64),
    ("iras_tax_class", 66, 69),
    ("code_1", 71, 74),
    ("code_2", 75, 78),
    ("code_3", 79, 82),
    ("code_4", 83, 86),
    ("code_5", 87, 90),
    ("code_6", 91, 94),
    ("orbital_arc_days", 96, 100),
    ("n_observations", 101, 105),
    ("epoch_yyyymmdd", 107, 114),
    ("mean_anomaly_deg", 116, 125),
    ("arg_perihelion_deg", 127, 136),
    ("long_asc_node_deg", 137, 146),
    ("inclination_deg", 147, 156),
    ("eccentricity", 158, 167),
    ("semimajor_axis_au", 168, 180),
    ("orbit_comp_date_yymmdd", 182, 187),
    ("ceu_arcsec", 189, 195),
    ("ceu_rate_arcsec_per_day", 197, 204),
    ("ceu_date_yyyymmdd", 206, 213),
    ("next_peu_arcsec", 215, 221),
    ("next_peu_date_yyyymmdd", 223, 230),
    ("max_peu_10y_arcsec", 232, 238),
    ("max_peu_10y_date_yyyymmdd", 240, 247),
    ("post_obs_max_peu_10y_arcsec", 249, 255),
    ("post_obs_max_peu_10y_date_yyyymmdd", 257, 264),
]


def download_gzip(url: str) -> bytes:
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.content


def gunzip_bytes(data: bytes) -> str:
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
        return gz.read().decode("latin-1", errors="replace")


def clean_value(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    return text


def parse_record(line: str) -> dict[str, str]:
    row: dict[str, str] = {}
    for field_name, start_1b, end_1b in FIELD_SPECS:
        start = start_1b - 1
        end = end_1b
        raw = line[start:end]
        row[field_name] = clean_value(raw)
    return row


def iter_records(text: str) -> Iterable[dict[str, str]]:
    for i, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        # Lowell documents each record as 266 columns wide.
        # Keep parsing even if a line is slightly short/long, but pad if short.
        if len(line) < 266:
            line = line.ljust(266)
        yield parse_record(line)


def maybe_numeric(row: dict[str, str]) -> dict[str, object]:
    numeric_fields = {
        "H", "G", "B_V", "iras_diameter_km",
        "code_1", "code_2", "code_3", "code_4", "code_5", "code_6",
        "orbital_arc_days", "n_observations",
        "mean_anomaly_deg", "arg_perihelion_deg", "long_asc_node_deg",
        "inclination_deg", "eccentricity", "semimajor_axis_au",
        "ceu_arcsec", "ceu_rate_arcsec_per_day",
        "next_peu_arcsec", "max_peu_10y_arcsec", "post_obs_max_peu_10y_arcsec",
    }

    out: dict[str, object] = {}
    for k, v in row.items():
        if v == "":
            out[k] = ""
            continue

        if k in numeric_fields:
            # Convert FORTRAN D-exponent to E if present
            vv = re.sub(r"([0-9])D([+-]?[0-9]+)", r"\1E\2", v)
            try:
                if k.startswith("code_") or k in {"orbital_arc_days", "n_observations"}:
                    out[k] = int(float(vv))
                else:
                    out[k] = float(vv)
            except ValueError:
                out[k] = v
        else:
            out[k] = v
    return out


def main() -> None:
    print(f"Downloading Lowell astorb data from: {LOWELL_URL}")
    gz_bytes = download_gzip(LOWELL_URL)

    print("Decompressing...")
    text = gunzip_bytes(gz_bytes)

    print("Parsing fixed-width records...")
    rows = [maybe_numeric(r) for r in iter_records(text)]

    output_path = Path(OUTPUT_CSV)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[name for name, _, _ in FIELD_SPECS])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Done. Wrote {len(rows):,} rows to {output_path.resolve()}")


if __name__ == "__main__":
    main()