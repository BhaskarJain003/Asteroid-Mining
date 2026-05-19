# pre_plotting.py

from __future__ import annotations

from pathlib import Path
import math
import re

import matplotlib.pyplot as plt
import pandas as pd


INPUT_CANDIDATES = [
    # Prefer the enriched merged dataset so plots colored by spec_B/spec_T
    # reflect the best_tax_class assignments from enrich_taxonomy.py.
    "sbdb_lowell_merged.csv",
    "SDD_API_test_cleaned.csv",
    "SSD_API_test_cleaned.csv",
    "SDD_API_test.csv",
    "SSD_API_test.csv",
]

OUTPUT_DIR = Path("pre_plotting")
TOP_N_TAXONOMY = 12  # for boxplots, keep only the most common categories for readability


def find_input_file() -> Path:
    for candidate in INPUT_CANDIDATES:
        p = Path(candidate)
        if p.exists():
            return p
    raise FileNotFoundError(
        "Could not find an input CSV. Tried: " + ", ".join(INPUT_CANDIDATES)
    )


def sanitize_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_")


def save_current_plot(filename: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / filename, dpi=200, bbox_inches="tight")
    plt.close()


def as_numeric(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = df.copy()
    for col in columns:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def plot_missing_percent(df: pd.DataFrame) -> None:
    missing_pct = df.isna().mean().sort_values(ascending=False) * 100

    plt.figure(figsize=(max(12, len(missing_pct) * 0.22), 6))
    plt.bar(missing_pct.index, missing_pct.values)
    plt.xticks(rotation=90)
    plt.ylabel("Percent Missing")
    plt.title("Percent Missing by Variable")
    save_current_plot("missing_percent_by_variable.png")


def plot_histogram(df: pd.DataFrame, column: str, bins: int = 40, log_x: bool = False) -> None:
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
    save_current_plot(filename)


def plot_taxonomy_bar(df: pd.DataFrame, column: str) -> None:
    if column not in df.columns:
        print(f"Skipping taxonomy bar chart for missing column: {column}")
        return

    s = df[column].astype(str).str.strip()
    s = s.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
    counts = s.dropna().value_counts()

    if counts.empty:
        print(f"Skipping taxonomy bar chart for empty column: {column}")
        return

    plt.figure(figsize=(10, 5))
    plt.bar(counts.index.astype(str), counts.values)
    plt.xticks(rotation=60, ha="right")
    plt.xlabel(column)
    plt.ylabel("Count")
    plt.title(f"Taxonomy Class Counts: {column}")
    save_current_plot(f"taxonomy_counts_{sanitize_filename(column)}.png")


def plot_scatter(
    df: pd.DataFrame,
    x: str,
    y: str,
    filename: str,
    title: str,
    color_col: str | None = None,
) -> None:
    if x not in df.columns or y not in df.columns:
        print(f"Skipping scatter {x} vs {y}: missing column.")
        return

    plot_df = df.copy()
    plot_df[x] = pd.to_numeric(plot_df[x], errors="coerce")
    plot_df[y] = pd.to_numeric(plot_df[y], errors="coerce")
    plot_df = plot_df.dropna(subset=[x, y])

    if plot_df.empty:
        print(f"Skipping scatter {x} vs {y}: no valid rows.")
        return

    plt.figure(figsize=(8, 6))

    if color_col is not None and color_col in plot_df.columns:
        color_series = plot_df[color_col].astype(str).fillna("Unknown")
        top_classes = color_series.value_counts().head(8).index.tolist()

        for cls in top_classes:
            sub = plot_df[color_series == cls]
            if not sub.empty:
                plt.scatter(sub[x], sub[y], s=12, alpha=0.6, label=str(cls))

        other = plot_df[~color_series.isin(top_classes)]
        if not other.empty:
            plt.scatter(other[x], other[y], s=12, alpha=0.3, label="Other")

        plt.legend(fontsize=8)
    else:
        plt.scatter(plot_df[x], plot_df[y], s=12, alpha=0.6)

    plt.xlabel(x)
    plt.ylabel(y)
    plt.title(title)
    save_current_plot(filename)


def plot_boxplot_by_taxonomy(df: pd.DataFrame, value_col: str, taxonomy_col: str) -> None:
    if value_col not in df.columns or taxonomy_col not in df.columns:
        print(f"Skipping boxplot {value_col} by {taxonomy_col}: missing column.")
        return

    plot_df = df[[value_col, taxonomy_col]].copy()
    plot_df[value_col] = pd.to_numeric(plot_df[value_col], errors="coerce")
    plot_df[taxonomy_col] = plot_df[taxonomy_col].astype(str).str.strip()
    plot_df[taxonomy_col] = plot_df[taxonomy_col].replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
    plot_df = plot_df.dropna(subset=[value_col, taxonomy_col])

    if plot_df.empty:
        print(f"Skipping boxplot {value_col} by {taxonomy_col}: no valid rows.")
        return

    top_classes = plot_df[taxonomy_col].value_counts().head(TOP_N_TAXONOMY).index.tolist()
    plot_df = plot_df[plot_df[taxonomy_col].isin(top_classes)]

    if plot_df.empty:
        print(f"Skipping boxplot {value_col} by {taxonomy_col}: no top classes available.")
        return

    grouped = []
    labels = []
    for cls in top_classes:
        vals = plot_df.loc[plot_df[taxonomy_col] == cls, value_col].dropna()
        if len(vals) > 0:
            grouped.append(vals.values)
            labels.append(str(cls))

    if not grouped:
        print(f"Skipping boxplot {value_col} by {taxonomy_col}: no grouped data.")
        return

    plt.figure(figsize=(max(8, len(labels) * 0.8), 6))
    plt.boxplot(grouped, tick_labels=labels)
    plt.xticks(rotation=45, ha="right")
    plt.xlabel(taxonomy_col)
    plt.ylabel(value_col)
    plt.title(f"{value_col} by {taxonomy_col}")
    save_current_plot(f"boxplot_{sanitize_filename(value_col)}_by_{sanitize_filename(taxonomy_col)}.png")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    input_path = find_input_file()
    df = pd.read_csv(input_path)

    # If the enriched best_tax_class column is present (from
    # enrich_taxonomy.py), use it as the taxonomy source for plots.
    # We overlay it onto spec_B / spec_T so existing plot calls below pick
    # it up without per-call edits. spec_T is Tholen-style (uppercase first
    # letter only); spec_B keeps the full Bus-extended string.
    if "best_tax_class" in df.columns:
        bt = df["best_tax_class"].astype("object")
        df["spec_B"] = bt
        df["spec_T"] = bt.astype(str).str.extract(r"^([A-Z])", expand=False)

    numeric_cols = [
        "diameter", "albedo", "H", "rot_per", "moid", "q", "a", "e"
    ]
    df = as_numeric(df, numeric_cols)

    print(f"Loaded: {input_path}")
    print(f"Shape: {df.shape}")
    print(f"Saving plots to: {OUTPUT_DIR.resolve()}")

    # 1) Bar chart of percent missing per variable
    plot_missing_percent(df)

    # 2) Histograms
    plot_histogram(df, "diameter", bins=40)
    plot_histogram(df, "albedo", bins=40)
    plot_histogram(df, "H", bins=40)
    plot_histogram(df, "moid", bins=40)
    plot_histogram(df, "q", bins=40)

    # Optional extra useful log-scale histogram for diameter
    if "diameter" in df.columns:
        plot_histogram(df, "diameter", bins=40, log_x=True)

    # 3) Taxonomy count bar charts
    plot_taxonomy_bar(df, "spec_B")
    plot_taxonomy_bar(df, "spec_T")

    # 4) Scatter plots
    plot_scatter(
        df,
        x="H",
        y="diameter",
        filename="scatter_H_vs_diameter.png",
        title="H vs Diameter",
    )

    plot_scatter(
        df,
        x="diameter",
        y="albedo",
        filename="scatter_diameter_vs_albedo.png",
        title="Diameter vs Albedo",
    )

    plot_scatter(
        df,
        x="H",
        y="albedo",
        filename="scatter_H_vs_albedo.png",
        title="H vs Albedo",
    )

    plot_scatter(
        df,
        x="rot_per",
        y="diameter",
        filename="scatter_rot_per_vs_diameter.png",
        title="Rotation Period vs Diameter",
    )

    plot_scatter(
        df,
        x="a",
        y="e",
        filename="scatter_a_vs_e_colored_by_class.png",
        title="Semimajor Axis vs Eccentricity (colored by class)",
        color_col="class",
    )

    # 5) Boxplots by taxonomy
    for taxonomy_col in ["spec_B", "spec_T"]:
        plot_boxplot_by_taxonomy(df, value_col="albedo", taxonomy_col=taxonomy_col)
        plot_boxplot_by_taxonomy(df, value_col="diameter", taxonomy_col=taxonomy_col)
        plot_boxplot_by_taxonomy(df, value_col="H", taxonomy_col=taxonomy_col)

    print("Done.")


if __name__ == "__main__":
    main()