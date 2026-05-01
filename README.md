# Australian Census Augmentation Tool

[![tests](https://github.com/cauldnz/abs-census-augmentor/actions/workflows/test.yml/badge.svg)](https://github.com/cauldnz/abs-census-augmentor/actions/workflows/test.yml)

Augment Australian location datasets with ABS Census data at the SA2 statistical area level. Use it as a CLI tool against CSV files, or as a Python library against a `pandas.DataFrame`.

```
Input → Geocoding (G-NAF tiered → Nominatim) → SA2 (MB fast path → spatial fallback) → Census Enrichment → Output
```

For each location row, the pipeline resolves coordinates (using your input lat/lon if present, else geocoding the address through G-NAF's three offline match tiers and falling back to Nominatim), looks up which SA2 the point falls in (via mesh-block lookup for G-NAF rows, point-in-polygon for the rest), and merges in your chosen Census variables. G-NAF Core, ASGS boundary files, Census DataPacks, and Nominatim responses all cache locally so re-runs are fast.

## Requirements

- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv) (recommended) or `pip` + `venv`

## Install

```bash
uv pip install -e ".[dev]"
```

## Two ways to use it

### As a library (notebooks / data-science pipelines)

```python
import pandas as pd
from census_augment import Pipeline

pipeline = Pipeline.create(
    variables={
        "median_age": "G02.Median_age_persons",
        "median_household_income_weekly": "G02.Median_tot_hhd_inc_weekly",
        "total_population": "G01.Tot_P_P",
    },
    user_agent="my-app/1.0 (me@example.com)",
    latitude_column="lat",
    longitude_column="lon",
)

df = pd.DataFrame({
    "label": ["Sydney Opera House", "Melbourne MCG", "Open ocean"],
    "lat":   [-33.8568,             -37.8200,        -35.0],
    "lon":   [151.2153,              144.9831,        155.0],
})

result = pipeline.augment(df)

result.df                        # original + geo + sa2 + enrichment columns
result.summary                   # counts: input/cache/fresh/failed/unmatched/...
result.is_fully_enriched         # bool Series, indexed like df
result.df[result.is_fully_enriched]   # filter to clean rows
```

`pipeline.augment(df)` returns an `AugmentResult` with the augmented DataFrame, a `RunSummary`, and three boolean Series (`is_fully_enriched`, `geocoding_failed`, `sa2_unmatched`) for filtering. See [`spec.md` §18](spec.md) for the full API.

> **First-run download:** the first call to `pipeline.augment(df)` (or `Pipeline.run()`, or any `census-augment` CLI command that touches the data) downloads ~50 MB of SA2 boundaries and ~40 MB of Census DataPacks into the user cache. Subsequent calls — including across notebooks, scripts, and CLI runs on the same machine — reuse the cache and are instant. See "Where data is cached" below.
>
> **G-NAF is not auto-downloaded.** The `config.example.yaml` defaults to `providers: [gnaf, nominatim]`, but you must populate the G-NAF cache yourself before running — see "[G-NAF setup](#g-naf-setup)" below. If you skip that, switch to `providers: [nominatim]` to use Nominatim only.

### As a CLI

```bash
# Augment a CSV end-to-end
census-augment run --config config.yaml

# Discover what variables the DataPack offers
census-augment discover --config config.yaml --search income
census-augment discover --config config.yaml --table G02

# Validate a config (with --full also checks variable refs against the DataPack)
census-augment validate --config config.yaml --full

# Pre-fetch ABS data (saves the first --run from doing the download)
census-augment fetch --config config.yaml --boundaries --census --gnaf

# Inspect the resolved G-NAF release / cache size
census-augment gnaf-info --config config.yaml
```

Run `census-augment --help` for the full list. See [`config.example.yaml`](config.example.yaml) for the full config schema.

## Examples

Runnable scripts and a sample CLI invocation are in [`examples/`](examples/):

- [`examples/library_basic.py`](examples/library_basic.py) — minimal library use.
- [`examples/library_with_overrides.py`](examples/library_with_overrides.py) — per-call column overrides, custom prefix, mask-based filtering.
- [`examples/cli/`](examples/cli/) — sample config + input CSV + walkthrough.

First run downloads ~90 MB of ABS data into the user cache; subsequent runs are instant.

## Where data is cached

By default both ABS downloads and the geocoding cache live in the platform user cache:

- Linux: `~/.cache/census-augment/`
- macOS: `~/Library/Caches/census-augment/`
- Windows: `%LOCALAPPDATA%\census-augment\Cache\`

Override with `CENSUS_AUGMENT_DATA_DIR` / `CENSUS_AUGMENT_CACHE_DIR` env vars, the CLI's `--data-dir` / `--cache-dir` flags, or `data_dir=` / `cache_dir=` kwargs in Python. See [`spec.md` §9](spec.md) for the full table.

## Documentation

- [`spec.md`](spec.md) — design specification; the source of truth.
- [`CLAUDE.md`](CLAUDE.md) — contributor and AI-agent conventions.
- [`tools/README.md`](tools/README.md) — how to verify parsers against real ABS endpoints (opt-in; not part of CI).
- [`examples/`](examples/) — runnable usage scripts.

## Development

```bash
pytest                            # 400+ hermetic tests; no real network
ruff check . && ruff format .     # Lint + format
mypy src/ tools/                  # Strict type check
```

The full suite is hermetic — every external interaction (Nominatim, ABS) is mocked. To validate against the live ABS endpoints, use the opt-in scripts in [`tools/`](tools/).

## Status

v1.0 implementation per [`spec.md` §16](spec.md). G-NAF integration, mesh-block fast path, tiered geocoding, and the v1.0 output schema are all in place. See [`CHANGELOG.md`](CHANGELOG.md) for the upgrade notes from v0.1 → v1.0.

## G-NAF setup

> **There is no automated G-NAF download.** This tool does not fetch the G-NAF GeoParquet from anywhere — *you must manually populate the cache yourself* before any G-NAF feature works. `census-augment fetch --gnaf` only validates the cache layout and downloads the (small) Mesh Block correspondence shapefile alongside; it does **not** download G-NAF itself. Automated S3 fetch is on the roadmap but deferred for v1.0 — see [`CHANGELOG.md`](CHANGELOG.md).

### What you need to do

1. **Pick a release.** Geoscape publishes G-NAF quarterly. Hugh Saalmans' [`gnaf-loader`](https://github.com/minus34/gnaf-loader) project mirrors a pre-built GeoParquet snapshot of each release at `s3://minus34.com/opendata/geoscape-{YYYYMM}/geoparquet/` (anonymous public-read access). For example, `geoscape-202602` is the February 2026 release.

2. **Find your data directory.** Run `census-augment gnaf-info --config config.yaml` — it prints the path the tool expects, e.g.:

   ```
   Path:           /home/you/.cache/census-augment/data/gnaf/{YYYYMM}/ (none yet)
   Hint: run `census-augment fetch --gnaf` (or populate
       /home/you/.cache/census-augment/data/gnaf/{YYYYMM}/ manually) to enable G-NAF.
   ```

   On Linux that resolves to `~/.cache/census-augment/data/gnaf/`; macOS uses `~/Library/Caches/census-augment/data/gnaf/`; Windows uses `%LOCALAPPDATA%\census-augment\Cache\data\gnaf\`. Override via `CENSUS_AUGMENT_DATA_DIR`.

3. **Download the Parquet files into a YYYYMM subdirectory of that path.** The directory name must be exactly the 6-digit release identifier — `202602`, not `2026-02` or `feb-2026`. With the AWS CLI:

   ```bash
   mkdir -p ~/.cache/census-augment/data/gnaf/202602
   aws s3 sync --no-sign-request \
       s3://minus34.com/opendata/geoscape-202602/geoparquet/ \
       ~/.cache/census-augment/data/gnaf/202602/
   ```

   The full snapshot is roughly 10 GB across ~50 Parquet files. If you don't have the AWS CLI, any S3 client that supports anonymous reads (e.g. `s5cmd`, `rclone`, or a small `boto3` script with `signature_version=UNSIGNED`) works. Browser-based downloads also work — `https://minus34.com.s3.amazonaws.com/opendata/geoscape-202602/geoparquet/` lists the files.

4. **Verify the cache.** Run `census-augment gnaf-info --config config.yaml` again. You should see:

   ```
   Resolved release: 202602
   Path:             /home/you/.cache/census-augment/data/gnaf/202602
   Cached size:      9847.3 MB (52 parquet file(s))
   ```

5. **Pin the release in your config** (recommended for reproducibility):

   ```yaml
   geocoding:
     gnaf:
       release: "202602"   # or 'latest' to pick up whichever release is in the cache
   ```

### Schema requirements

The Parquet must include these columns (the parser will raise loudly if they're missing):

- `ADDRESS_DETAIL_PID` — G-NAF's stable address identifier
- `ADDRESS_LABEL` — the pre-formatted "1 GEORGE STREET SYDNEY NSW 2000" string
- `LATITUDE`, `LONGITUDE`
- `MB_CODE` — 11-digit ABS Mesh Block (powers the §7.3 fast path)
- `POSTCODE` — required for the Tier 2/3 pre-filter

The `gnaf-loader` snapshot covers all of these. If you build your own Parquet from the official Geoscape PSV files (data.gov.au), make sure those columns are preserved.

### Opting out of G-NAF entirely

If you'd rather not deal with the cache, switch to Nominatim-only — that's the v0.1 behaviour:

```yaml
geocoding:
  providers: [nominatim]
  nominatim:
    user_agent: "..."
```

Nominatim is rate-limited (1 req/sec default), so this is much slower than G-NAF for any non-trivial input set, but it requires zero local data.

### G-NAF attribution

> Incorporates or developed using G-NAF © Geoscape Australia licensed by the Commonwealth of Australia under the Open Geo-coded National Address File (G-NAF) End User Licence Agreement.

The Open G-NAF EULA permits this kind of geocoding-and-enrichment use. It does *not* permit using the data to generate or compile addresses for sending mail unless each address has been verified against a secondary source.

## License

MIT — see [`LICENSE`](LICENSE). G-NAF data is licensed separately under Geoscape's [Open G-NAF EULA](https://geoscape.com.au/legal/g-naf-end-user-licence-agreement/).
