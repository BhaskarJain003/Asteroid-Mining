# Asteroid Mining

Python pipeline for near-Earth asteroid (NEA) catalog assembly, spectral taxonomy enrichment, resource-estimation visualizations, and a first-principles **cost-per-kg-to-LEO** financial model.

Data are pulled from public sources (JPL SBDB, Lowell Observatory, NASA PDS, VizieR, SDSS), merged into a single working catalog, and used to generate interactive Bokeh/Matplotlib dashboards and static plots. Large generated files (CSVs, PNGs, HTML) stay local and are excluded from Git — run the pipeline after cloning.

---

## Features

- **SBDB + Lowell merge** — Join JPL Small-Body Database NEA records with Lowell `astorb` physical/orbital columns without adding non-SBDB rows.
- **Multi-catalog taxonomy** — Cascade MITHNEOS → Neese (2010) → SDSS → Lowell IRAS into `best_tax_class` / `best_tax_source`.
- **Resource slider** — Family-based density and metal/water fraction bounds; interactive cumulative usable-mass plot.
- **Composition & EDA plots** — Bar charts, histograms, and scatter plots for orbital and physical parameters.
- **Tier-1 cost model** — Slider-driven $/kg to LEO from volume, density, extraction efficiency, Tsiolkovsky propellant, launch price, ops, and amortized development (see `cost_model_reference.md`).
- **Website export** — Optional `build_website_data.py` step publishes compact JSON for a separate site repo.

---

## Requirements

- **Python 3.13+** (see `.python-version`)
- Dependencies: `requests`, `pandas`, `numpy`, `matplotlib`, `bokeh`, `certifi` (`requirements.txt`)

Recommended: [uv](https://docs.astral.sh/uv/) for environments and `uv run`.

---

## Quick start

```bash
git clone https://github.com/BhaskarJain003/Asteroid-Mining.git
cd Asteroid-Mining

# Create environment and install deps
uv venv
uv pip install -r requirements.txt

# Run the full pipeline (downloads taxonomy, merges data, builds plots & HTML)
uv run python make_all.py
```

If you already have taxonomy CSVs and a merged catalog on disk:

```bash
uv run python make_all.py --skip-download
```

---

## Pipeline overview

`make_all.py` runs these steps in order:

| Step | Script | Output |
|------|--------|--------|
| `merge` | `merge_sbdb_lowell.py` | `sbdb_lowell_merged.csv` |
| `download` | `neese_mithneos_enrichment.py` | `neese_taxonomy.csv`, `mithneos_taxonomy.csv` |
| `mithneos` | `mithneos_vizier_pull.py` | Refreshed MITHNEOS NEA taxonomy |
| `enrich` | `enrich_taxonomy.py` | `sbdb_lowell_merged.csv` (+ `best_tax_*` columns) |
| `slider` | `resource_slider_bounds.py` | `resource_slider_output/*.html`, `*.csv` |
| `plots` | `composition_plots.py` | `composition_plots/*.png` |
| `pre` | `pre_plotting.py` | `pre_plotting/*.png` |
| `cost` | `cost_model.py` | `cost_model_output/cost_model_tier1.html` |
| `website` | `build_website_data.py` | `asteroid-data.json` (external path) |

### Common `make_all.py` options

```bash
uv run python make_all.py --skip plots          # taxonomy + merge only
uv run python make_all.py --only enrich         # single step
uv run python make_all.py --keep-going          # continue after a failed step
```

---

## Bootstrapping SBDB data

The merge step expects a cleaned SBDB export and Lowell astorb CSV (not in the repo):

1. **NEA catalog from JPL** — `SDD_API_test.py` queries the [SBDB Query API](https://ssd-api.jpl.nasa.gov/doc/sbdb_query.html) and writes `SDD_API_test.csv`.
2. **Clean** — `data_cleaning.py` produces `SDD_API_test_cleaned.csv`.
3. **Lowell** — `lowell_to_csv.py` builds `lowell_astorb.csv` from Lowell astorb sources.

Then run `merge_sbdb_lowell.py` or the full `make_all.py` pipeline.

---

## Taxonomy priority

When multiple catalogs classify the same numbered asteroid:

**MITHNEOS** (spectroscopy) → **Neese 2010** (multi-survey compilation) → **SDSS** (photometric) → **Lowell IRAS** (legacy fallback)

---

## Project layout

```
├── make_all.py                 # Pipeline driver
├── merge_sbdb_lowell.py        # SBDB ↔ Lowell join
├── enrich_taxonomy.py          # best_tax_class / best_tax_source
├── neese_mithneos_enrichment.py
├── mithneos_vizier_pull.py
├── mithneos_real_pull.py
├── sdss_taxonomy_crossmatch.py
├── resource_slider_bounds.py
├── composition_plots.py
├── pre_plotting.py
├── cost_model.py               # Tier-1 $/kg interactive model
├── cost_model_reference.md     # Equations & variable reference
├── cost_model_audit.docx       # Model audit notes
├── build_website_data.py       # JSON export for website
├── requirements.txt
└── pyproject.toml
```

Generated directories (gitignored): `composition_plots/`, `pre_plotting/`, `cost_model_output/`, `resource_slider_output/`, plus root `*.csv` files.

---

## Cost model

`cost_model.py` implements **Tier 1** from `cost_model_reference.md`: fleet-aggregate **$/kg of resource delivered to LEO**, with Bokeh sliders for density, resource fraction, extraction efficiency, Δv, Isp, launch price, ops rate, mission duration, development cost, and fleet size.

Open the interactive report after running the cost step:

```bash
uv run python cost_model.py
# open cost_model_output/cost_model_tier1.html in a browser
```

---

## Data sources

| Source | Use |
|--------|-----|
| [JPL SBDB Query API](https://ssd-api.jpl.nasa.gov/doc/sbdb_query.html) | NEA orbital & physical parameters |
| Lowell astorb | Supplemental diameters, colors, IRAS taxonomy |
| [NASA PDS SBN](https://sbn.psi.edu/) | Neese (2010) taxonomy (EAR-A-5-DDR-TAXONOMY-V6.0) |
| MITHNEOS / Marsset+ 2022 | High-confidence NEA spectroscopy |
| SDSS | Photometric taxonomy crossmatch |
| VizieR | Fallback catalog access |

---

## Contributing

Online collaborators are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, what to commit, and the pull request workflow.

---

## License

This project is open source under the [GNU General Public License v3.0](LICENSE) (GPL-3.0). You may use, modify, and distribute it under the terms of that license; derivative works must also be licensed under GPL-3.0.

---

## Author

[Bhaskar Jain](https://github.com/BhaskarJain003)
