# Australian Census Augmentation Tool

[![tests](https://github.com/cauldnz/abs-census-augmentor/actions/workflows/test.yml/badge.svg)](https://github.com/cauldnz/abs-census-augmentor/actions/workflows/test.yml)

Augment Australian location datasets with ABS Census data at the SA2 statistical area level. Use it as a CLI tool against CSV files, or as a Python library against a `pandas.DataFrame`.

![demo](docs/demo.gif)

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
> **G-NAF has two modes — pick one that fits.** With `geocoding.gnaf.mode: cache` (default), the first run downloads ~10 GB of parquet locally for offline querying. With `geocoding.gnaf.mode: remote`, DuckDB streams directly from S3 via httpfs — no download, but each query is HTTPS-bound. See "[G-NAF setup](#g-naf-setup)" for the trade-offs. To skip G-NAF entirely set `providers: [nominatim]`.

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

`census-augment` ships two G-NAF distribution modes (set via `geocoding.gnaf.mode`); pick whichever matches your environment.

| Mode | What happens | Best for |
| --- | --- | --- |
| `cache` *(default)* | First call downloads the [gnaf-loader](https://github.com/minus34/gnaf-loader) snapshot (~10 GB across ~50 parquet files) from `s3://minus34.com/opendata/` to your user cache. Subsequent calls run entirely offline. | Production runs, large workloads, anywhere bandwidth is cheaper than disk-divided-by-time. |
| `remote` | DuckDB queries the same parquet files directly over HTTPS via its `httpfs` extension. **No download.** Each query pulls only the parquet metadata + the columns/rows it needs. | Prototyping, CI, disk-constrained environments, occasional one-off queries. |

To use remote mode:

```yaml
geocoding:
  providers: [gnaf, nominatim]
  gnaf:
    mode: remote
    release: latest        # or "202602"
```

That's it. No prefetch step. Open a notebook, run `Pipeline.augment(df)`, DuckDB does the rest.

**Trade-offs of remote mode:**

- *Speed.* Each query is HTTPS-bound — single Tier-1 lookup is ~100ms (parquet metadata fetch + ranged read). Tier 2/3 (postcode-bucket scans) read more bytes. Fine for thousands of addresses; not ideal for hundreds of thousands.
- *Bandwidth.* Cumulative reads can get pricey. A workload that does ~10k geocodes might pull ~500 MB across queries; if you'll re-run that workload many times, cache mode pays off.
- *Offline use.* Doesn't work without network. If your laptop's spotty, prefer cache.
- *No local schema validation up-front* — the `httpfs` extension itself has to be installable (DuckDB downloads it once on first use, then caches in `~/.duckdb/extensions/`).

**Bucket layout auto-detection.** Two layouts are recognised:

1. *gnaf-loader* (the production [gnaf-loader](https://github.com/minus34/gnaf-loader) bucket): G-NAF data lives in named subdirectories. The geocoder reads from `geoparquet/address_principal_census_{year}_boundaries/` — gnaf-loader's denormalised join of address principals with the ABS census boundary IDs. Source columns (`gnaf_pid`, `address`, `latitude`, `mb_{year}_code`, ...) are aliased to the uppercase schema the geocoder expects. Set `census.year` to pick `2016` vs `2021` boundaries (default `2021`).
2. *Legacy / bring-your-own*: a flat parquet at the release root with already-uppercase columns. Used by users who pre-build G-NAF from the official Geoscape PSV.

Detection runs on every `open_connection()`; gnaf-loader wins when both layouts coexist. For non-default layouts on self-hosted mirrors (MinIO, R2, ...), combine `data_sources.gnaf_s3_https_endpoint` with `data_sources.gnaf_parquet_filter` (regex against the relative key — only consulted under the legacy code path).

### One-shot prefetch (recommended for cache mode)

Pull the data ahead of your first run so it isn't on the critical path of your first augmentation:

```bash
census-augment fetch --config config.yaml --gnaf
```

This:

1. Anonymously lists `s3://minus34.com/opendata/geoscape-*/` to find the latest release (or honours `geocoding.gnaf.release: "202602"` if you've pinned one).
2. Downloads every `*.parquet` under `.../geoparquet/` to `<data_dir>/gnaf/{YYYYMM}/` with atomic-rename semantics — interrupted runs resume from the partial cache, no half-files left behind.
3. Fetches the small (~50 MB) Mesh Block correspondence shapefile alongside, since the `mb_code → SA2` fast path depends on it.

### Refreshing to a newer release

```bash
census-augment fetch --config config.yaml --gnaf --refresh
```

With `release: "latest"` (the default), `--refresh` re-checks S3 to pick up any newer quarterly that's dropped since you last fetched. With an explicit `release: "202602"`, `--refresh` re-downloads that same release.

### Inspecting the cache

```bash
census-augment gnaf-info --config config.yaml
```

Prints the resolved release, the on-disk path, and the cached size in MB.

### Pinning a specific release

For reproducibility (e.g. running the same pipeline against the same data at different times):

```yaml
geocoding:
  gnaf:
    release: "202602"   # default is "latest"
```

### Bringing your own G-NAF parquet

If your organisation builds G-NAF from the official Geoscape PSVs (data.gov.au) instead of using gnaf-loader, drop your own `*.parquet` files into `<data_dir>/gnaf/{YYYYMM}/` — the auto-download is skipped when the cache is already populated.

Two ways to lay out the file(s):

- **Match the gnaf-loader convention** (preferred): place the parquet at `<data_dir>/gnaf/{YYYYMM}/address_principal_census_{year}_boundaries/your-file.parquet` with lowercase columns (`gnaf_pid`, `address`, `latitude`, `longitude`, `postcode`, `mb_{year}_code`). The view aliases them to the uppercase schema for you.
- **Legacy flat layout**: place the parquet at `<data_dir>/gnaf/{YYYYMM}/your-file.parquet` with already-uppercase columns. Required columns: `ADDRESS_DETAIL_PID`, `ADDRESS_LABEL` (the pre-formatted "1 GEORGE STREET SYDNEY NSW 2000" string), `LATITUDE`, `LONGITUDE`, `MB_CODE` (11-digit ABS Mesh Block), `POSTCODE`. The parser raises loudly if any are missing.

### Opting out of G-NAF entirely

If you'd rather not deal with a 10 GB cache, switch to Nominatim-only:

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
