# Australian Census Augmentation Tool — Specification

> **Status:** Draft v0.8
> **Purpose:** Hand-off specification for implementation by Claude Code. Update this document as design decisions evolve.

---

## 1. Purpose

A Python tool that takes a dataset of locations in Australia (as addresses, coordinates, or a mix) and augments each record with selected variables from the Australian Bureau of Statistics (ABS) Census of Population and Housing at the SA2 statistical area level.

The output is a CSV with the original records plus appended columns drawn from the census, suitable for downstream analysis or merging with other datasets.

---

## 2. Scope

### v1 — in scope
- Input: CSV containing addresses and/or `(lat, lon)` coordinates (or a mix per row).
- Geocoding via Nominatim (public OpenStreetMap API).
- SA2-level statistical area assignment via point-in-polygon.
- 2021 Census DataPack — General Community Profile (GCP).
- Output: enriched CSV.
- Configuration-driven variable selection using human-readable names.
- Local caching of geocoded addresses.
- Runtime download of ABS data (boundaries + DataPacks); nothing checked into git.
- A `discover` command to help users find census variables by keyword.

### Future / out of scope for v1
- G-NAF-based geocoding (pluggable interface should support this later).
- Paid geocoding providers (Google, Mapbox).
- SA1 and SA3 levels (architecture should not preclude them).
- Other DataPack profiles (Indigenous, Working Population, Time Series).
- 2026 Census data when released (architecture should not preclude it).
- Computed/derived variables (ratios, percentages combining multiple columns) — these are an explicit downstream concern of the data science feature engineering pipeline that consumes this tool's output, not a responsibility of this tool.
- Output formats other than CSV (Parquet, GeoPackage).
- Explicit input deduplication. Duplicate input rows are processed independently; efficiency on duplicate addresses comes from the geocoding cache.

### Usage assumptions
- **Target scale:** typically a few hundred rows per run. Nominatim's 1 req/sec policy is acceptable at this scale. Larger workloads are deferred to a future pluggable geocoder (see §13).

---

## 3. Architecture Overview

A linear pipeline:

```
Input CSV  →  Geocoding  →  Spatial Join  →  Census Enrichment  →  Output CSV
              (Nominatim)   (SA2 polygons)   (DataPack lookup)
                  ↓                                  ↑
            geocoding cache              metadata-driven config
```

Each stage is independently testable. Cached artifacts (geocoded addresses, downloaded boundaries and DataPacks) make re-runs cheap.

---

## 4. Data Sources

### 4.1 ASGS SA2 Boundaries
- **Source:** ABS Australian Statistical Geography Standard (ASGS) Edition 3 (covers Jul 2021 – Jun 2026).
- **Format:** Shapefile (`.shp` + `.dbf` / `.prj` / `.shx` sidecars). Per-level GeoPackage is not offered by ABS at SA-level granularity; only a 505 MB bundled "main structure" GeoPackage exists, which is overkill for v1's SA2-only scope.
- **CRS:** GDA2020 (EPSG:7844). Reproject input points as needed.
- **Approximate size:** ~50 MB.
- **Base URL (configurable):** `https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/digital-boundary-files`
- **Filename pattern:** `{level}_{year}_AUST_SHP_{datum}.zip`, e.g. `SA2_2021_AUST_SHP_GDA2020.zip`. Note the `SHP` token sits between `AUST_` and the datum on the **ZIP** filename — the files **inside** the ZIP do not have it (they are named `SA2_2021_AUST_GDA2020.shp` etc.).
- The tool downloads this on first use into `data/boundaries/` and caches it.

### 4.2 Census DataPacks (2021 GCP)
- **Source:** ABS Census 2021 DataPacks page.
- **Selection (defaults):** General Community Profile (`GCP`), SA2 level, all of Australia (`AUS`), short-header descriptor.
- **Format:** ZIP archive containing one CSV per table (`G01`, `G02`, …) plus an Excel metadata file mapping cryptic column codes (e.g. `Tot_P_M`) to human-readable descriptions ("Total Persons Male").
- **Base URL (configurable):** `https://www.abs.gov.au/census/find-census-data/datapacks/download`
- **Filename pattern:** `{year}_{profile}_{level}_for_{region}_{descriptor}.zip`, e.g. `2021_GCP_SA2_for_AUS_short-header.zip`.
- The tool constructs the full filename deterministically from config values, so users can override the *base* URL without having to specify each individual file.
- The tool downloads, extracts, and indexes this on first use into `data/census/`.

