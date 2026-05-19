#!/usr/bin/env python3
"""
interactive_resource_slider_plot_matplotlib.py

Interactive local plot with Matplotlib sliders.

What it does:
- Reads asteroid CSV
- Estimates asteroid mass from diameter using a spherical approximation
- Assigns family-based lower/default/upper bounds for:
    * density
    * broad metal-rich usable resource mass fraction
    * water-equivalent usable resource mass fraction
- Creates an interactive stacked-area plot with sliders for every bounded variable

Plot:
- X-axis: asteroid rank (sorted by usable kg under current slider settings)
- Y-axis: cumulative usable kg
- Stacked areas: family subcategories
- Black line: total cumulative usable kg

Sliders:
- Density sliders for each family
- Metal fraction sliders for each family
- Water fraction sliders for each family
- Resource mode selector button (Metal / Water)

Outputs:
- asteroid_resource_bounds.csv
- asteroid_resource_summary.csv

Sources used for ranges:
Broad metal-rich usable fraction presets:
- Primitive / carbonaceous-like: 0–5%
- Ordinary stony / chondritic-like: 5–20%
- Metal-rich / iron-meteorite-like: 60–95%

Water-equivalent usable fraction presets:
- Primitive / carbonaceous-like: 0–15%
- Ordinary stony / chondritic-like: 0–2%
- Metal-rich / iron-meteorite-like: 0–0.5%

Density presets:
- Primitive / carbonaceous-like: 1500–2500 kg/m^3
- Ordinary stony / chondritic-like: 3000–3800 kg/m^3
- Metal-rich / iron-meteorite-like: 7000–8000 kg/m^3
- Other / unknown: 2500–3500 kg/m^3
"""

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

from bokeh.layouts import column, row
from bokeh.models import ColumnDataSource, CustomJS, Div, Select, Slider as BokehSlider
from bokeh.plotting import figure, output_file, save


DEFAULT_INPUT_CANDIDATES = [
    "sbdb_lowell_merged.csv",
    "SDD_API_test_cleaned.csv",
    "SSD_API_test_cleaned.csv",
]
DEFAULT_OUTPUT_DIR = "resource_slider_output"
MAX_PLOT_POINTS = 1500


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactive asteroid resource slider plot generator (Matplotlib)."
    )
    parser.add_argument("--input", type=str, default=None, help="Path to input CSV")
    parser.add_argument("--output-dir", type=str, default=DEFAULT_OUTPUT_DIR, help="Output folder")
    return parser.parse_args()


def find_input_file(explicit_path: str | None) -> Path:
    if explicit_path:
        p = Path(explicit_path)
        if not p.exists():
            raise FileNotFoundError(f"Input file not found: {p}")
        return p

    for candidate in DEFAULT_INPUT_CANDIDATES:
        p = Path(candidate)
        if p.exists():
            return p

    raise FileNotFoundError(
        "Could not find input CSV. Tried: " + ", ".join(DEFAULT_INPUT_CANDIDATES)
    )


def clean_taxonomy(value: object) -> str | None:
    if pd.isna(value):
        return None
    s = str(value).strip().upper()
    if s in {"", "NAN", "NONE", "NULL"}:
        return None
    s = re.sub(r"[^A-Z0-9]+", "", s)
    return s or None


def first_tax_letter(value: str | None) -> str | None:
    if value is None:
        return None
    m = re.search(r"[A-Z]", value)
    return m.group(0) if m else None


def classify_material_family(lowell_tax: str | None) -> str:
    t = clean_taxonomy(lowell_tax)
    letter = first_tax_letter(t)

    if letter in {"C", "B", "F", "G", "P", "D", "T"}:
        return "primitive_carbonaceous_like"

    if letter in {"S", "Q", "K", "L", "V", "A", "R"}:
        return "ordinary_stony_chondritic_like"

    if letter in {"M", "X", "E"}:
        return "metal_rich_iron_meteorite_like"

    return "other_or_unknown"


