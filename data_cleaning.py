# data_cleaning.py

from __future__ import annotations

from pathlib import Path
import pandas as pd


INPUT_CANDIDATES = [
    "SDD_API_test.csv",   # user-specified name
    "SSD_API_test.csv",   # likely original export name
]

OUTPUT_FILE = "SDD_API_test_cleaned.csv"

# Columns that are primarily comet-specific / non-gravitational comet-model fields
COMET_SPECIFIC_COLUMNS = [
    "prefix",     # comet prefix like P/C/D/A
    "M1",         # comet total magnitude parameter
    "M2",         # comet total magnitude slope parameter
    "K1",         # comet nuclear magnitude parameter
    "K2",         # comet nuclear magnitude slope parameter
    "PC",         # comet phase coefficient
    "A1",         # non-gravitational radial parameter
    "A1_sigma",
    "A2",         # non-gravitational transverse parameter
    "A2_sigma",
    "A3",         # non-gravitational normal parameter
    "A3_sigma",
    "DT",         # perihelion-maximum offset in non-grav model
    "DT_sigma",
]


def find_input_file() -> Path:
    for candidate in INPUT_CANDIDATES:
        p = Path(candidate)
        if p.exists():
            return p
    raise FileNotFoundError(
        "Could not find any input CSV. Tried: "
        + ", ".join(INPUT_CANDIDATES)
    )


def main() -> None:
    input_path = find_input_file()
    df = pd.read_csv(input_path)

    original_cols = list(df.columns)
    cols_to_drop = [c for c in COMET_SPECIFIC_COLUMNS if c in df.columns]

    df_cleaned = df.drop(columns=cols_to_drop, errors="ignore")

    output_path = Path(OUTPUT_FILE)
    df_cleaned.to_csv(output_path, index=False)

    print(f"Loaded: {input_path}")
    print(f"Original shape: {df.shape}")
    print(f"Dropped {len(cols_to_drop)} comet-specific columns:")
    for c in cols_to_drop:
        print(f"  - {c}")
    print(f"Cleaned shape: {df_cleaned.shape}")
    print(f"Saved cleaned CSV to: {output_path.resolve()}")

    missing_requested = [c for c in COMET_SPECIFIC_COLUMNS if c not in original_cols]
    if missing_requested:
        print("\nThese comet-specific columns were not present in the input:")
        for c in missing_requested:
            print(f"  - {c}")


if __name__ == "__main__":
    main()