**Real DataPack layout (verified against 2021 GCP):**
- CSVs live in a long-named subdirectory (e.g. `2021 Census GCP Statistical Area 2 for AUS/`) with names like `2021Census_G01_AUST_SA2.csv`. Discovery is by `rglob` and table-ID extraction, not fixed paths.
- The `Metadata/` directory contains *three* `.xlsx` files; only `Metadata_*GCP*DataPack*.xlsx` (case-insensitive) is the descriptor we want. The others (`*geog_desc*.xlsx`, `*Sequential_Template*.xlsx`) are unrelated and ignored.
- The descriptor sheet `Cell Descriptors Information` has ~10 rows of title/blank padding above the actual header row. The parser auto-detects the header row by scanning for a row containing `Short`, `Long`, and `DataPackfile`.
- Six descriptor-sheet columns: `Sequential`, `Short`, `Long`, `DataPackfile`, `Profiletable`, `Columnheadingdescriptioninprofile`. The last is the user-readable description (e.g. "Median total household income ($/weekly)") and is what we expose via `discover`.
- The `Table Number, Name, Population` sheet provides table-level names (e.g. `G02 → "Selected Medians and Averages"`). Note: some headers have trailing whitespace and must be stripped.

> **Implementation note:** Both base URLs are exposed as `data_sources.boundaries_base_url` and `data_sources.datapacks_base_url` in config (see §6). Defaults ship with the spec and are validated to be reachable on first run; users can override either if ABS restructures their site.

---

## 5. Project Structure

```
census-augment/
├── README.md                      # User-facing intro (CLI + library)
├── CLAUDE.md                      # Contributor / agent guidance
├── pyproject.toml
├── config.example.yaml            # Sample config for the CLI
├── LICENSE
├── .gitignore
├── examples/                      # Runnable usage scripts (CLI + library)
├── data/                          # Optional project-local cache; gitignored
│   ├── README.md                  # Defaults to platform user cache (§9)
│   └── .gitignore                 # Ignores all but README.md
├── cache/                         # Optional project-local geocoding cache
│   ├── README.md
│   └── .gitignore
├── src/
│   └── census_augment/
│       ├── __init__.py            # Public API exports (spec §18.4)
│       ├── py.typed               # Inline type marker for downstream users
│       ├── cli.py                 # Typer entry point
│       ├── config.py              # Pydantic schema + YAML loader
│       ├── paths.py               # User-cache directory resolution (§9)
│       ├── catalog.py             # Variable resolution + search + suggestions
│       ├── spatial.py             # Point-in-polygon → SA2
│       ├── enrich.py              # SA2 + variables → enriched DataFrame
│       ├── pipeline.py            # Orchestration; Pipeline.run + Pipeline.augment
│       ├── data_sources/
│       │   ├── _base.py           # Shared download/extract base (boundaries + datapacks)
│       │   ├── boundaries.py      # Shapefile download + load
│       │   └── datapacks.py       # CSV + Excel-metadata parser
│       └── geocoding/
│           ├── base.py            # Geocoder Protocol + GeocodeResult dataclass
│           ├── cache.py           # Hash-keyed JSON cache (sharded)
│           └── nominatim.py       # Nominatim impl with rate-limit + back-off
├── tools/                         # Real-data verification (see §17)
│   ├── README.md
│   ├── fetch_real_data.py
│   └── verify_real_parsers.py
└── tests/                         # Hermetic test suite (no real network)
    ├── conftest.py                # Shared fixtures (synthetic SA2 + DataPack)
    └── test_*.py
```

### `.gitignore` pattern for data and cache folders

Each runtime folder gets its own `.gitignore` so the folder structure is committed but contents are not:

```
# data/.gitignore (and cache/.gitignore)
*
!.gitignore
!README.md
```