def estimate_preferred_diameter_km(df: pd.DataFrame) -> pd.Series:
    for col in ["diameter", "lowell_iras_diameter_km"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "diameter" in df.columns and "lowell_iras_diameter_km" in df.columns:
        return df["diameter"].combine_first(df["lowell_iras_diameter_km"])
    if "diameter" in df.columns:
        return df["diameter"]
    if "lowell_iras_diameter_km" in df.columns:
        return df["lowell_iras_diameter_km"]

    return pd.Series(np.nan, index=df.index)


def density_bounds_kg_m3(material_family: str) -> tuple[float, float, float]:
    if material_family == "primitive_carbonaceous_like":
        return 1500.0, 2000.0, 2500.0
    if material_family == "ordinary_stony_chondritic_like":
        return 3000.0, 3400.0, 3800.0
    if material_family == "metal_rich_iron_meteorite_like":
        return 7000.0, 7800.0, 8000.0
    return 2500.0, 3000.0, 3500.0


def metal_fraction_bounds(material_family: str) -> tuple[float, float, float]:
    if material_family == "primitive_carbonaceous_like":
        return 0.00, 0.025, 0.05
    if material_family == "ordinary_stony_chondritic_like":
        return 0.05, 0.12, 0.20
    if material_family == "metal_rich_iron_meteorite_like":
        return 0.60, 0.80, 0.95
    return 0.00, 0.10, 0.20


def water_fraction_bounds(material_family: str) -> tuple[float, float, float]:
    if material_family == "primitive_carbonaceous_like":
        return 0.00, 0.08, 0.15
    if material_family == "ordinary_stony_chondritic_like":
        return 0.00, 0.005, 0.02
    if material_family == "metal_rich_iron_meteorite_like":
        return 0.00, 0.001, 0.005
    return 0.00, 0.01, 0.05


def spherical_volume_m3(diameter_km: float | None) -> float | None:
    if diameter_km is None or pd.isna(diameter_km) or diameter_km <= 0:
        return None
    diameter_m = float(diameter_km) * 1000.0
    return (math.pi / 6.0) * (diameter_m ** 3)


def summarize_by_family(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.groupby("material_family", dropna=False)
        .agg(
            asteroid_count=("material_family", "size"),
            median_preferred_diameter_km=("preferred_diameter_km", "median"),
            total_mass_default_kg=("mass_default_kg", "sum"),
            total_metal_usable_default_kg=("metal_usable_default_kg", "sum"),
            total_water_usable_default_kg=("water_usable_default_kg", "sum"),
        )
        .reset_index()
    )


def downsample_curves(x, curves_dict, max_points=MAX_PLOT_POINTS):
    n = len(x)
    if n <= max_points:
        return x, curves_dict

    idx = np.linspace(0, n - 1, max_points).astype(int)
    x_ds = x[idx]
    curves_ds = {k: v[idx] for k, v in curves_dict.items()}
    return x_ds, curves_ds


def main() -> None:
    args = parse_args()
    input_path = find_input_file(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(input_path, low_memory=False)

    # Prefer the enriched best_tax_class column (populated by
    # enrich_taxonomy.py, priority MITHNEOS > Neese > SDSS > Lowell IRAS).
    # Fall back to the legacy lowell_iras_tax_class column if enrichment
    # hasn't been run yet.
    if "best_tax_class" not in df.columns:
        if "lowell_iras_tax_class" not in df.columns:
            df["lowell_iras_tax_class"] = pd.NA
        df["best_tax_class"] = df["lowell_iras_tax_class"]

    df["preferred_diameter_km"] = estimate_preferred_diameter_km(df)
    df["best_tax_class"] = df["best_tax_class"].map(clean_taxonomy)
    df["material_family"] = df["best_tax_class"].map(classify_material_family)
    df["volume_m3"] = df["preferred_diameter_km"].apply(spherical_volume_m3)

    # Save defaults table
    density_vals = df["material_family"].map(density_bounds_kg_m3)
    df["density_lower_kg_m3"] = density_vals.map(lambda x: x[0])
    df["density_default_kg_m3"] = density_vals.map(lambda x: x[1])
    df["density_upper_kg_m3"] = density_vals.map(lambda x: x[2])

    metal_vals = df["material_family"].map(metal_fraction_bounds)
    df["metal_fraction_lower"] = metal_vals.map(lambda x: x[0])
    df["metal_fraction_default"] = metal_vals.map(lambda x: x[1])
    df["metal_fraction_upper"] = metal_vals.map(lambda x: x[2])

    water_vals = df["material_family"].map(water_fraction_bounds)
    df["water_fraction_lower"] = water_vals.map(lambda x: x[0])
    df["water_fraction_default"] = water_vals.map(lambda x: x[1])
    df["water_fraction_upper"] = water_vals.map(lambda x: x[2])

    df["mass_default_kg"] = df["volume_m3"] * df["density_default_kg_m3"]
    df["metal_usable_default_kg"] = df["mass_default_kg"] * df["metal_fraction_default"]
    df["water_usable_default_kg"] = df["mass_default_kg"] * df["water_fraction_default"]

    preferred_cols = [
        "spkid", "full_name", "pdes", "class",
        "lowell_iras_tax_class", "material_family",
        "preferred_diameter_km", "volume_m3",
        "density_lower_kg_m3", "density_default_kg_m3", "density_upper_kg_m3",
        "metal_fraction_lower", "metal_fraction_default", "metal_fraction_upper",
        "water_fraction_lower", "water_fraction_default", "water_fraction_upper",
        "mass_default_kg", "metal_usable_default_kg", "water_usable_default_kg",
    ]
    output_cols = [c for c in preferred_cols if c in df.columns] + [c for c in df.columns if c not in preferred_cols]
    df[output_cols].to_csv(output_dir / "asteroid_resource_bounds.csv", index=False)

    summary = summarize_by_family(df)
    summary.to_csv(output_dir / "asteroid_resource_summary.csv", index=False)

    # Family masks
    fam_order = [
        "primitive_carbonaceous_like",
        "ordinary_stony_chondritic_like",
        "metal_rich_iron_meteorite_like",
        "other_or_unknown",
    ]
    fam_labels = {
        "primitive_carbonaceous_like": "Primitive",
        "ordinary_stony_chondritic_like": "Stony",
        "metal_rich_iron_meteorite_like": "Metal-rich",
        "other_or_unknown": "Other",
    }
    fam_colors = {
        "primitive_carbonaceous_like": "#66c2a5",
        "ordinary_stony_chondritic_like": "#fc8d62",
        "metal_rich_iron_meteorite_like": "#8da0cb",
        "other_or_unknown": "#bdbdbd",
    }

    family_codes = {fam: i for i, fam in enumerate(fam_order)}
    df["family_code"] = df["material_family"].map(lambda x: family_codes.get(x, family_codes["other_or_unknown"]))

    volumes = pd.to_numeric(df["volume_m3"], errors="coerce").fillna(0.0).tolist()
    family_code = df["family_code"].to_numpy().astype(int).tolist()
    n = len(volumes)

    # Bokeh: raw data source for JavaScript callback
    all_data_source = ColumnDataSource(data={"volume_m3": volumes, "family_code": family_code})

    # Bokeh: plot source (updated by callback)
    plot_source = ColumnDataSource(data={
        "x": [], "primitive": [], "stony": [], "metal": [], "other": [], "total": []
    })

    # Bokeh: widgets
    resource_select = Select(
        title="Resource mode",
        value="metal",
        options=[("metal", "Metal-rich feedstock"), ("water", "Water-equivalent")],
    )
    dens_primitive = BokehSlider(start=1500, end=2500, value=2000, step=50, title="Primitive density")
    dens_stony = BokehSlider(start=3000, end=3800, value=3400, step=50, title="Stony density")
    dens_metal = BokehSlider(start=7000, end=8000, value=7800, step=50, title="Metal-rich density")
    dens_other = BokehSlider(start=2500, end=3500, value=3000, step=50, title="Other density")
    metal_primitive = BokehSlider(start=0.00, end=0.05, value=0.025, step=0.001, title="Primitive metal frac")
    metal_stony = BokehSlider(start=0.05, end=0.20, value=0.12, step=0.001, title="Stony metal frac")
    metal_metal = BokehSlider(start=0.60, end=0.95, value=0.80, step=0.005, title="Metal-rich metal frac")
    metal_other = BokehSlider(start=0.00, end=0.20, value=0.10, step=0.001, title="Other metal frac")
    water_primitive = BokehSlider(start=0.00, end=0.15, value=0.08, step=0.001, title="Primitive water frac")
    water_stony = BokehSlider(start=0.00, end=0.02, value=0.005, step=0.0005, title="Stony water frac")
    water_metal = BokehSlider(start=0.00, end=0.005, value=0.001, step=0.0001, title="Metal-rich water frac")
    water_other = BokehSlider(start=0.00, end=0.05, value=0.01, step=0.001, title="Other water frac")

    summary_div = Div(width=800)

    callback = CustomJS(
        args=dict(
            raw=all_data_source,
            source=plot_source,
            summary_div=summary_div,
            resource_select=resource_select,
            dens_primitive=dens_primitive,
            dens_stony=dens_stony,
            dens_metal=dens_metal,
            dens_other=dens_other,
            metal_primitive=metal_primitive,
            metal_stony=metal_stony,
            metal_metal=metal_metal,
            metal_other=metal_other,
            water_primitive=water_primitive,
            water_stony=water_stony,
            water_metal=water_metal,
            water_other=water_other,
        ),
        code=f"""
const volume = raw.data['volume_m3'];
const family = raw.data['family_code'];

const resourceType = resource_select.value;

const densityByFamily = [
    dens_primitive.value,
    dens_stony.value,
    dens_metal.value,
    dens_other.value
];

let fractionByFamily;
if (resourceType === "metal") {{
    fractionByFamily = [
        metal_primitive.value,
        metal_stony.value,
        metal_metal.value,
        metal_other.value
    ];
}} else {{
    fractionByFamily = [
        water_primitive.value,
        water_stony.value,
        water_metal.value,
        water_other.value
    ];
}}

const n = volume.length;
const usable = new Array(n);
for (let i = 0; i < n; i++) {{
    const fam = family[i] ?? 3;
    usable[i] = volume[i] * densityByFamily[fam] * fractionByFamily[fam];
}}

const idx = Array.from({{length: n}}, (_, i) => i);
idx.sort((a, b) => usable[b] - usable[a]);

const maxPoints = {MAX_PLOT_POINTS};
const step = Math.max(1, Math.floor(n / maxPoints));

let runPrimitive = 0.0;
let runStony = 0.0;
let runMetal = 0.0;
let runOther = 0.0;

const x = [];
const primitive = [];
const stony = [];
const metal = [];
const other = [];
const total = [];

for (let rank = 0; rank < n; rank++) {{
    const i = idx[rank];
    const fam = family[i] ?? 3;
    const val = usable[i] || 0.0;

    if (fam === 0) runPrimitive += val;
    else if (fam === 1) runStony += val;
    else if (fam === 2) runMetal += val;
    else runOther += val;

    if (((rank + 1) % step === 0) || (rank === n - 1)) {{
        x.push(rank + 1);
        primitive.push(runPrimitive);
        stony.push(runStony);
        metal.push(runMetal);
        other.push(runOther);
        total.push(runPrimitive + runStony + runMetal + runOther);
    }}
}}

source.data = {{
    x: x,
    primitive: primitive,
    stony: stony,
    metal: metal,
    other: other,
    total: total
}};
source.change.emit();

const totalUsable = total.length > 0 ? total[total.length - 1] : 0.0;
const primitivePct = totalUsable > 0 ? 100.0 * runPrimitive / totalUsable : 0.0;
const stonyPct = totalUsable > 0 ? 100.0 * runStony / totalUsable : 0.0;
const metalPct = totalUsable > 0 ? 100.0 * runMetal / totalUsable : 0.0;
const otherPct = totalUsable > 0 ? 100.0 * runOther / totalUsable : 0.0;

summary_div.text = `
<div style="font-size:14px; line-height:1.5;">
  <b>Current mode:</b> ${{resourceType === "metal" ? "Metal-rich feedstock" : "Water-equivalent"}}<br>
  <b>Total usable kg:</b> ${{totalUsable.toExponential(4)}}<br>
  <b>Family breakdown:</b>
  Primitive ${{primitivePct.toFixed(2)}}% |
  Stony ${{stonyPct.toFixed(2)}}% |
  Metal-rich ${{metalPct.toFixed(2)}}% |
  Other ${{otherPct.toFixed(2)}}%
</div>
`;
"""
    )

    for w in [
        resource_select,
        dens_primitive, dens_stony, dens_metal, dens_other,
        metal_primitive, metal_stony, metal_metal, metal_other,
        water_primitive, water_stony, water_metal, water_other,
    ]:
        w.js_on_change("value", callback)
    resource_select.js_on_change("value", callback)

    colors = [fam_colors[f] for f in fam_order]
    p = figure(
        width=900,
        height=500,
        x_axis_label="Asteroid rank (sorted by usable kg)",
        y_axis_label="Cumulative usable kg",
        y_axis_type="log",
        tools="pan,wheel_zoom,box_zoom,reset,save",
        active_scroll="wheel_zoom",
    )
    p.varea_stack(
        stackers=["primitive", "stony", "metal", "other"],
        x="x",
        color=colors,
        legend_label=[fam_labels[f] for f in fam_order],
        source=plot_source,
        alpha=0.85,
    )
    p.line("x", "total", source=plot_source, line_width=3, color="black", legend_label="Total")
    p.legend.location = "top_left"
    p.legend.click_policy = "hide"

    controls = column(
        Div(text="<b>Resource selector</b>"),
        resource_select,
        Div(text="<b>Density sliders</b>"),
        dens_primitive, dens_stony, dens_metal, dens_other,
        Div(text="<b>Metal fraction sliders</b>"),
        metal_primitive, metal_stony, metal_metal, metal_other,
        Div(text="<b>Water fraction sliders</b>"),
        water_primitive, water_stony, water_metal, water_other,
        width=360,
    )

    callback.args["summary_div"] = summary_div
    callback.args["source"] = plot_source

    # Compute initial plot data (metal mode, default slider values)
    vol_arr = np.array(volumes)
    fam_arr = np.array(family_code)
    dens = np.array([2000, 3400, 7800, 3000])
    frac = np.array([0.025, 0.12, 0.80, 0.10])
    usable = vol_arr * dens[fam_arr] * frac[fam_arr]
    usable = np.nan_to_num(usable, nan=0.0, posinf=0.0, neginf=0.0)
    order = np.argsort(-usable)
    running = np.zeros(4)
    x_vals, prim, ston, met, oth, tot = [], [], [], [], [], []
    for rank, idx in enumerate(order, start=1):
        fam = min(int(fam_arr[idx]), 3)
        running[fam] += usable[idx]
        if (rank % max(1, n // MAX_PLOT_POINTS) == 0) or rank == n:
            x_vals.append(rank)
            prim.append(float(running[0]))
            ston.append(float(running[1]))
            met.append(float(running[2]))
            oth.append(float(running[3]))
            tot.append(float(running.sum()))
    plot_source.data = {
        "x": x_vals, "primitive": prim, "stony": ston,
        "metal": met, "other": oth, "total": tot,
    }
    total_sum = float(running.sum()) or 1.0
    summary_div.text = f"""
<div style="font-size:14px; line-height:1.5;">
  <b>Current mode:</b> Metal-rich feedstock<br>
  <b>Total usable kg:</b> {float(running.sum()):.4e}<br>
  <b>Family breakdown:</b>
  Primitive {100*running[0]/total_sum:.2f}% |
  Stony {100*running[1]/total_sum:.2f}% |
  Metal-rich {100*running[2]/total_sum:.2f}% |
  Other {100*running[3]/total_sum:.2f}%
</div>
"""

    layout = row(controls, column(summary_div, p), sizing_mode="scale_width")

    html_path = output_dir / "interactive_resource_slider_plot.html"
    output_file(html_path, title="Interactive Asteroid Resource Slider Plot")
    save(layout)

    print(f"Loaded input: {input_path}")
    print(f"Rows processed: {len(df):,}")
    print(f"Output directory: {output_dir.resolve()}")
    print("Wrote:")
    print("  - asteroid_resource_bounds.csv")
    print("  - asteroid_resource_summary.csv")
    print(f"  - {html_path.name}")
    print()
    print("Open the HTML file in a browser to use the interactive sliders.")
    print("The stacked areas are the family subcategories; the black line is the total cumulative usable kg.")


if __name__ == "__main__":
    main()