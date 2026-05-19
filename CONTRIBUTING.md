# Contributing to Asteroid Mining

Thank you for your interest in this project. This guide explains how to set up your environment, what belongs in Git, and how to propose changes so maintainers can review them efficiently.

For project background and pipeline details, see [README.md](README.md).

---

## Ways to contribute

You do not need to write code to help:

- **Report bugs** — Open a [GitHub issue](https://github.com/BhaskarJain003/Asteroid-Mining/issues) with steps to reproduce, expected vs actual behavior, and your Python version.
- **Suggest improvements** — Issues are welcome for new data sources, model tiers, plot types, or documentation gaps.
- **Fix bugs or add features** — Fork the repo, make changes on a branch, and open a pull request (PR).
- **Improve docs** — README, `cost_model_reference.md`, or comments in scripts that explain non-obvious science or API behavior.

If you are unsure whether an idea fits, open an issue first. That avoids large PRs that need to be reworked.

---

## Development setup

1. **Fork and clone** the repository:

   ```bash
   git clone https://github.com/YOUR_USERNAME/Asteroid-Mining.git
   cd Asteroid-Mining
   ```

2. **Use Python 3.13+** (see `.python-version`).

3. **Create a virtual environment** and install dependencies:

   ```bash
   uv venv
   uv pip install -r requirements.txt
   ```

   Or with standard `venv`:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate   # Windows
   # source .venv/bin/activate   # macOS / Linux
   pip install -r requirements.txt
   ```

4. **Generate local data** (not stored in Git):

   ```bash
   uv run python make_all.py
   ```

   If downloads fail or you only need to test a single script, see README sections *Quick start* and *Bootstrapping SBDB data*. You can skip heavy steps with:

   ```bash
   uv run python make_all.py --skip plots --skip-download
   ```

   when you already have the required CSVs on disk from a previous run.

---

## What to commit (and what not to)

This repo keeps **source code and documentation** in Git. **Generated outputs and large data** stay local.

| Commit | Do not commit |
|--------|----------------|
| `.py` scripts | `.venv/`, `venv/`, `env/` |
| `requirements.txt`, `pyproject.toml` | `__pycache__/`, `*.pyc` |
| `README.md`, `CONTRIBUTING.md`, `cost_model_reference.md` | `*.csv` (catalogs, merges, test exports) |
| `cost_model_audit.docx` (documentation) | `composition_plots/`, `pre_plotting/` (PNG outputs) |
| `.gitignore` | `cost_model_output/`, `resource_slider_output/` |
| | Archives (`*.zip`, `*.7z`), logs (`*.log`), large binaries |

If you add a new script that writes outputs, put files under an existing output directory or add a matching rule to `.gitignore` in the same PR.

**Never commit** secrets (API keys, tokens, `.env` files with credentials). Public data APIs used here (JPL SBDB, PDS, VizieR) do not require keys for normal use.

---

## Branch and pull request workflow

1. **Sync** your fork’s `main` with upstream `main`.
2. **Create a branch** from `main` with a short descriptive name:

   ```bash
   git checkout -b fix/merge-column-dtypes
   git checkout -b feature/tier2-dv-from-ephemeris
   ```

3. **Make focused changes** — One logical change per PR (e.g. one bug fix or one feature) is easier to review than a mixed batch.
4. **Test** the scripts you touched:
   - Run the relevant pipeline step, e.g. `uv run python enrich_taxonomy.py`, or
   - Run `uv run python make_all.py --only <step>` when your change affects the orchestrated flow.
5. **Commit** with clear messages:

   ```text
   Fix Lowell join when pdes is non-numeric

   Explain why the change was needed in the body if it is not obvious.
   ```

6. **Push** to your fork and open a PR against `BhaskarJain003/Asteroid-Mining` → `main`.
7. **Fill in the PR description**:
   - What changed and why
   - How you tested it (commands run, sample output paths)
   - Related issue number, if any (`Fixes #12`)

Maintainers may request edits before merging. Be responsive to review comments; it keeps the process smooth for everyone.

---

## Code guidelines

- **Match existing style** in the file you edit: naming, imports, `pathlib` usage, and argparse patterns.
- **Prefer small, readable functions** over large refactors unrelated to your PR.
- **Document non-obvious logic** briefly (taxonomy priority, unit conversions, API quirks). Do not comment obvious code.
- **Preserve pipeline contracts** — Scripts often read/write fixed filenames (`sbdb_lowell_merged.csv`, etc.). If you rename outputs, update `make_all.py` and README in the same PR.
- **Science and units** — Orbital and cost-model changes should stay consistent with `cost_model_reference.md`. Note assumptions in the PR if you change equations or defaults.

There is no enforced formatter yet; keep diffs clean and avoid drive-by reformatting of unrelated files.

---

## Running specific pipeline steps

`make_all.py` is the supported entry point. Useful flags:

```bash
uv run python make_all.py --only enrich          # single step
uv run python make_all.py --skip plots           # skip plot regeneration
uv run python make_all.py --keep-going           # continue after a failure
```

Individual scripts can be run directly when iterating (see each file’s module docstring for usage).

---

## Issues: good reports help us help you

Include when possible:

- OS and Python version (`python --version`)
- Exact command you ran
- Full error traceback
- Whether you used `--skip-download` or local CSVs
- Approximate size or source of input files (e.g. “fresh SBDB pull today”)

For **model or plot behavior**, describe slider settings or input columns and attach a screenshot of the HTML/PNG if relevant.

---

## Code of conduct

Be respectful and constructive in issues and PRs. Critique ideas and code, not people. Maintainers may close interactions that are hostile, spam, or off-topic.

---

## Questions?

- Open a [GitHub issue](https://github.com/BhaskarJain003/Asteroid-Mining/issues) for bugs and feature ideas.
- For security-sensitive findings, contact the repository owner privately rather than opening a public issue.

We appreciate your time and contributions.