The top-level `.gitignore` should additionally cover `out/`, `*.pyc`, `__pycache__/`, virtualenv folders, etc.

---

## 6. Configuration

**Format:** YAML. **Validation:** Pydantic models. Fail fast and loudly on schema or reference errors.

### 6.1 `config.example.yaml`

```yaml
# Census augmentation configuration

input:
  path: data/locations.csv
  address_column: address          # Optional. If present, used for geocoding.
  latitude_column: lat             # Optional. If both lat & lon present, used directly.
  longitude_column: lon            # Both required together if used.

output:
  path: out/locations_enriched.csv
  prefix: sa2_                     # Prefix for all census-derived columns

census:
  year: 2021                       # Reserved for future use (2026 Census)
  level: SA2                       # v1: SA2 only
  profile: GCP                     # General Community Profile
  region: AUS                      # AUS or state code (NSW, VIC, QLD, ...)
  descriptor: short-header         # short-header | sequential | long-header
  asgs_edition: 3                  # ASGS edition for boundaries (3 covers Jul 2021 – Jun 2026)
  datum: GDA2020                   # GDA2020 or GDA94 for boundary files

# Base URLs for ABS data downloads. Override only if ABS restructures their site.
# Full filenames are constructed deterministically from the census.* values above.
data_sources:
  boundaries_base_url: https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/digital-boundary-files
  datapacks_base_url: https://www.abs.gov.au/census/find-census-data/datapacks/download

geocoding:
  provider: nominatim
  user_agent: "census-augment/0.1 (you@example.com)"   # Required by Nominatim policy
  rate_limit_per_second: 1
  cache_enabled: true

# Variables to attach to each record.
# Format: <friendly_name>: <table_id>.<column_name>
# Output column will be: <output.prefix><friendly_name>
# Friendly names must be valid snake_case identifiers.
variables:
  median_age: G02.Median_age_persons
  median_household_income_weekly: G02.Median_tot_hhd_inc_weekly
  median_rent_weekly: G02.Median_rent_weekly
  total_population: G01.Tot_P_P
  born_overseas_count: G01.Birthplace_Elsewhere_P
```

**Input column validation:** at least one of `input.address_column` OR both `input.latitude_column` and `input.longitude_column` must be set; the lat/lon pair must be set together (setting one without the other is a config error).

**Optional path fields:** `input.path` and `output.path` are required only by the CLI's `run` command (which reads/writes CSVs). Library users calling `Pipeline.augment(df)` (see §18) can omit them — the column-name fields under `input` still describe the DataFrame regardless of where it came from.

### 6.2 Variable resolution

- Each entry under `variables` maps a friendly name to a `<table>.<column>` reference into the DataPack.
- At config-load time, the tool validates every reference against the loaded DataPack metadata. Unknown table or column → fail with a helpful message that suggests near-matches.
- The `<column>` portion is interpreted in the configured descriptor mode (`census.descriptor`):
  - `short-header` (default): codes are short tokens (e.g. `Tot_P_M`).
  - `long-header`: codes are underscored long forms (e.g. `Total_Persons_Males`).
  - `sequential`: codes are numbered sequential identifiers (e.g. `G1`, `G108`).

  The metadata column matched against the reference depends on this setting; the human-readable description shown by `discover` always comes from the `Columnheadingdescriptioninprofile` field of the descriptor sheet.
- Friendly names must match `^[a-z][a-z0-9_]*$`. The output column is `{prefix}{friendly_name}`.
- A `discover` CLI command lets users search metadata to find the right `table.column` references:
  ```
  census-augment discover --search "income"
  census-augment discover --table G02
  ```

---

## 7. Pipeline Stages

### 7.1 Input parsing
- Load the CSV with pandas.
- Validate that referenced columns exist.
- Determine per-row source: `coordinates` (if both lat/lon present and non-null) takes precedence over `address`.

### 7.2 Geocoding
- For each address-only row, check the geocoding cache first.
- Cache key: SHA-256 hash of the normalized address (lowercase, whitespace-collapsed, trailing punctuation stripped).
- Cache layout: `cache/geocoding/{hash[:2]}/{hash}.json` (sharded so directories stay small).
- Cache value:
  ```json
  {
    "address_input": "...",
    "address_normalized": "...",
    "lat": -33.8688,
    "lon": 151.2093,
    "provider": "nominatim",
    "timestamp": "2026-04-30T...",
    "raw_response": { ... }
  }
  ```
