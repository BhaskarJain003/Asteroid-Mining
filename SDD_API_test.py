#!/usr/bin/env python3
"""
SSD_API_test.py

Download all NEAs from the JPL Small-Body Database (SBDB) Query API
and write them to a CSV using all currently available output fields.

Docs:
- SBDB Query API: https://ssd-api.jpl.nasa.gov/doc/sbdb_query.html
- SBDB Filter docs: https://ssd-api.jpl.nasa.gov/doc/sbdb_filter.html
- SBDB overview: https://ssd-api.jpl.nasa.gov/doc/sbdb.html

Default behavior:
- Filters to near-Earth objects via sb-group=neo
- Filters to asteroids only via sb-kind=a
- Auto-discovers all available output fields using info=field
- Pages through all matching records
- Writes a CSV with every returned field

Examples:
    python SSD_API_test.py
    python SSD_API_test.py --output all_neas_sbdb.csv
    python SSD_API_test.py --page-size 2000 --full-prec
    python SSD_API_test.py --include-comets --output all_neos.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import requests

BASE_URL = "https://ssd-api.jpl.nasa.gov/sbdb_query.api"
DEFAULT_OUTPUT = "SDD_API_test.csv"
DEFAULT_PAGE_SIZE = 1000
REQUEST_TIMEOUT = 60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download all NEAs from JPL SBDB Query API into a CSV."
    )
    parser.add_argument(
        "--output",
        type=str,
        default=DEFAULT_OUTPUT,
        help=f"Output CSV path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help=f"Number of rows per API page (default: {DEFAULT_PAGE_SIZE})",
    )
    parser.add_argument(
        "--full-prec",
        action="store_true",
        help="Request full numeric precision from the API.",
    )
    parser.add_argument(
        "--include-comets",
        action="store_true",
        help="Include NEO comets too. Default is asteroids only (NEAs).",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.15,
        help="Sleep time in seconds between paged requests (default: 0.15).",
    )
    return parser.parse_args()


def safe_get(
    url: str,
    params: Dict[str, Any],
    timeout: int = REQUEST_TIMEOUT,
    max_retries: int = 3,
) -> Dict[str, Any]:
    last_err: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_err = exc
            if attempt < max_retries:
                time.sleep(1.5 * attempt)
            else:
                raise RuntimeError(
                    f"Request failed after {max_retries} attempts.\n"
                    f"URL: {url}\n"
                    f"Params: {params}\n"
                    f"Error: {exc}"
                ) from exc

    raise RuntimeError(f"Unexpected request failure: {last_err}")


def get_all_available_fields() -> List[str]:
    """
    Query SBDB for the currently available output fields.
    The API returns metadata for fields via info=field.
    Structure: info.field.{object,orbit,phys_par}.list[].name
    """
    payload = safe_get(BASE_URL, {"info": "field"})

    field_obj = payload.get("info", {}).get("field")
    if isinstance(field_obj, dict):
        extracted: List[str] = []
        for category_data in field_obj.values():
            if isinstance(category_data, dict) and "list" in category_data:
                for item in category_data["list"]:
                    if isinstance(item, dict) and "name" in item:
                        extracted.append(str(item["name"]))
        if extracted:
            return extracted

    raise RuntimeError(
        "Could not parse field list from SBDB Query API response for info=field.\n"
        f"Response keys: {list(payload.keys())}"
    )


def get_result_count(include_comets: bool) -> int:
    params: Dict[str, Any] = {"sb-group": "neo"}
    if not include_comets:
        params["sb-kind"] = "a"

    payload = safe_get(BASE_URL, params)

    if "count" not in payload:
        raise RuntimeError(
            "Count query did not return 'count'. "
            f"Response keys: {list(payload.keys())}"
        )

    return int(payload["count"])


def fetch_page(
    fields: List[str],
    offset: int,
    limit: int,
    include_comets: bool,
    full_prec: bool,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "sb-group": "neo",
        "fields": ",".join(fields),
        "limit": limit,
        "limit-from": offset,
    }

    if not include_comets:
        params["sb-kind"] = "a"

    if full_prec:
        params["full-prec"] = "true"

    return safe_get(BASE_URL, params)


def write_csv(
    output_path: Path,
    header_fields: List[str],
    rows: List[List[Any]],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header_fields)
        writer.writerows(rows)


def main() -> int:
    args = parse_args()

    if args.page_size <= 0:
        print("Error: --page-size must be > 0", file=sys.stderr)
        return 1

    output_path = Path(args.output)

    print("Discovering all available SBDB output fields...")
    all_fields = get_all_available_fields()
    print(f"Discovered {len(all_fields)} fields.")

    print("Querying total object count...")
    total_count = get_result_count(include_comets=args.include_comets)
    kind_label = "NEOs (asteroids + comets)" if args.include_comets else "NEAs only"
    print(f"Total matching objects for {kind_label}: {total_count}")

    all_rows: List[List[Any]] = []
    fetched = 0

    while fetched < total_count:
        print(
            f"Fetching rows {fetched} to "
            f"{min(fetched + args.page_size - 1, total_count - 1)}..."
        )
        payload = fetch_page(
            fields=all_fields,
            offset=fetched,
            limit=args.page_size,
            include_comets=args.include_comets,
            full_prec=args.full_prec,
        )

        response_fields = payload.get("fields")
        data_rows = payload.get("data")

        if not isinstance(response_fields, list) or not isinstance(data_rows, list):
            raise RuntimeError(
                "Unexpected page response format.\n"
                f"Response keys: {list(payload.keys())}"
            )

        # On the first page, trust the returned order from the API.
        if fetched == 0:
            all_fields = [str(x) for x in response_fields]

        for row in data_rows:
            if isinstance(row, list):
                all_rows.append(row)
            else:
                raise RuntimeError("Encountered a non-list row in API response.")

        fetched += len(data_rows)

        if len(data_rows) == 0:
            print(
                "Warning: API returned zero rows before reaching expected total count. "
                "Stopping early."
            )
            break

        time.sleep(args.sleep)

    print(f"Writing CSV to: {output_path}")
    write_csv(output_path, all_fields, all_rows)

    print("\nDone.")
    print(f"Rows written: {len(all_rows)}")
    print(f"Columns written: {len(all_fields)}")
    print(f"Output file: {output_path.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
EXPLANATION OF FIELDS:
I’m using JPL’s official SBDB Query/API field definitions, plus the SBDB filter page for a few shorthand model/photometric parameters that are named there explicitly.

orbit_id: orbit solution ID; an identifier for the specific orbit solution JPL is using.

epoch: epoch of osculation in Julian Day, in TDB; the reference time at which the orbital elements are valid.

epoch_mjd: same epoch, but in Modified Julian Day form.

epoch_cal: same epoch, but in calendar form YYYY-MM-DD.D.

equinox: equinox/reference frame of the elements, such as J2000.

e: eccentricity; how non-circular the orbit is. 0 is circular, values closer to 1 are more elongated.

a: semimajor axis in au; the orbit’s characteristic size.

q: perihelion distance in au; the closest distance to the Sun.

i: inclination in degrees; tilt of the orbit relative to the reference plane.

om: longitude of the ascending node in degrees; orientation of the orbital plane.

w: argument of perihelion in degrees; orientation of perihelion within the orbital plane.

ma: mean anomaly in degrees; where the body is along its orbit at the epoch.

ad: aphelion distance in au; the farthest distance from the Sun.

n: mean motion in deg/day; average angular orbital speed.

tp: time of perihelion passage in Julian Day, TDB.

tp_cal: time of perihelion passage in calendar form YYYY-MM-DD.D.

per: orbital period in days.

per_y: orbital period in Julian years.

moid: Earth MOID in au; minimum distance between the object’s orbit and Earth’s orbit. It is an orbit-to-orbit metric, not an actual encounter distance at a specific time.

moid_ld: Earth MOID in lunar distances instead of au. JPL explicitly notes moid_ld is the lunar-distance version of moid.

moid_jup: Jupiter MOID in au; minimum distance between the object’s orbit and Jupiter’s orbit.

t_jup: Jupiter Tisserand parameter; a dynamical classification quantity often used to distinguish asteroid-like and comet-like orbital behavior relative to Jupiter.

sigma_e: 1-sigma uncertainty in eccentricity.

sigma_a: 1-sigma uncertainty in semimajor axis, in au.

sigma_q: 1-sigma uncertainty in perihelion distance, in au.

sigma_i: 1-sigma uncertainty in inclination, in degrees.

sigma_om: 1-sigma uncertainty in longitude of ascending node, in degrees.

sigma_w: 1-sigma uncertainty in argument of perihelion, in degrees.

sigma_ma: 1-sigma uncertainty in mean anomaly, in degrees.

sigma_ad: 1-sigma uncertainty in aphelion distance, in au.

sigma_n: 1-sigma uncertainty in mean motion, in deg/day.

sigma_tp: 1-sigma uncertainty in time of perihelion passage, in days.

sigma_per: 1-sigma uncertainty in period, in days.

class: orbit classification code, such as Amor, Apollo, Aten, MBA, comet classes, etc.

producer: name of the person or institution responsible for the orbit determination.

data_arc: number of days spanned by the observations used in the orbit determination. Longer arcs generally mean a better-constrained orbit.

first_obs: date of the first observation used in the orbit.

last_obs: date of the last observation used in the orbit.

n_obs_used: total number of observations of all types used in the orbit solution.

n_del_obs_used: number of radar delay observations used.

n_dop_obs_used: number of radar Doppler observations used.

condition_code: MPC “U” parameter, from 0 to 9; 0 means a well-determined orbit and 9 means highly uncertain.

rms: normalized RMS of the fit of the orbit solution to the observations. Lower is generally better, but interpretation depends on weighting/model details.

two_body: flag indicating a low-precision two-body dynamical model was used in orbit determination. Usually T/F style.

A1: non-gravitational radial parameter. In practice this is one of the outgassing/rocket-force model parameters, mainly relevant for comets and a few special objects.

A1_sigma: 1-sigma uncertainty in A1.

A2: non-gravitational transverse parameter.

A2_sigma: 1-sigma uncertainty in A2.

A3: non-gravitational normal parameter.

A3_sigma: 1-sigma uncertainty in A3.

DT: non-gravitational perihelion-maximum offset; part of the non-gravitational force model.

DT_sigma: 1-sigma uncertainty in DT.

H: absolute magnitude; brightness normalized to 1 au from both Sun and observer. Often used as a size proxy when diameter is unavailable.

G: asteroid magnitude slope parameter in the standard H/G law.

M1: comet total magnitude parameter.

M2: comet total magnitude slope parameter.

K1: comet nuclear magnitude parameter.

K2: comet nuclear magnitude slope parameter.

PC: comet nuclear magnitude law phase coefficient.

diameter: effective body diameter in km.

extent: triaxial or biaxial body dimensions in km; a shape-size description rather than a single effective diameter.

albedo: geometric albedo; reflectivity. Higher albedo means a more reflective surface.

rot_per: synodic rotation period in hours.

GM: gravitational parameter 
GM
GM, in km
3
3
/s
2
2
; mass times the gravitational constant.

BV: color index B−V.

UB: color index U−B.

IR: color index I−R.

spec_B: SMASSII spectral taxonomic class.

spec_T: Tholen spectral taxonomic class.

H_sigma: 1-sigma uncertainty in H.

diameter_sigma: 1-sigma formal or estimated uncertainty in diameter.

spkid: primary SPK-ID; JPL’s stable object identifier used in SPICE/SPK contexts.

full_name: full designation and name of the object.

pdes: primary designation; for numbered asteroids this is the IAU number.

name: IAU name, if one exists.

prefix: comet prefix such as P, C, D, or A; for most asteroids this is blank/null.

neo: flag indicating whether the object is a near-Earth object, Y or N.

pha: flag indicating whether the object is a potentially hazardous asteroid/object, Y or N.

sats: satellite-related data/flag. In the single-object SBDB API, satellite data are returned in a dedicated sat section when requested; in a query-table context this field indicates associated satellite information exists or encodes satellite-related content for the object. JPL documents the full satellite section separately rather than this table-field in detail.

A few practical interpretations for your mining workflow:

Best first-pass orbital screening fields: a, e, q, i, moid, t_jup, class.

Best “data quality” fields: condition_code, data_arc, n_obs_used, rms, plus the sigma_* uncertainties.

Best “physical economics” fields: H, diameter, albedo, spec_B, spec_T, rot_per, diameter_sigma.

For most NEA asteroid work, M1, M2, K1, K2, PC, A1, A2, A3, and DT will often be blank because they are mainly comet/non-gravitational-model fields.

I can also turn this into a CSV-style data dictionary table with columns like field, units, category, plain_english_meaning, useful_for_mining?.

"""
