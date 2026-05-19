# composition_plots.py
#
# Reads sbdb_lowell_merged.csv and creates composition-related plots in a folder
# called composition_plots.
#
# Usage:
#   python composition_plots.py
#
# Optional:
#   python composition_plots.py --input sbdb_lowell_merged.csv --output-dir composition_plots

from __future__ import annotations

import argparse
from pathlib import Path
import re

import matplotlib.pyplot as plt
import pandas as pd


DEFAULT_INPUT_CANDIDATES = [
    "sbdb_lowell_merged.csv",
    "SDD_API_test_cleaned.csv",
    "SSD_API_test_cleaned.csv",
]
DEFAULT_OUTPUT_DIR = "composition_plots"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create composition-related asteroid plots.")
    parser.add_argument("--input", type=str, default=None, help="Path to merged CSV")
    parser.add_argument("--output-dir", type=str, default=DEFAULT_OUTPUT_DIR, help="Output plot folder")
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


def sanitize_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_")


def save_plot(output_dir: Path, filename: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(output_dir / filename, dpi=220, bbox_inches="tight")
    plt.close()


def clean_taxonomy_series(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip()
    s = s.replace(
        {
            "": pd.NA,
            "nan": pd.NA,
            "None": pd.NA,
            "NONE": pd.NA,
            "NULL": pd.NA,
        }
    )
    return s


def make_numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    out = df.copy()
    for col in cols:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def add_preferred_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    if "diameter" in out.columns:
        out["diameter"] = pd.to_numeric(out["diameter"], errors="coerce")
    if "lowell_iras_diameter_km" in out.columns:
        out["lowell_iras_diameter_km"] = pd.to_numeric(out["lowell_iras_diameter_km"], errors="coerce")

    if "H" in out.columns:
        out["H"] = pd.to_numeric(out["H"], errors="coerce")
    if "lowell_H" in out.columns:
        out["lowell_H"] = pd.to_numeric(out["lowell_H"], errors="coerce")

    if "diameter" in out.columns and "lowell_iras_diameter_km" in out.columns:
        out["preferred_diameter"] = out["diameter"].combine_first(out["lowell_iras_diameter_km"])
    elif "diameter" in out.columns:
        out["preferred_diameter"] = out["diameter"]
    elif "lowell_iras_diameter_km" in out.columns:
        out["preferred_diameter"] = out["lowell_iras_diameter_km"]
    else:
        out["preferred_diameter"] = pd.NA

    if "H" in out.columns and "lowell_H" in out.columns:
        out["preferred_H"] = out["H"].combine_first(out["lowell_H"])
    elif "H" in out.columns:
        out["preferred_H"] = out["H"]
    elif "lowell_H" in out.columns:
        out["preferred_H"] = out["lowell_H"]
    else:
        out["preferred_H"] = pd.NA

    return out


def plot_histogram(
    df: pd.DataFrame,
    column: str,
    output_dir: Path,
    bins: int = 40,
    log_x: bool = False,
) -> None:
    if column not in df.columns:
        print(f"Skipping histogram for missing column: {column}")
        return

    s = pd.to_numeric(df[column], errors="coerce").dropna()
    if s.empty:
        print(f"Skipping histogram for empty column: {column}")
        return

    if log_x:
        s = s[s > 0]
        if s.empty:
            print(f"Skipping log histogram for {column}: no positive values.")
            return

    plt.figure(figsize=(8, 5))
    plt.hist(s, bins=bins)
    if log_x:
        plt.xscale("log")
        title = f"Histogram of {column} (log x-scale)"
        filename = f"hist_{sanitize_filename(column)}_logx.png"
    else:
        title = f"Histogram of {column}"
        filename = f"hist_{sanitize_filename(column)}.png"

    plt.xlabel(column)
    plt.ylabel("Count")
    plt.title(title)
    save_plot(output_dir, filename)


def plot_category_bar(df: pd.DataFrame, column: str, output_dir: Path, top_n: int | None = None) -> None:
    if column not in df.columns:
        print(f"Skipping bar chart for missing column: {column}")
        return

    s = clean_taxonomy_series(df[column])
    counts = s.dropna().value_counts()

    if counts.empty:
        print(f"Skipping bar chart for empty column: {column}")
        return

    if top_n is not None:
        counts = counts.head(top_n)

    plt.figure(figsize=(10, 5))
    plt.bar(counts.index.astype(str), counts.values)
    plt.xticks(rotation=45, ha="right")
    plt.xlabel(column)
    plt.ylabel("Count")
    plt.title(f"Bar Chart of {column}")
    save_plot(output_dir, f"bar_{sanitize_filename(column)}.png")


def plot_taxonomy_pie(df: pd.DataFrame, column: str, output_dir: Path) -> None:
    if column not in df.columns:
        print(f"Skipping pie chart for missing column: {column}")
        return

    s = clean_taxonomy_series(df[column])
    present_count = int(s.notna().sum())
    missing_count = int(s.isna().sum())

    plt.figure(figsize=(6, 6))
    plt.pie(
        [present_count, missing_count],
        labels=[f"{column} present", f"{column} missing"],
        autopct="%1.1f%%",
        startangle=90,
    )
    plt.title(f"Coverage of {column}")
    save_plot(output_dir, f"pie_coverage_{sanitize_filename(column)}.png")


def plot_scatter(
    df: pd.DataFrame,
    x: str,
    y: str,
    output_dir: Path,
    title: str,
    filename: str,
    color_col: str | None = None,
    max_legend_classes: int = 8,
    log_x: bool = False,
    log_y: bool = False,
) -> None:
    if x not in df.columns or y not in df.columns:
        print(f"Skipping scatter {x} vs {y}: missing column.")
        return

    plot_df = df.copy()
    plot_df[x] = pd.to_numeric(plot_df[x], errors="coerce")
    plot_df[y] = pd.to_numeric(plot_df[y], errors="coerce")
    plot_df = plot_df.dropna(subset=[x, y])

    if log_x:
        plot_df = plot_df[plot_df[x] > 0]
    if log_y:
        plot_df = plot_df[plot_df[y] > 0]

    if plot_df.empty:
        print(f"Skipping scatter {x} vs {y}: no valid rows.")
        return

    plt.figure(figsize=(8, 6))

    if color_col is not None and color_col in plot_df.columns:
        c = clean_taxonomy_series(plot_df[color_col])
        counts = c.dropna().value_counts()
        top_classes = counts.head(max_legend_classes).index.tolist()

        for cls in top_classes:
            sub = plot_df[c == cls]
            if not sub.empty:
                plt.scatter(sub[x], sub[y], s=12, alpha=0.60, label=str(cls))

        other = plot_df[~c.isin(top_classes) | c.isna()]
        if not other.empty:
            plt.scatter(other[x], other[y], s=12, alpha=0.25, label="Other/Unknown")

        plt.legend(fontsize=8)
    else:
        plt.scatter(plot_df[x], plot_df[y], s=12, alpha=0.50)

    if log_x:
        plt.xscale("log")
    if log_y:
        plt.yscale("log")

    plt.xlabel(x)
    plt.ylabel(y)
    plt.title(title)
    save_plot(output_dir, filename)


def main() -> None:
    args = parse_args()
    input_path = find_input_file(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(input_path, low_memory=False)

    # If the enriched best_tax_class column is present (from enrich_taxonomy.py)
    # use it as the taxonomy source for every plot below. We do this by
    # overwriting lowell_iras_tax_class so the existing plot calls pick it up
    # without needing per-call edits.
    if "best_tax_class" in df.columns:
        df["lowell_iras_tax_class"] = df["best_tax_class"]

    numeric_cols = [
        "albedo",
        "lowell_B_V",
        "preferred_diameter",
        "preferred_H",
        "moid",
        "a",
        "e",
        "q",
        "diameter",
        "lowell_iras_diameter_km",
        "H",
        "lowell_H",
    ]
    df = add_preferred_columns(df)
    df = make_numeric(df, numeric_cols)

    print(f"Loaded: {input_path}")
    print(f"Rows: {len(df):,}")
    print(f"Saving plots to: {output_dir.resolve()}")

    # -------------------------
    # Requested histograms
    # -------------------------
    plot_histogram(df, "albedo", output_dir, bins=40)
    plot_histogram(df, "lowell_B_V", output_dir, bins=40)
    plot_histogram(df, "preferred_diameter", output_dir, bins=40)
    plot_histogram(df, "preferred_diameter", output_dir, bins=40, log_x=True)
    plot_histogram(df, "preferred_H", output_dir, bins=40)
    plot_histogram(df, "moid", output_dir, bins=40)
    plot_histogram(df, "moid", output_dir, bins=40, log_x=True)

    # lowell_iras_tax_class requested as a "histogram" - categorical, so use bar-style counts
    plot_category_bar(df, "lowell_iras_tax_class", output_dir)

    # -------------------------
    # Requested bar chart
    # -------------------------
    plot_category_bar(df, "class", output_dir)

    # -------------------------
    # Requested pie chart
    # -------------------------
    plot_taxonomy_pie(df, "lowell_iras_tax_class", output_dir)

    # -------------------------
    # Interesting scatters
    # -------------------------
    # 1. Composition classic
    plot_scatter(
        df,
        x="preferred_diameter",
        y="albedo",
        output_dir=output_dir,
        title="Preferred Diameter vs Albedo",
        filename="scatter_preferred_diameter_vs_albedo.png",
        color_col="lowell_iras_tax_class",
        log_x=True,
    )

    # 2. Brightness-size relation
    plot_scatter(
        df,
        x="preferred_H",
        y="preferred_diameter",
        output_dir=output_dir,
        title="Preferred H vs Preferred Diameter",
        filename="scatter_preferred_H_vs_preferred_diameter.png",
        color_col="lowell_iras_tax_class",
        log_y=True,
    )

    # 3. Color vs reflectivity
    plot_scatter(
        df,
        x="lowell_B_V",
        y="albedo",
        output_dir=output_dir,
        title="Lowell B-V vs Albedo",
        filename="scatter_lowell_B_V_vs_albedo.png",
        color_col="lowell_iras_tax_class",
    )

    # 4. Orbital size vs reflectivity
    plot_scatter(
        df,
        x="a",
        y="albedo",
        output_dir=output_dir,
        title="Semimajor Axis (a) vs Albedo",
        filename="scatter_a_vs_albedo.png",
        color_col="class",
    )

    # 5. Eccentricity vs albedo
    plot_scatter(
        df,
        x="e",
        y="albedo",
        output_dir=output_dir,
        title="Eccentricity (e) vs Albedo",
        filename="scatter_e_vs_albedo.png",
        color_col="class",
    )

    # 6. q vs albedo
    plot_scatter(
        df,
        x="q",
        y="albedo",
        output_dir=output_dir,
        title="Perihelion Distance (q) vs Albedo",
        filename="scatter_q_vs_albedo.png",
        color_col="class",
    )

    # 7. MOID vs diameter
    plot_scatter(
        df,
        x="moid",
        y="preferred_diameter",
        output_dir=output_dir,
        title="MOID vs Preferred Diameter",
        filename="scatter_moid_vs_preferred_diameter.png",
        color_col="lowell_iras_tax_class",
        log_x=True,
        log_y=True,
    )

    # 8. a vs e colored by class
    plot_scatter(
        df,
        x="a",
        y="e",
        output_dir=output_dir,
        title="Semimajor Axis (a) vs Eccentricity (e)",
        filename="scatter_a_vs_e_colored_by_class.png",
        color_col="class",
    )

    # 9. H vs albedo
    plot_scatter(
        df,
        x="preferred_H",
        y="albedo",
        output_dir=output_dir,
        title="Preferred H vs Albedo",
        filename="scatter_preferred_H_vs_albedo.png",
        color_col="lowell_iras_tax_class",
    )

    # 10. B-V vs diameter
    plot_scatter(
        df,
        x="lowell_B_V",
        y="preferred_diameter",
        output_dir=output_dir,
        title="Lowell B-V vs Preferred Diameter",
        filename="scatter_lowell_B_V_vs_preferred_diameter.png",
        color_col="lowell_iras_tax_class",
        log_y=True,
    )

    print("Done.")


if __name__ == "__main__":
    main()