- Respect Nominatim's rate limit (1 req/sec) and User-Agent policy.
- If Nominatim returns HTTP 429 (Too Many Requests) or 503 (rate-limited), back off exponentially up to 3 retries before treating the lookup as failed.
- Failed lookups: record retains `null` coordinates and is flagged in the run summary; pipeline continues. Failures are not cached — they are retried on the next run.
- **Duplicate addresses are not explicitly deduplicated** before geocoding. Within a single run, a duplicate address will hit the cache as soon as the first occurrence is processed and written. This keeps row-level processing predictable and avoids reordering output, at the cost of one extra cache lookup per duplicate (negligible).

### 7.3 Spatial join
- Load SA2 GeoPackage into a GeoDataFrame.
- Build a spatial index (`sindex`).
- Input lat/lon are interpreted as EPSG:4326 (WGS84) — the de facto standard for consumer GPS, web mapping, and Nominatim output. Points are reprojected to the boundary CRS (GDA2020 / EPSG:7844) before the join.
- Point-in-polygon for each record. Records outside any SA2 get `null` `sa2_code` and `sa2_name`.

### 7.4 Census enrichment
- Determine the unique set of `(table)` references across all configured variables.
- Load only those tables from the DataPack.
- Build a single lookup keyed by SA2 code.
- Join enriched columns onto the dataset.

### 7.5 Output
- Write CSV with all original columns preserved, in original order, followed by appended columns (see §8).
- Print a run summary: total rows, geocoded (cache hit / fresh), unmatched SA2, fully enriched, errors.

---

## 8. Output Schema

Original input columns are preserved unchanged. The following columns are appended in this order:

| Column | Description |
|---|---|
| `geo_lat` | Resolved latitude (from input or geocoding). |
| `geo_lon` | Resolved longitude. |
| `geo_source` | One of `input`, `cache`, `fresh`, `failed`. |
| `sa2_code` | 9-digit SA2 code from ASGS. `null` if no match. |
| `sa2_name` | SA2 human-readable name. |
| `sa2_<friendly_name>` | One column per configured variable, with the configured prefix. |

Example header for a config with two variables:

```
address, lat, lon, geo_lat, geo_lon, geo_source, sa2_code, sa2_name,
sa2_median_age, sa2_median_household_income_weekly
```

---

## 9. Caching Strategy

| Cache | Default location | Format | Invalidation |
|---|---|---|---|
| Geocoded addresses | `<cache_dir>/geocoding/` | JSON per address (sharded) | Manual delete; key is hash of normalized address |
| ASGS boundaries | `<data_dir>/boundaries/` | Shapefile (extracted) | Re-download with `census-augment fetch --boundaries --refresh` |
| Census DataPacks | `<data_dir>/census/` | Extracted CSVs + metadata | Re-download with `census-augment fetch --census --refresh` |

**Defaults** are platform-appropriate user cache directories (via the `platformdirs` package), so downloads are shared across runs and across notebooks regardless of CWD — a single ~50 MB boundary download serves every project on the machine.

| OS | `<data_dir>` | `<cache_dir>` |
|---|---|---|
| Linux | `~/.cache/census-augment/data/` | `~/.cache/census-augment/cache/` |
| macOS | `~/Library/Caches/census-augment/data/` | `~/Library/Caches/census-augment/cache/` |
| Windows | `%LOCALAPPDATA%\census-augment\Cache\data\` | `%LOCALAPPDATA%\census-augment\Cache\cache\` |

**Override precedence:** explicit kwarg / CLI flag > `CENSUS_AUGMENT_DATA_DIR` / `CENSUS_AUGMENT_CACHE_DIR` env vars > platform default.

---

## 10. Error Handling

| Condition | Behavior |
|---|---|
| Address fails to geocode | Warn; row keeps null coords; flagged in summary; pipeline continues. Failures are not cached. |
| Nominatim rate-limit response (HTTP 429 / 503) | Back off exponentially up to 3 retries; if still rate-limited, treat as failed lookup. |
| Coordinates fall outside Australia / no SA2 match | Warn; row keeps null SA2; flagged in summary; pipeline continues. |
| Some configured variables missing or suppressed for a matched SA2 | Leave those cells as null; flag the row in the summary as "partially enriched"; pipeline continues. |
| Variable reference not found in metadata | **Fail fast at config load.** Suggest near-matches. |
| Required input column missing | **Fail fast at startup.** |
| Network failure during data download | Retry with exponential backoff (3 attempts); then abort with clear message. |
| Required `user_agent` missing for Nominatim | **Fail fast at config load.** |

A summary report is printed at the end of every run.

---

## 11. CLI

Using Typer. Commands and their flags:

```
# Augment a CSV end-to-end
census-augment run --config config.yaml

# Discover variables in the DataPack
census-augment discover --config config.yaml --search "income"
census-augment discover --config config.yaml --table G02

# Pre-fetch ABS data
census-augment fetch --config config.yaml --boundaries
census-augment fetch --config config.yaml --census
census-augment fetch --config config.yaml --boundaries --census --refresh

# Validate config (structurally; --full also validates against DataPack)
census-augment validate --config config.yaml
census-augment validate --config config.yaml --full

# Global flag (any command)
census-augment --verbose <command> ...        # DEBUG-level logging

# Cache override (any command that uses ABS data)
census-augment <command> --data-dir /path/to/data --cache-dir /path/to/cache
```

Cache flag precedence is documented in §9.

---

## 12. Dependencies

Suggested (Claude Code may adjust):

- `geopandas` — spatial join
- `shapely` — geometry primitives
- `pyproj` — CRS transforms
- `pandas` — tabular data
- `pydantic` — config validation
- `pyyaml` — config parsing
- `typer` (or `click`) — CLI
- `requests` — HTTP downloads
- `openpyxl` — read DataPack metadata Excel file
- `platformdirs` — user-cache directory resolution (§9)

Test deps: `pytest`, `pytest-mock`, `responses` (HTTP mocking).

---

## 13. Extensibility Hooks

The architecture should make these additions cheap, without restructuring:

- **New geocoder** (G-NAF, Google, Mapbox): implement the `Geocoder` interface in `geocoding/base.py`; register by name in config.
- **New SA level** (SA1, SA3): boundary loader and spatial join already key on SA code. Add boundary file resolution and update level enum.
- **New census year** (2026): data source loader takes year as a parameter; variable catalog is year-scoped.
- **New DataPack profile** (Indigenous, Working Population): DataPack downloader takes profile as a parameter; metadata index is profile-scoped.
- **New input/output formats**: parser and writer modules behind a small format interface.

---

## 14. Resolved Decisions

These were open questions in v0.1, resolved in v0.2:

1. **Address deduplication.** *Decision: Do not explicitly deduplicate.* Rely on the geocoding cache for efficiency. Avoids reordering output rows and keeps row-level processing predictable. Documented in §7.2.
2. **DataPack download URL stability.** *Decision: Ship with known-good base URLs and construct filenames deterministically.* Two configurable base URLs in `data_sources` (one for boundaries, one for DataPacks). Filenames built from `census.*` config values. Documented in §4 and §6.1.
3. **Computed variables.** *Decision: Out of scope for this tool entirely.* This tool's job is raw census variable attachment; ratios, percentages, and other derived metrics are the responsibility of the downstream feature engineering pipeline. Documented in §2.
4. **Partial enrichment policy.** *Decision: Leave missing/suppressed cells as null and flag the row as "partially enriched" in the run summary.* No row is dropped due to missing census values. Documented in §10.
5. **Boundary version pinning.** *Decision: Add explicit `census.asgs_edition` and `census.datum` config fields with sensible defaults (3 / GDA2020).* Forces deliberate handling when a new ASGS edition is released. Documented in §6.1.
6. **Target scale and geocoder choice.** *Decision: design for a few hundred rows per run.* At this scale Nominatim's 1 req/sec policy is acceptable (~5 minutes of geocoding for 300 fresh addresses; cache hits on re-runs are instant). Larger scales are deferred to the pluggable geocoder hook in §13 (G-NAF, paid providers). Documented in §2.
7. **Input lat/lon CRS.** *Decision: assume input lat/lon are EPSG:4326 (WGS84).* Most consumer/web/GPS-derived coordinates are WGS84; reprojection to the boundary CRS (GDA2020 / EPSG:7844) is handled internally. v1 does not expose this as configurable — convert externally if your data is GDA94 or a projected CRS. Documented in §7.3.
8. **Nominatim rate-limit handling.** *Decision: back off exponentially on HTTP 429/503 with up to 3 retries; if still rate-limited, treat as a failed lookup. Failed lookups are not cached.* Aligns with the overall "geocoding failure → null coords, flag, continue" policy (§10) and avoids stalling the pipeline on persistent throttling, while letting next-run retries pick up after a transient outage. Documented in §7.2 and §10.
9. **Verified ABS endpoints.** *Decision: corrected boundary filename to the `_SHP_` variant; format is Shapefile.* Real ABS does not offer per-level GeoPackage at SA granularity; only Shapefile per-level (or a 505 MB bundled main-structure GeoPackage). Per-level Shapefile is the right v1 fit at ~50 MB. Documented in §4.1.
10. **Real DataPack metadata structure.** *Decision: parse the real `Cell Descriptors Information` sheet with title-row tolerance and descriptor-mode-aware code lookup; use `Columnheadingdescriptioninprofile` (not `Long`) for human descriptions; pick metadata file by name pattern.* Confirmed against 2021 GCP. Documented in §4.2 and §6.2.
11. **Verified Nominatim response shape.** *Decision: parser as designed works against the live service.* `lat` / `lon` are strings (parsed to float), response is a JSON array of objects, descriptive User-Agent format `name/version (email)` is accepted. Documented in §7.2.
12. **Real-data verification strategy.** *Decision: hermetic pytest suite stays mocked; opt-in `tools/` scripts download real ABS files and exercise the parsers against them.* Avoids CI flake from ABS uptime while making real-world validation a discoverable, deliberate developer activity. Documented in §17.
13. **Library / programmatic use as a first-class entry point.** *Decision: same `Pipeline` class, two entry points — `Pipeline.run()` for file-in/file-out (CLI) and `Pipeline.augment(df)` for DataFrame-in/DataFrame-out (notebooks/library).* Returns an `AugmentResult` with the DataFrame, run summary, and typed boolean Series for per-row classification. Documented in §18.
14. **User-level cache by default.** *Decision: default cache locations use `platformdirs`-managed user cache directories rather than CWD-relative `./data` / `./cache`.* Friendlier for library use (one shared cache across notebooks), avoids the CLI surprise of "where did this 50 MB go". Override via env vars or explicit flags/kwargs. Documented in §9 and §18.
15. **Optional input/output paths in Config.** *Decision: `input.path` and `output.path` are optional fields on the Config schema.* CLI's `run` command validates they're set; library use doesn't need them. The input column-name fields (`address_column`, `latitude_column`, `longitude_column`) remain — they describe the DataFrame regardless of provenance. Documented in §6.1 and §18.

## 15. Open Questions

*(None at present. Add new ones here as they arise during implementation.)*

---

## 16. Acceptance Criteria for v1

The implementation is considered done when:

- A user can run `census-augment run --config config.example.yaml` end-to-end on a sample input of mixed addresses + coordinates and produce an enriched CSV.
- All boundary and DataPack files are downloaded automatically on first run.
- Geocoding cache is populated and reused on the second run (verifiable by faster runtime).
- `census-augment discover --search "income"` returns matching census variables.
- Config errors (bad variable references, missing input columns) produce clear, actionable error messages.
- Test suite covers: config validation, cache hit/miss, spatial join correctness on a small fixture, end-to-end pipeline on a tiny dataset.
- **Library use:** `pipeline.augment(df)` produces an enriched DataFrame and an `AugmentResult` (with run summary + per-row classification masks) without touching the filesystem beyond the shared user cache for ABS data.

---

## 17. Real-data verification

The pytest suite is **hermetic**: every external interaction (Nominatim, ABS downloads) is mocked. To validate that parsers work against the **real** ABS endpoints and files, two scripts live under `tools/`:

- **`tools/fetch_real_data.py`** downloads the real boundary ZIP and DataPack ZIP into the configured cache (defaults to the platform user cache per §9; override via `CENSUS_AUGMENT_DATA_DIR`) and optionally captures one Nominatim sample response. It uses the actual `BoundariesDataSource` and `DataPacksDataSource` classes, so running it exercises the production code path.
- **`tools/verify_real_parsers.py`** runs the parsers against the locally-cached real files and prints a tick/cross summary. Non-zero exit on failure. Suitable as a manual smoke test or a low-frequency scheduled job.

When to run:
1. After initial dev environment setup.
2. After ABS publishes new versions of the boundaries / DataPacks (e.g. when 2026 Census lands).
3. Whenever code touches the parsers.

This dual approach keeps the test suite fast and offline-safe while making real-world validation a deliberate, audited path to ground-truth.

---

## 18. Library use

The same `Pipeline` class supports two entry points:

- **`pipeline.run()`** — reads `config.input.path`, writes `config.output.path`, returns a `RunSummary`. Used by the CLI's `run` command.
- **`pipeline.augment(df)`** — takes a DataFrame, returns an `AugmentResult` (the augmented DataFrame plus typed per-row classification masks and the run summary). No file I/O.

### 18.1 Constructing a pipeline

Three ways, in increasing power:

```python
from census_augment import Pipeline, Config, load_config

# A. Notebook-friendly factory (most common for library use)
pipeline = Pipeline.create(
    variables={"median_age": "G02.Median_age_persons"},
    user_agent="my-app/1.0 (me@example.com)",
    latitude_column="lat",
    longitude_column="lon",
)

# B. Programmatic Config (full control without YAML)
cfg = Config(input=..., output=..., census=..., ...)
pipeline = Pipeline.from_config(cfg)

# C. From YAML (same as the CLI uses)
pipeline = Pipeline.from_config(load_config("config.yaml"))
```

### 18.2 The `augment` method

```python
result = pipeline.augment(
    df,
    # Optional per-call overrides; default to whatever's in config.input.
    address_column="street",
    latitude_column="latitude",
    longitude_column="longitude",
)
```

Returns an `AugmentResult` with:

| Field | Type | Meaning |
|---|---|---|
| `df` | `pandas.DataFrame` | The input DataFrame plus geo + sa2 + enrichment columns. |
| `summary` | `RunSummary` | Aggregated counts (same shape as CLI). |
| `added_columns` | `list[str]` | Names of columns added by this augment (handy for `df[result.added_columns]`). |
| `is_fully_enriched` | `pandas.Series[bool]` | True for rows where all enrichment cells are non-null. |
| `geocoding_failed` | `pandas.Series[bool]` | True for rows whose geocoding ended in `failed`. |
| `sa2_unmatched` | `pandas.Series[bool]` | True for rows that had coords but didn't match any SA2 polygon. |

All three boolean Series share `df`'s index, so `result.df[~result.geocoding_failed]` is the natural filter.

### 18.3 Cache directories

See §9. By default `Pipeline.augment(df)` and `Pipeline.run()` share a single user-level cache, so the ~50 MB boundary download (and ~40 MB DataPack download) happens once across all notebooks and runs. Override via env vars or kwargs.

### 18.4 Public API

Top-level imports from `census_augment`:

```python
from census_augment import (
    # Main entry point
    Pipeline, AugmentResult, RunSummary,
    # Config schema
    Config, InputConfig, OutputConfig, CensusConfig,
    DataSourcesConfig, GeocodingConfig, load_config,
    # Catalog (for programmatic discover)
    VariableCatalog, CatalogError,
    # For implementing a custom geocoder per §13
    Geocoder,
)
```

Internal subsystems (`BoundariesDataSource`, `DataPacksDataSource`, `SpatialIndex`, `CensusEnricher`, `NominatimGeocoder`, `GeocodeCache`) remain importable from their submodules but are not promoted to the top level — they may evolve internally between versions.
