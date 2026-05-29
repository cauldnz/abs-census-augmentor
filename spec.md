# Australian Census Augmentation Tool — Specification

> **Status:** Implemented through v1.4.1 (current).
> **Purpose:** Hand-off specification for implementation by Claude Code. Update this document as design decisions evolve.
>
> Release history at a glance:
>
> - **v1.0** — SA2-keyed Census GCP enrichment pipeline (the baseline).
> - **v1.1 / v1.2.x** — G-NAF Core integration as the primary geocoder; tiered matching (`gnaf_exact` → `gnaf_component` → `gnaf_fuzzy` → `nominatim_*`); MB-fast-path SA2 resolution; misc. bug fixes for the dotted-bucket TLS path and the `gnaf-loader` subdirectory.
> - **v1.3** — Generalises the pipeline into a pluggable framework. Datasets and derived features become first-class registry entries described by markdown spec files. The 2021 GCP DataPack stops being special and becomes one entry alongside SEIFA, ERP, DSS, ATO Personal Income, plus six curated PRESET features. See §20 (Pluggable Datasets) and §21 (Derived Features).
> - **v1.4** — `PRESET.<id>` is now a first-class variable namespace alongside `G\d+.<col>`, `SEIFA.*`, `ERP.*`, `DSS.*`, `ATO.*`. The pipeline auto-loads PRESET source columns transparently and runs `FeatureEvaluator` on them. See §21.2.
> - **v1.4.1** — Build-config fix: the wheel now ships the dataset / feature spec markdown so registries populate on a real `pip install` (not just source checkouts). See decision #32 in §14.

---

## 1. Purpose

A Python tool that takes a dataset of locations in Australia (as addresses, coordinates, or a mix) and augments each record with selected variables from the Australian Bureau of Statistics (ABS) Census of Population and Housing at the SA2 statistical area level.

The output is a CSV with the original records plus appended columns drawn from the census, suitable for downstream analysis or merging with other datasets.

---

## 2. Scope

### v1 — in scope (v1.0 baseline + v1.3 additions)

- Input: CSV containing addresses and/or `(lat, lon)` coordinates (or a mix per row).
- Geocoding via a tiered strategy: G-NAF (Geoscape's Geocoded National Address File) as the primary "gold-standard" source, with Nominatim (public OpenStreetMap API) as a fallback.
- SA2-level statistical area assignment via either G-NAF's mesh-block code (when available, no spatial join needed) or point-in-polygon spatial join (fallback path).
- **Registered SA2-keyed datasets** (v1.3 §20). The 2021 GCP DataPack is one entry. Initial registry:
    - `gcp` — ABS Census GCP DataPack (2021 + 2016 releases).
    - `seifa` — Socio-Economic Indexes for Areas (4 indexes × 10 fields).
    - `erp_by_sa2` — ABS Estimated Resident Population (annual).
    - `dss_payments` — DSS Payment Demographic Data (quarterly).
    - `ato_personal_income` — ABS Personal Income (administrative; ATO-derived).
- **Derived features / PRESETs** (v1.3 §21). Curated ratios — `pct_drive_to_work`, `pct_renters`, `pct_aged_65_plus`, etc. — with the right denominator pre-baked. Single source of truth for "what's the right denominator for X" across downstream consumers.
- Output: enriched CSV.
- Configuration-driven variable selection using human-readable names. Variable strings dispatch to the right dataset by namespace (e.g. `G02.foo` → GCP, `SEIFA.irsd_decile` → SEIFA, `PRESET.pct_drive_to_work` → derived feature).
- Local caching of geocoded addresses, G-NAF data, and ABS data.
- Runtime download of ABS data and G-NAF data; nothing checked into git.
- A `discover` command that surfaces registered variables, datasets, and PRESET features.

### Future / out of scope for v1

- Paid geocoding providers (Google, Mapbox).
- SA1 and SA3 levels (architecture should not preclude them).
- Other DataPack profiles (Indigenous, Working Population, Time Series).
- 2026 Census data when released (architecture should not preclude it).
- Output formats other than CSV (Parquet, GeoPackage).
- Explicit input deduplication. Duplicate input rows are processed independently; efficiency on duplicate addresses comes from the geocoding cache.
- Heavy NLP-based address parsers (`address-net`, `libpostal`) are deferred entirely to extensibility hooks (§13). v1 ships a lightweight rules-based normaliser sufficient for well-formed AU addresses, with no opt-in extras — the heavy NLP options carry system-level prerequisites (TensorFlow, libpostal C library) that we want v1 to stay clear of.
- Datasets with native granularity finer than annual (e.g. monthly economic indicators) — this tool is fundamentally about static / slow-moving SA2 features. DSS quarterly is the borderline case; v1.3 takes the latest snapshot per Pipeline run (see §20.4).
- Datasets that aren't natively SA2-keyed (BoM weather is the canonical example — station-keyed, requires interpolation). Scope-creeping into point-to-area interpolation belongs in a sibling tool. See §20.7 for the deferred backlog.

### Usage assumptions

- **Target scale:** typically a few hundred rows per run. With G-NAF as the primary geocoder, most rows are matched offline (instant); Nominatim's 1 req/sec policy applies only to the residual fallback set. See §19.6 for stronger performance claims when no Nominatim fallback is needed.

---

## 3. Architecture Overview

A linear pipeline with a tiered geocoder:

```
Input CSV
   │
   ▼
Geocoding ─── Tier 1: G-NAF exact match  (offline, instant)
              Tier 2: G-NAF component    (offline, instant)
              Tier 3: G-NAF FTS / fuzzy  (offline, fast)
              Tier 4: Nominatim fallback (rate-limited)
   │
   ▼
SA2 Resolution ─── A: Mesh-block lookup (when G-NAF matched)
                   B: Point-in-polygon  (lat/lon inputs + Nominatim hits)
   │
   ▼
Census Enrichment (DataPack lookup)
   │
   ▼
Output CSV
```

Each stage is independently testable. Cached artifacts (G-NAF database, geocoded addresses, downloaded boundaries and DataPacks) make re-runs cheap.

---

## 4. Data Sources

### 4.1 ASGS SA2 Boundaries

- **Source:** ABS Australian Statistical Geography Standard (ASGS) Edition 3 (covers Jul 2021 – Jun 2026).
- **Format:** Shapefile (`.shp` + `.dbf` / `.prj` / `.shx` sidecars). Per-level GeoPackage is not offered by ABS at SA-level granularity; only a 505 MB bundled "main structure" GeoPackage exists, which is overkill for v1's SA2-only scope.
- **CRS:** GDA2020 (EPSG:7844). Reproject input points as needed.
- **Approximate size:** ~50 MB.
- **Base URL (configurable):** `https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/digital-boundary-files`
- **Filename pattern:** `{level}_{year}_AUST_SHP_{datum}.zip`, e.g. `SA2_2021_AUST_SHP_GDA2020.zip`. Note the `SHP` token sits between `AUST_` and the datum on the **ZIP** filename — the files **inside** the ZIP do not have it (they are named `SA2_2021_AUST_GDA2020.shp` etc.).
- The tool downloads this on first use into `data/boundaries/` and caches it. Used only for the spatial-join fallback path (§7.3); G-NAF-matched rows skip it.

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

### 4.3 G-NAF (Geoscape Geocoded National Address File)

- **Source:** Geoscape Australia, distributed under the Open G-NAF EULA (CC BY 4.0 with a mail-use restriction). Two distribution paths are supported (see §19).
- **Selection:** **G-NAF Core** — the simplified single-table format introduced in August 2022. Cuts G-NAF's ~50 normalised tables down to two (current + retired), with `ADDRESS_DETAIL_PID`, `ADDRESS_LABEL` (pre-formatted), `LATITUDE`, `LONGITUDE`, `LEGAL_PARCEL_ID`, and crucially **`MB_CODE`** (the ABS Mesh Block code).
- **Coverage (Feb 2026 release):** ~15.86 million current Australian addresses.
- **Update cadence:** Quarterly (Feb / May / Aug / Nov).
- **Datum:** GDA2020 (matches §4.1 boundaries). GDA94 also available; selectable in config.
- **Distribution:**
  1. **Primary path — pre-built GeoParquet** from the community-maintained [`gnaf-loader`](https://github.com/minus34/gnaf-loader) project, hosted on AWS S3 at `s3://minus34.com/opendata/geoscape-{YYYYMM}/geoparquet/` with `--no-sign-request` (public). Already cleaned of known data quirks (e.g. orphan addresses re-linked to gazetted localities, locality boundaries flattened). DuckDB queries this directly, with optional download-to-cache.
  2. **Fallback path — official PSV from data.gov.au.** Used if S3 is unreachable or the user opts out of the third-party-curated copy. Larger and slower to load, but direct ABS/Geoscape provenance.

The full G-NAF (~5 GB unpacked, multiple tables) is **not** used; G-NAF Core is sufficient for this tool's purposes.

> **Implementation note:** Both ABS base URLs (boundaries, DataPacks) and the G-NAF S3 base URL are exposed in config (see §6). Defaults ship with the spec; users can override any if endpoints change.

---

## 5. Project Structure

```
abs-census-augmentor/
├── README.md                       # User-facing intro (CLI + library)
├── CLAUDE.md                       # Contributor / agent guidance
├── CHANGELOG.md                    # Per-release change log
├── BACKLOG.md                      # Deferred items + future demos / datasets
├── spec.md                         # This document
├── pyproject.toml                  # hatchling build; force-include for spec md (§14 #32)
├── config.example.yaml             # Sample config for the CLI
├── LICENSE
├── .gitignore
├── .gitattributes                  # LF endings on *.sh / *.tape
├── .devcontainer/                  # VSCode + WSL one-command dev setup
│   ├── devcontainer.json
│   ├── post-create.sh              # uv install + sync + smoke test
│   └── README.md
├── .github/workflows/              # CI: tests + ruff + mypy + wheel-install regression
├── datasets/                       # Markdown specs for registered datasets (§20.1)
│   ├── _template.md
│   ├── gcp.md
│   ├── seifa.md
│   ├── erp_by_sa2.md
│   ├── dss_payments.md
│   └── ato_personal_income.md
├── features/                       # Markdown specs for PRESET features (§21.1)
│   ├── _template.md
│   ├── pct_drive_to_work.md
│   ├── motor_vehicles_per_dwelling.md
│   ├── pct_renters.md
│   ├── pct_employed_full_time.md
│   ├── pct_aged_65_plus.md
│   └── pct_one_parent_family.md
├── docs/                           # Handbook (markdown) + embedded README assets
│   ├── index.md                    # Handbook TOC / entry point
│   ├── usage-library.md            # Pipeline.augment, AugmentResult
│   ├── usage-cli.md                # Full CLI reference
│   ├── configuration.md            # config.yaml schema, cache locations
│   ├── gnaf-setup.md               # G-NAF cache vs remote, prefetch, BYO
│   ├── development.md              # Make targets, dev container, contributing
│   ├── frames/                     # Per-scene PNGs for README scene strips
│   └── *.gif                       # Demo GIFs embedded in README
├── examples/                       # Runnable usage scripts (CLI + library)
├── data/                           # Optional project-local cache; gitignored
│   ├── README.md                   # Defaults to platform user cache (§9)
│   └── .gitignore                  # Ignores all but README.md
├── cache/                          # Optional project-local geocoding cache
│   ├── README.md
│   └── .gitignore
├── src/
│   └── census_augment/
│       ├── __init__.py             # Public API exports (spec §18.4)
│       ├── py.typed                # Inline type marker for downstream users
│       ├── cli.py                  # Typer entry point (run / discover / fetch / gnaf-info / validate)
│       ├── config.py               # Pydantic schema + YAML loader
│       ├── paths.py                # User-cache directory resolution (§9)
│       ├── catalog.py              # GCP variable resolution + search + suggestions
│       ├── spatial.py              # Point-in-polygon → SA2 (fallback path)
│       ├── enrich.py               # CensusEnricher: dispatch + PRESET integration (§7.4, §21.2)
│       ├── pipeline.py             # Orchestration; multi-provider, MB/spatial split
│       ├── features.py             # FeatureSpec + FeatureRegistry + FeatureEvaluator (§21)
│       ├── _http_retry.py          # Shared retry helper for ABS streaming downloads
│       ├── data_sources/
│       │   ├── _base.py            # Shared download/extract base
│       │   ├── boundaries.py       # Shapefile download + load
│       │   ├── datapacks.py        # CSV + Excel-metadata parser
│       │   ├── mb_correspondence.py # MB_CODE → SA2_CODE lookup (fast path)
│       │   └── gnaf.py             # G-NAF Core fetch + DuckDB indexing
│       ├── datasets/               # Pluggable-dataset framework (§20)
│       │   ├── __init__.py         # `registry` singleton (re-export)
│       │   ├── _spec.py            # DatasetSpec parser
│       │   ├── _protocol.py        # DatasetFetcher Protocol
│       │   ├── _registry.py        # Registry + namespace resolution
│       │   ├── _seifa.py           # SeifaDataSource
│       │   ├── _erp.py             # ErpDataSource
│       │   ├── _dss.py             # DssDataSource
│       │   └── _ato.py             # AtoDataSource
│       └── geocoding/
│           ├── base.py             # Geocoder Protocol + GeocodeResult dataclass
│           ├── cache.py            # Hash-keyed JSON cache (sharded)
│           ├── normalize.py        # AU-specific address normaliser (rules-based)
│           ├── gnaf.py             # GnafGeocoder (Tiers 1–3); DuckDB-backed
│           └── nominatim.py        # NominatimGeocoder (Tier 4); fallback
├── tools/                          # Real-data verification (see §17) + demo rendering
│   ├── README.md
│   ├── fetch_real_data.py
│   ├── verify_real_parsers.py
│   └── demo/                       # VHS scripts + Dockerfile for README GIFs
│       ├── Dockerfile
│       ├── demo.tape
│       ├── render.sh / render.ps1
│       ├── config.yaml
│       └── input.csv
└── tests/                          # Hermetic test suite (no real network)
    ├── conftest.py                 # Shared fixtures (synthetic SA2 + DataPack + G-NAF)
    └── test_*.py                   # 24 files, ~515 tests as of v1.4.1
```

Wheel installs additionally see (force-included from `datasets/` and `features/`
at build time, per §14 #32):

- `census_augment/datasets/_specs/*.md`
- `census_augment/_features/*.md`

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
data_sources:
  boundaries_base_url: https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/digital-boundary-files
  datapacks_base_url: https://www.abs.gov.au/census/find-census-data/datapacks/download
  gnaf_s3_base_url: s3://minus34.com/opendata           # Pre-built GeoParquet (default)
  gnaf_official_base_url: https://data.gov.au/data/dataset  # PSV fallback

geocoding:
  # Ordered list of geocoders. The first to return a result for a given row wins.
  # 'gnaf' is the gold-standard offline matcher; 'nominatim' is the network fallback.
  providers: [gnaf, nominatim]
  cache_enabled: true

  gnaf:
    # 'remote': query Parquet over HTTPS without downloading (fast first call, needs net at query time)
    # 'cache':  download Parquet to user cache on first use, query locally thereafter (default)
    # 'official': fetch official PSV from data.gov.au, build local DuckDB (heaviest, most provenance-clean)
    mode: cache
    release: latest                # 'latest' or specific YYYYMM (e.g. 202602). Pinned in resolved config.
    datum: GDA2020                 # GDA2020 or GDA94. Should match census.datum.
    fuzzy_threshold: 0.85          # Tier 3 score floor; below this, fall through to Nominatim.

  nominatim:
    user_agent: "census-augment/0.1 (you@example.com)"   # Required by Nominatim policy
    rate_limit_per_second: 1

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

**Input column validation:** at least one of `input.address_column` OR both `input.latitude_column` and `input.longitude_column` must be set; the lat/lon pair must be set together.

**Optional path fields:** `input.path` and `output.path` are required only by the CLI's `run` command. Library users calling `Pipeline.augment(df)` (see §18) can omit them.

**Geocoder selection:** the `geocoding.providers` list controls which geocoders are tried, in order. Setting `providers: [nominatim]` reproduces the v0.9 behaviour for users who don't want G-NAF. Setting `providers: [gnaf]` is offline-only (no network at query time).

**Datum consistency:** if `geocoding.gnaf.datum` differs from `census.datum`, a config-load **WARNING** is emitted. They should normally match — silent CRS mismatch is the kind of thing that turns up as a weird bug six months later.

**Release pinning:** `geocoding.gnaf.release: latest` resolves to the most recent release at fetch time and is then *recorded* in the resolved-config snapshot, so subsequent runs use the same release. This keeps a re-run reproducible without forcing users to specify versions explicitly.

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

### 7.2 Geocoding (tiered)

For each row needing geocoding, providers in `geocoding.providers` are tried in order. Each provider returns a `GeocodeResult` carrying lat/lon, optional `mb_code`, and a `match_quality` tag.

**G-NAF geocoder** (offline, see §19 for full design):

- **Tier 1 — exact `ADDRESS_LABEL`.** Normalise the input address (uppercase, collapse whitespace, expand AU street/state abbreviations via a small lookup table, strip punctuation), then exact-match against G-NAF's pre-formatted `ADDRESS_LABEL`. Fast and high-precision.
- **Tier 2 — component match.** Parse the input into `number + street + locality + postcode + state` using the rules-based normaliser in `geocoding/normalize.py`, then exact-match each component (with postcode/state pre-filtering for performance).
- **Tier 3 — FTS / fuzzy match.** Within a candidate set pre-filtered by postcode (or locality if no postcode), score candidates by similarity using DuckDB's FTS extension. Best score above `fuzzy_threshold` wins; otherwise fall through.
- **Match quality** is recorded as `gnaf_exact`, `gnaf_component`, or `gnaf_fuzzy` (with the score for fuzzy hits surfaced in the run summary).

**Nominatim geocoder** (network fallback, unchanged from v0.9):

- For each address-only row that fell through G-NAF (or for runs configured with `providers: [nominatim]`), check the geocoding cache first.
- Cache key: SHA-256 hash of the normalised address.
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
- If Nominatim returns HTTP 429 or 503, back off exponentially up to 3 retries before treating the lookup as failed.
- Match quality is recorded as `nominatim_cache` (cache hit) or `nominatim_fresh` (network call).
- Failed lookups: record retains `null` coordinates and is flagged in the run summary; pipeline continues. Failures are not cached — they are retried on the next run.

**G-NAF cache.** G-NAF queries hit a local DuckDB-indexed database (or remote Parquet, depending on `mode`). G-NAF lookups are deterministic and effectively free, so they are not memoised separately — caching the underlying database is sufficient.

**Duplicate addresses are not explicitly deduplicated** before geocoding. Within a single run, a duplicate address hits the cache (or a deterministic G-NAF lookup) on its second occurrence. Output ordering is preserved.

### 7.3 SA2 resolution

Two paths, picked per-row based on what the geocoder produced:

- **Fast path — mesh-block lookup.** If the geocoder returned an `mb_code`, resolve `mb_code → sa2_code` via the ABS-published correspondence (§19.4), bypassing the spatial join entirely. This applies to all G-NAF-matched rows. Faster, deterministic, and arguably more correct than centroid-in-polygon (G-NAF's mesh-block assignment uses parcel-level intelligence, not just point geometry).
- **Fallback path — point-in-polygon.** For lat/lon inputs and Nominatim-resolved rows (which return coordinates but no `mb_code`):
  - Load SA2 GeoPackage into a GeoDataFrame.
  - Build a spatial index (`sindex`).
  - Input lat/lon are interpreted as EPSG:4326 (WGS84). Points are reprojected to the boundary CRS (GDA2020 / EPSG:7844) before the join.
  - Point-in-polygon for each record. Records outside any SA2 get `null` `sa2_code` and `sa2_name`.

The boundary file (§4.1) is downloaded only when the fallback path is needed for at least one row in the run.

**Implementation note (batched, not per-row).** Rows are partitioned into the two groups (those with `mb_code`, those without) and each group is processed in one batch — `mb_code` lookups via dict indexing, lat/lon lookups via a single `sjoin` call. Results are then re-merged into the original input order. Read the per-row "decision" above as logical, not as a row-by-row Python loop.

### 7.4 Census enrichment

- Determine the unique set of `(table)` references across all configured variables.
- Load only those tables from the DataPack.
- Build a single lookup keyed by SA2 code.
- Join enriched columns onto the dataset.

### 7.5 Output

- Write CSV with all original columns preserved, in original order, followed by appended columns (see §8).
- Print a run summary: total rows; per-tier hit counts (`gnaf_exact`, `gnaf_component`, `gnaf_fuzzy`, `nominatim_cache`, `nominatim_fresh`, `failed`); SA2 resolution path counts (`mb_code`, `spatial_join`, `unmatched`); fully enriched / partially enriched counts.

---

## 8. Output Schema

Original input columns are preserved unchanged. The following columns are appended in this order:

| Column | Description |
|---|---|
| `geo_lat` | Resolved latitude (from input or geocoding). |
| `geo_lon` | Resolved longitude. |
| `geo_source` | One of `input`, `gnaf_exact`, `gnaf_component`, `gnaf_fuzzy`, `nominatim_cache`, `nominatim_fresh`, `failed`. |
| `geo_match_score` | For `gnaf_fuzzy`: similarity score (0.0–1.0). Null otherwise. |
| `sa2_code` | 9-digit SA2 code from ASGS. `null` if no match. |
| `sa2_name` | SA2 human-readable name. |
| `sa2_resolution` | One of `mb_code`, `spatial_join`, `unmatched`. Indicates which §7.3 path resolved this row. |
| `sa2_<friendly_name>` | One column per configured variable, with the configured prefix. |

Example header for a config with two variables:

```
address, lat, lon,
geo_lat, geo_lon, geo_source, geo_match_score,
sa2_code, sa2_name, sa2_resolution,
sa2_median_age, sa2_median_household_income_weekly
```

> **v1.0 is a breaking change from v0.9.** The `geo_source` enum has changed (was `input | cache | fresh | failed`; now provider-prefixed: `input | gnaf_* | nominatim_* | failed`). Two new columns (`geo_match_score`, `sa2_resolution`) appear in the output. Anyone reading v0.9 output CSVs alongside v1.0 needs to handle both schemas. See `CHANGELOG.md` for the full upgrade note.

---

## 9. Caching Strategy

| Cache | Default location | Format | Invalidation |
|---|---|---|---|
| Geocoded addresses (Nominatim) | `<cache_dir>/geocoding/` | JSON per address (sharded) | Manual delete; key is hash of normalised address |
| ASGS boundaries | `<data_dir>/boundaries/` | Shapefile (extracted) | Re-download with `census-augment fetch --boundaries --refresh` |
| Census DataPacks | `<data_dir>/census/` | Extracted CSVs + metadata | Re-download with `census-augment fetch --census --refresh` |
| G-NAF Core (cache mode) | `<data_dir>/gnaf/{release}/` | GeoParquet files | Re-fetch with `census-augment fetch --gnaf --refresh` or by changing `geocoding.gnaf.release` |
| MB → SA2 correspondence | `<data_dir>/mb/MB_{year}_AUST_SHP_{datum}/` | Shapefile (.dbf only is read) | Re-fetched alongside boundaries; lookup dict built lazily from .dbf attribute table (see §15.1 / §19.4) |

**Defaults** are platform-appropriate user cache directories (via the `platformdirs` package), so downloads are shared across runs and across notebooks regardless of CWD.

| OS | `<data_dir>` | `<cache_dir>` |
|---|---|---|
| Linux | `~/.cache/census-augment/data/` | `~/.cache/census-augment/cache/` |
| macOS | `~/Library/Caches/census-augment/data/` | `~/Library/Caches/census-augment/cache/` |
| Windows | `%LOCALAPPDATA%\census-augment\Cache\data\` | `%LOCALAPPDATA%\census-augment\Cache\cache\` |

**Override precedence:** explicit kwarg / CLI flag > `CENSUS_AUGMENT_DATA_DIR` / `CENSUS_AUGMENT_CACHE_DIR` env vars > platform default.

**G-NAF size on disk:** ~500 MB for G-NAF Core Parquet (cache mode); ~5 GB if user opts into the official PSV path with full G-NAF.

---

## 10. Error Handling

| Condition | Behavior |
|---|---|
| Address fails to match G-NAF at any tier | Fall through to next provider in `geocoding.providers`. Not an error. |
| Address fails to geocode in all providers | Warn; row keeps null coords; flagged in summary; pipeline continues. Failures are not cached. |
| Nominatim rate-limit response (HTTP 429 / 503) | Back off exponentially up to 3 retries; if still rate-limited, treat as failed lookup. |
| Coordinates fall outside Australia / no SA2 match | Warn; row keeps null SA2; flagged in summary; pipeline continues. |
| G-NAF row matched but `mb_code` is null | Fall through to spatial-join path using G-NAF's `lat`/`lon`. Logged at INFO. |
| G-NAF release `latest` cannot be resolved (S3 listing fails) | Cascading fallback: (1) most recent cached release if any; (2) suggest `mode: official` in the abort message; (3) abort. |
| G-NAF S3 unreachable in `remote` mode | Abort with cascading suggestions: (a) switch to `mode: cache` (still requires S3 for first download), (b) switch to `mode: official` (no S3 dependency at all), (c) point at a previously-fetched cached release if one exists. |
| Some configured variables missing or suppressed for a matched SA2 | Leave those cells as null; flag the row as "partially enriched"; pipeline continues. |
| Variable reference not found in metadata | **Fail fast at config load.** Suggest near-matches. |
| Required input column missing | **Fail fast at startup.** |
| Network failure during data download | Retry with exponential backoff (3 attempts); then abort with clear message. |
| Required `user_agent` missing for Nominatim | **Fail fast at config load** — but only if `nominatim` is in `geocoding.providers`. |

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

# Pre-fetch data
census-augment fetch --config config.yaml --boundaries
census-augment fetch --config config.yaml --census
census-augment fetch --config config.yaml --gnaf
census-augment fetch --config config.yaml --boundaries --census --gnaf --refresh

# Validate config (structurally; --full also validates against DataPack)
census-augment validate --config config.yaml
census-augment validate --config config.yaml --full

# Inspect resolved G-NAF release (useful for reproducibility)
census-augment gnaf-info --config config.yaml

# Global flag (any command)
census-augment --verbose <command> ...        # DEBUG-level logging

# Cache override (any command that uses ABS or G-NAF data)
census-augment <command> --data-dir /path/to/data --cache-dir /path/to/cache
```

The `gnaf-info` command reports: the resolved G-NAF release (e.g. `202602`), the release date, the `mode` in effect (`remote` / `cache` / `official`), the local on-disk cache path (when applicable), and the on-disk size. Useful for capacity planning and reproducibility.

---

## 12. Dependencies

- `geopandas` — spatial join (fallback path)
- `shapely` — geometry primitives
- `pyproj` — CRS transforms
- `pandas` — tabular data
- `pydantic` — config validation
- `pyyaml` — config parsing
- `typer` — CLI
- `requests` — HTTP downloads
- `openpyxl` — read DataPack metadata Excel file
- `platformdirs` — user-cache directory resolution (§9)
- **`duckdb`** — G-NAF indexing and FTS (new in v1.0)
- **`pyarrow`** — Parquet I/O for G-NAF (new in v1.0)
- **`rapidfuzz`** — fast Levenshtein/token-set scoring for Tier 3 fuzzy match (new in v1.0)
- **`boto3`** (or `s3fs`) — anonymous S3 access for `remote`/`cache` modes (new in v1.0)

Test deps: `pytest`, `pytest-mock`, `responses` (HTTP mocking).

v1 deliberately ships with no NLP-parser optional extras. `address-net` (TensorFlow-based) and `libpostal` (C library + ~2 GB data files) both have system-level prerequisites that we want v1 to stay clear of. They remain pluggable via the `Geocoder` and `Normalizer` interfaces (§13) for any user who wants to layer them in without forking.

---

## 13. Extensibility Hooks

The architecture should make these additions cheap, without restructuring:

- **New geocoder.** Implement the `Geocoder` Protocol in `geocoding/base.py`; register in the providers list in config. v1 ships `gnaf` and `nominatim`; future candidates include G-NAF Live (Geoscape API), Google, Mapbox.
- **Better address parser.** Plug in `address-net` or `libpostal` behind the same `geocoding/normalize.py` interface. v1 ships a rules-based normaliser; the pluggable design lets users opt in to NLP-based parsing without forking, and crucially without v1 itself depending on those heavy components.
- **New SA level** (SA1, SA3): boundary loader and spatial join already key on SA code. Add boundary file resolution and update level enum. The MB-correspondence path naturally extends — G-NAF's `MB_CODE` resolves to all higher SA levels.
- **New census year** (2026): data source loader takes year as a parameter; variable catalog is year-scoped. G-NAF's `MB_CODE` is tied to ABS Mesh Block vintages — when 2026 mesh blocks land, the correspondence file gets a new vintage.
- **New DataPack profile** (Indigenous, Working Population): DataPack downloader takes profile as a parameter; metadata index is profile-scoped.
- **New input/output formats**: parser and writer modules behind a small format interface.

---

## 14. Resolved Decisions

These were open questions in earlier drafts, resolved through discussion:

1. **Address deduplication.** *Decision: Do not explicitly deduplicate.* Rely on the geocoding cache for efficiency.
2. **DataPack download URL stability.** *Decision: Ship with known-good base URLs and construct filenames deterministically.*
3. **Computed variables.** *Decision: Out of scope for this tool entirely.*
4. **Partial enrichment policy.** *Decision: Leave missing/suppressed cells as null and flag the row as "partially enriched" in the run summary.*
5. **Boundary version pinning.** *Decision: Add explicit `census.asgs_edition` and `census.datum` config fields with sensible defaults (3 / GDA2020).*
6. **Target scale and geocoder choice (v0.9).** *Decision: design for a few hundred rows per run, Nominatim acceptable.* **Updated in v1.0:** with G-NAF as primary, even few-thousand-row runs are practical (see §19.6).
7. **Input lat/lon CRS.** *Decision: assume input lat/lon are EPSG:4326 (WGS84).*
8. **Nominatim rate-limit handling.** *Decision: back off exponentially on HTTP 429/503 with up to 3 retries; failed lookups are not cached.*
9. **Verified ABS endpoints.** *Decision: corrected boundary filename to the `_SHP_` variant; format is Shapefile.*
10. **Real DataPack metadata structure.** *Decision: parse the real `Cell Descriptors Information` sheet with title-row tolerance and descriptor-mode-aware code lookup.*
11. **Verified Nominatim response shape.** *Decision: parser as designed works against the live service.*
12. **Real-data verification strategy.** *Decision: hermetic pytest suite stays mocked; opt-in `tools/` scripts download real ABS files.*
13. **Library / programmatic use as a first-class entry point.** *Decision: same `Pipeline` class, two entry points.*
14. **User-level cache by default.** *Decision: default cache locations use `platformdirs`-managed user cache directories.*
15. **Optional input/output paths in Config.** *Decision: optional fields, CLI's `run` command validates they're set.*
16. **Lenient column resolution in `augment`.** *Decision: configured locator columns absent from the input DataFrame are dropped with a WARNING.*
17. **Wire `geocoding.cache_enabled` through.** *Decision: implement as a `NullCache` injected when the flag is `False`.*

**v1.0 additions (G-NAF support):**

18. **G-NAF as first-class geocoder.** *Decision: G-NAF is shipped as the primary geocoder, not a future-pluggable option.* Nominatim becomes the fallback. Users wanting v0.9 behaviour set `providers: [nominatim]`. Documented in §7.2 and §19.
19. **G-NAF Core, not full G-NAF.** *Decision: use Geoscape's simplified single-table G-NAF Core distribution.* Same coverage (~15.86 M addresses), much simpler schema, includes the critical `MB_CODE` field. Full G-NAF (~50 normalised tables, ~5 GB) is overkill for our use case. Documented in §4.3.
20. **DuckDB as G-NAF backend.** *Decision: use DuckDB rather than SQLite.* Columnar engine, native Parquet reads, FTS extension, embedded (no server). Better fit than SQLite for a 15 M-row analytical lookup workload. Documented in §12 and §19.
21. **Two G-NAF distribution paths, default to pre-built Parquet.** *Decision: default `mode: cache` uses [`gnaf-loader`](https://github.com/minus34/gnaf-loader) pre-built GeoParquet on AWS S3 (anonymous access).* `mode: remote` queries S3 directly without local download; `mode: official` falls back to ABS PSV for users wanting direct provenance. Documented in §4.3, §6.1, §19.
22. **Mesh-block fast path for SA2 resolution.** *Decision: when G-NAF returns an `mb_code`, resolve SA2 via the ABS-published MB→SA1→SA2 correspondence file rather than running a spatial join.* Faster, deterministic, and arguably more correct (parcel-level intelligence vs centroid-in-polygon). Spatial join remains for lat/lon inputs and Nominatim hits. Documented in §7.3 and §19.4.
23. **Tiered matching with explicit confidence.** *Decision: four match tiers (`gnaf_exact`, `gnaf_component`, `gnaf_fuzzy`, `nominatim_*`) with the tier surfaced in the output `geo_source` column.* Lets downstream feature engineering filter on geocoding quality. `gnaf_fuzzy` rows additionally carry a similarity score in `geo_match_score`. Documented in §7.2 and §8.
24. **Lightweight rules-based normaliser for v1; no NLP-parser extras.** *Decision: ship a rules-based AU normaliser (`geocoding/normalize.py`) handling AS4590 street-type abbreviations, state abbreviations, postcode extraction, punctuation/whitespace.* `address-net` and `libpostal` are *not* shipped as optional extras — both have system-level prerequisites (TensorFlow, libpostal C library) that conflict with the goal of a clean `pip install -e .`. They remain pluggable via the `Normalizer` interface (§13) for users who want to take on those dependencies themselves. Documented in §12 and §13.
25. **G-NAF release pinning.** *Decision: `geocoding.gnaf.release: latest` resolves to the most recent quarterly release at first fetch and is then recorded in a resolved-config snapshot.* Re-runs are reproducible without forcing users to specify `202602` etc explicitly; an explicit value pins the release indefinitely. Documented in §6.1.
26. **License attribution.** *Decision: G-NAF attribution lives in the README, not in output files. Also printed by `tools/fetch_real_data.py` when it downloads G-NAF.* The Open G-NAF EULA requires attribution but doesn't mandate per-output stamping. README mention plus the on-download attribution print is sufficient for a data-science tool whose output is intermediate. Documented in §19.5.
27. **`GeocodeResult` extended with `mb_code` / `match_quality` / `match_score`.** *Decision: extend the existing dataclass rather than introduce a parallel type.* Backwards-compatible for the field set (new fields default to `None`). Documented in §19.1.
28. **v1.0 is a breaking change from v0.9 in the output schema.** *Decision: bump major version; document in CHANGELOG; provide upgrade note.* `geo_source` enum has changed values; two new columns (`geo_match_score`, `sa2_resolution`) appear. The internal Python API (`Pipeline.augment(df)` etc.) stays mostly compatible; the breakage is in the *file format*. Documented in §8 and `CHANGELOG.md`.
29. **MB → SA2 correspondence is the .dbf attribute table of the Mesh Block shapefile.** *Decision: build the MB→SA2 lookup dict by reading the `.dbf` of `MB_{year}_AUST_SHP_{datum}.zip` (downloaded from the same Digital Boundary Files endpoint as SA2 boundaries), not from the ABS *correspondences* page.* HEAD-checks confirmed that the correspondences page hosts only **change files** between ASGS editions (e.g. 2016→2021 transitions) — it has no within-edition hierarchy lookups. The Mesh Block shapefile carries `MB_CODE21`, `SA2_CODE21`, `SA2_NAME21` columns, which is exactly what we need; reading attributes only (via `pyogrio.read_dataframe(read_geometry=False)`) keeps the cost cheap. Resolves former §15.1. Documented in §4.2, §15.1, §17, §19.4.

**v1.4 additions (PRESET pipeline integration):**

30. **PRESET integration into `CensusEnricher`, not a separate pipeline stage.** *Decision: handle `PRESET.<id>` refs inside `CensusEnricher.build_lookup()` by expanding them into synthetic source-column entries that the existing GCP / registered-dataset dispatch already knows how to fetch.* The alternative — a separate post-enrichment "feature stage" — would have duplicated dataset-loading logic and made dedupe across PRESETs harder. Putting it inside the enricher means: one source-fetch path for everyone, one `_build_gcp_lookup`-level grouping that already de-dupes per-table loads, and PRESETs evaluate against the same DataFrame the rest of the dispatch produces. Synthetic source columns use the reserved prefix `__preset_src__` and are dropped from the final lookup. Documented in §21.2.
31. **Auto-load source columns rather than require users to request them.** *Decision: when a config asks for `pct_renters: PRESET.pct_renters`, the enricher walks `spec.source_fields()` and adds the underlying refs (`G37.R_Tot`, `G37.OPDs_Total`) to the load set transparently.* Forcing users to also list source columns would have been redundant — the spec already encodes them — and brittle (renaming a PRESET's denominator would silently break configs). v1.3 required users to do this manually because integration was not yet in scope; v1.4 closes that gap.

**v1.4.1 additions (wheel packaging):**

32. **Bundle dataset / feature spec markdown into the wheel via hatchling `force-include`.** *Decision: copy `datasets/*.md` and `features/*.md` into the built wheel under `census_augment/datasets/_specs/` and `census_augment/_features/` respectively at build time.* The pluggable framework's content lives in markdown spec files at the repo root, outside the package directory; with the default hatchling `packages = ["src/census_augment"]` config, those files never made it into the wheel — so a real `pip install abs-census-augmentor @ git+...` produced a working framework with empty registries. The runtime resolver in `_default_spec_dir()` / `_default_features_dir()` already looked in the right wheel-internal locations; the build just needed to put the files there. Verified end-to-end with a fresh isolated venv install. Closes #19. Documented in `CHANGELOG.md` under v1.4.1.

**Devcontainer tooling additions:**

33. **No host Docker / Podman socket bind in the devcontainer.** *Decision: drop the `ghcr.io/devcontainers/features/docker-outside-of-docker` feature; the devcontainer is now host-runtime agnostic and works under Docker Desktop, Podman Desktop, Colima, etc. with no project-side changes.* The earlier rationale for the socket bind was "so `tools/demo/render.sh --docker` works from inside the container". But nothing in `src/`, `tests/`, or CI talks to Docker; `render.sh`'s default path inside the devcontainer is `--local` (post-create installs native VHS); and the `--docker` mode is a maintainer-only diagnostic for exercising `tools/demo/Dockerfile`, which is easier to run from the host where the runtime CLI already lives. Removing the bind also resolves a Podman migration footgun: the feature hard-coded `/var/run/docker.sock`, which Podman exposes at a different path, so the mount created a broken-socket file that confused `docker ps` failures. Verification: inside the devcontainer, `command -v docker` returns nothing and `./tools/demo/render.sh` (default `--local`) produces a working GIF. Documented in `.devcontainer/README.md` "Why no Docker socket?".

## 15. Open Questions

1. *(Resolved — see §14 decision #29.)* **MB → SA2 correspondence file source.** Originally listed as open: ABS's correspondence page only hosts *change files* (e.g. 2016→2021 transitions), not within-edition hierarchy lookups. The MB→SA2 mapping lives in the **`.dbf` attribute table of the Mesh Block shapefile** (`MB_2021_AUST_SHP_GDA2020.zip`), downloaded from the same Digital Boundary Files endpoint as SA2 boundaries (§4.1). Implementation reads only the .dbf columns (no geometry) for cheap O(1) lookup table construction.

---

## 16. Acceptance Criteria for v1

The implementation is considered done when:

- A user can run `census-augment run --config config.example.yaml` end-to-end on a sample input of mixed addresses + coordinates and produce an enriched CSV.
- All boundary, DataPack, and G-NAF files are downloaded automatically on first run.
- G-NAF Tier 1 (exact `ADDRESS_LABEL`) match works on a sample of 50+ well-formed AU addresses with ≥95% hit rate, exercised by `tools/verify_real_parsers.py`.
- G-NAF Tiers 2–3 are exercised by deliberately malformed test fixtures.
- Nominatim fallback is exercised by addresses guaranteed to miss G-NAF (e.g. PO boxes, business names without addresses).
- Mesh-block fast path is exercised: a G-NAF-matched row produces a `sa2_code` without the spatial-join code path being entered.
- Geocoding cache is populated and reused on the second run for Nominatim hits.
- `census-augment discover --search "income"` returns matching census variables.
- `census-augment gnaf-info` reports the resolved G-NAF release, mode, on-disk path, and size.
- Config errors (bad variable references, missing input columns, missing user agent when Nominatim is enabled) produce clear, actionable error messages.
- Test suite covers: config validation, cache hit/miss, G-NAF tier behaviour on synthetic fixtures, MB→SA2 lookup correctness, spatial join correctness, end-to-end pipeline on a tiny dataset.
- **Library use:** `pipeline.augment(df)` produces an enriched DataFrame and an `AugmentResult` (with run summary including per-tier counts) without touching the filesystem beyond the shared user cache.

---

## 17. Real-data verification

The pytest suite is **hermetic**: every external interaction (Nominatim, ABS downloads, G-NAF S3) is mocked. To validate that parsers and matchers work against the **real** endpoints and files:

- **`tools/fetch_real_data.py`** downloads real boundary ZIP, DataPack ZIP, and G-NAF Parquet into the configured cache. Uses the actual production data-source classes. Prints the G-NAF attribution string (per §19.5) when it downloads G-NAF.
- **`tools/verify_real_parsers.py`** runs the parsers and the G-NAF matcher (against a small known-good address set — see §16's 95% hit rate criterion) against locally-cached real files and prints a tick/cross summary.

**Test fixtures (synthetic).** The hermetic test suite uses synthetic fixtures generated in `tests/conftest.py`, never binary blobs in the repo:

- **SA2 boundaries:** ~3 polygons covering known Sydney/Melbourne areas in EPSG:7844.
- **DataPack:** a small in-memory ZIP with G01.csv + G02.csv + a synthesised metadata Excel matching the real ABS layout (title rows, `Cell Descriptors Information` sheet, etc.).
- **G-NAF:** a small in-memory Parquet (~50 addresses) covering the test SA2 polygons and exercising all four match tiers (exact, component, fuzzy, miss). Includes representative `MB_CODE` values that resolve via the MB→SA2 correspondence fixture.
- **MB→SA2 correspondence:** a synthetic Mesh Block shapefile (5 mesh blocks across 3 SA2s) with ABS's `MB_CODE21` / `SA2_CODE21` / `SA2_NAME21` column names. The fixture's mesh-block codes line up with the G-NAF fixture's `MB_CODE` values so end-to-end MB-fast-path tests round-trip.

When to run the real-data scripts: after dev environment setup; after ABS or Geoscape publishes new versions; whenever code touches the parsers or matcher.

---

## 18. Library use

The same `Pipeline` class supports two entry points:

- **`pipeline.run()`** — reads `config.input.path`, writes `config.output.path`, returns a `RunSummary`. Used by the CLI's `run` command.
- **`pipeline.augment(df)`** — takes a DataFrame, returns an `AugmentResult` (the augmented DataFrame plus typed per-row classification masks and the run summary). No file I/O.

### 18.1 Constructing a pipeline

```python
from census_augment import Pipeline, Config, load_config

# A. Notebook-friendly factory
pipeline = Pipeline.create(
    variables={"median_age": "G02.Median_age_persons"},
    user_agent="my-app/1.0 (me@example.com)",
    latitude_column="lat",
    longitude_column="lon",
)

# B. Programmatic Config
cfg = Config(input=..., output=..., census=..., ...)
pipeline = Pipeline.from_config(cfg)

# C. From YAML
pipeline = Pipeline.from_config(load_config("config.yaml"))
```

### 18.2 The `augment` method

```python
result = pipeline.augment(
    df,
    address_column="street",
    latitude_column="latitude",
    longitude_column="longitude",
)
```

Override semantics: omit (use config), pass `"col"` (use that column), pass `None` explicitly (disable locator). Lenient column resolution: missing configured columns are dropped with a WARNING and surfaced on `result.summary.unused_configured_columns`.

`AugmentResult` fields: `df`, `summary` (now including per-tier geocoding counts), `added_columns`, `is_fully_enriched`, `geocoding_failed`, `sa2_unmatched`.

### 18.3 Cache directories

Per §9. Default user-level cache is shared across notebooks and runs.

### 18.4 Public API

```python
from census_augment import (
    Pipeline, AugmentResult, RunSummary,
    Config, InputConfig, OutputConfig, CensusConfig,
    DataSourcesConfig, GeocodingConfig, GnafConfig, NominatimConfig,
    load_config,
    VariableCatalog, CatalogError,
    Geocoder, GeocodeResult,
)
```

---

## 19. G-NAF Geocoder

This section details the G-NAF geocoder added in v1.0. It implements the `Geocoder` Protocol so it composes naturally with future geocoders.

### 19.1 Why G-NAF

- **Authoritative.** Geoscape's geocoded address index for Australia, derived from ~50 million contributed addresses across state/territory land records and Commonwealth agencies, distilled into ~15.86 million current addresses. This is the same dataset used by AusPost, emergency services, and most government systems.
- **Offline.** Once downloaded, all geocoding is local — no rate limits, no network jitter.
- **Includes mesh-block code.** G-NAF Core's `MB_CODE` is the ABS Mesh Block identifier, which deterministically rolls up to SA1, SA2, SA3, SA4 via the ASGS hierarchy. This bypasses the spatial join entirely for matched addresses.
- **Free.** Distributed under the Open G-NAF EULA (CC BY 4.0 with mail-use restriction; not relevant to geocoding).

> **`GeocodeResult` extension.** To carry the new information, the existing `GeocodeResult` dataclass gains three optional fields:
>
> - `mb_code: str | None` — 11-digit ABS Mesh Block identifier when the geocoder can produce one (G-NAF can; Nominatim cannot).
> - `match_quality: str | None` — one of `gnaf_exact`, `gnaf_component`, `gnaf_fuzzy`, `nominatim_cache`, `nominatim_fresh`, or `failed`. Becomes the `geo_source` value in the output (§8).
> - `match_score: float | None` — Tier 3 fuzzy similarity score in `[0.0, 1.0]`. Null for non-fuzzy matches.
>
> All three default to `None`, so existing code paths that don't populate them stay backwards-compatible internally. Output consumers see the new schema documented in §8.

### 19.2 Distribution and modes

Configured via `geocoding.gnaf.mode`:

| Mode | What happens | When to use |
|---|---|---|
| `remote` | DuckDB queries pre-built GeoParquet on `s3://minus34.com/opendata/geoscape-{YYYYMM}/geoparquet/` directly (anonymous, no download). | Quick prototyping; CI; environments with reliable network and limited disk. |
| `cache` *(default)* | First run downloads GeoParquet from the same S3 path into `<data_dir>/gnaf/{release}/`; subsequent runs query locally. | Most users — one-time ~500 MB download, then offline forever. |
| `official` | Downloads official PSV from data.gov.au, parses, and builds a local DuckDB. Larger and slower (PSV → load → index ~10 minutes), but provenance is direct from ABS/Geoscape with no third-party intermediary. | Users with provenance / supply-chain concerns about third-party pre-built data. |

The `release` field accepts `latest` (resolves at fetch time and is then recorded) or an explicit `YYYYMM` like `202602`.

### 19.3 Matching tiers

Implemented as four cascading attempts, each only run if previous tiers missed:

1. **Tier 1 — exact `ADDRESS_LABEL`.** The input is normalised (uppercase, whitespace collapsed, AS4590 street-type abbreviations expanded, state abbreviations expanded, punctuation stripped) and equality-matched against G-NAF's `ADDRESS_LABEL`. ~70–80% hit rate on well-formed inputs.
2. **Tier 2 — component match.** The input is parsed into `(unit_number, street_number, street_name, street_type, locality, state, postcode)` using a rules-based AU normaliser. Each component is exact-matched, with postcode (or postcode + state) used as a pre-filter to keep candidate sets small. Catches cases where input field order or punctuation differs from `ADDRESS_LABEL`.
3. **Tier 3 — FTS / fuzzy.** Within a candidate set pre-filtered by postcode (or locality if no postcode), score candidates against the input using DuckDB's FTS extension and `rapidfuzz` token-set ratio. Best-scoring candidate above `fuzzy_threshold` wins. Catches typos, alternative spellings, abbreviation mismatches the rules-based normaliser missed. Records the score in `geo_match_score`.
4. **Tier 4 — Nominatim.** Out-of-band: handled by the next provider in `geocoding.providers`, not by the G-NAF geocoder itself.

The output `geo_source` column distinguishes which tier matched (`gnaf_exact`, `gnaf_component`, `gnaf_fuzzy`).

### 19.4 Mesh-block to SA2 correspondence

When G-NAF returns a match, the result carries an `mb_code` (11-digit ABS Mesh Block). We resolve SA2 from this via:

- **Source:** the **`.dbf` attribute table of the ABS Mesh Block shapefile** (`MB_{year}_AUST_SHP_{datum}.zip`), downloaded from the same Digital Boundary Files endpoint as SA2 boundaries (§4.1). The ABS *correspondences* page only hosts cross-edition *change files* (e.g. 2016→2021 transitions); within-edition hierarchy lookups live in the boundary shapefiles themselves. Resolved §15.1.
- **Loading:** read only the .dbf attribute columns via `pyogrio.read_dataframe(read_geometry=False)` — no geometry parsing, fast on a ~100 MB shapefile.
- **Lookup:** loaded into a dict keyed by `mb_code` (mapping to `(sa2_code, sa2_name)`); lookup is O(1).
- **Column resolution:** ABS's column names are year-suffixed (`MB_CODE21`, `SA2_CODE21`, `SA2_NAME21` for the shapefile; `MB_CODE_2021`, `SA2_MAINCODE_2021`, `SA2_NAME_2021` for the CSV variant). The parser detects both forms and picks the highest-year-suffixed column when multiple coexist (spec §13 extensibility hook).
- **Vintage:** Mesh blocks have a vintage (e.g. `MB_2021`). G-NAF Core's `MB_CODE` is currently the 2021 vintage. When ABS publishes 2026 mesh blocks alongside the 2026 Census, this becomes a config-driven choice.

If `mb_code` is null on a G-NAF row (rare but possible for very recent additions), the pipeline falls back to the spatial-join path using G-NAF's lat/lon. This is logged at INFO and counted in the run summary.

### 19.5 License and attribution

G-NAF is licensed under the Open G-NAF End User Licence Agreement, based on CC BY 4.0 with one important restriction: the data must not be used for the generation or compilation of addresses for the sending of mail unless the user has verified each address against a secondary source. This tool does not generate mail-targeted addresses; it geocodes existing inputs and attaches statistical aggregates. The restriction does not constrain our use case.

The README must include the attribution string:

> Incorporates or developed using G-NAF © Geoscape Australia licensed by the Commonwealth of Australia under the Open Geo-coded National Address File (G-NAF) End User Licence Agreement.

`tools/fetch_real_data.py` also prints this string when it downloads G-NAF, so first-time users see it explicitly. Per-output-file attribution is not required for our use case.

### 19.6 Performance expectations

For the target scale (a few hundred rows per run):

- First run with `mode: cache`: ~30–60 seconds for the one-time S3 download (depends on network).
- Subsequent runs: G-NAF lookup latency is essentially zero. Total run time dominated by Nominatim fallback for un-matched addresses (1 req/sec).
- On a few-thousand-row run with mostly well-formed inputs: expect Tier 1 to handle ~80%+ instantly, with the residual 20% spread across Tiers 2–4. Total runtime is bounded by the Tier 4 fall-through size × 1 second.
- **For runs configured `providers: [gnaf]` (no Nominatim fallback) — or where every row matches G-NAF — there is no per-row network cost at all.** Tens of thousands of rows complete in seconds. This is the v1.0 unlock over v0.9, where Nominatim's 1 req/sec policy capped practical throughput at a few hundred rows.

---

## 20. Pluggable Datasets (v1.3)

The pipeline keeps the same shape — geocode → SA2 resolve → enrich — but the
*enrich* stage now dispatches across a registry of datasets rather than
hard-coding the GCP DataPack.

```
Input CSV
   │
   ▼
Geocoding (G-NAF tiered → Nominatim)
   │
   ▼
SA2 Resolution (MB fast path → spatial fallback)
   │
   ▼
Dataset Enrichment ─── for each requested variable, look up which
                       registered dataset provides it; fetch (cached);
                       join on sa2_code_2021; attach.
   │                   ┌── gcp             (G01..G62; 2016 + 2021)
   │                   ├── seifa     (SEIFA.*)
   │                   ├── erp_by_sa2     (ERP.*)
   │                   ├── dss_payments   (DSS.*)
   │                   └── abs_personal_income (ABS_PIA.*)
   ▼
Feature Derivation ─── for each PRESET feature, compute the curated
                       ratio with the right denominator (§21).
   │
   ▼
Output CSV
```

### 20.1 Dataset spec format

One file per dataset, lives at `datasets/<id>.md`. YAML front-matter holds
machine-parseable metadata; markdown body holds rationale, schema, and
fetch notes.

```markdown
---
id: <stable_snake_case_id>
name: <human-readable name>
status: proposed | active | deprecated
custodian: <organisation>
licence: <SPDX-style id>
update_cadence: annual | quarterly | monthly | adhoc | one-shot
geography_level: SA2
geography_edition: 2021_ASGS_Edition_3
geography_native: true | false
join_key: sa2_code_2021
landing_page: <URL>
fetch_size_compressed: <approximate, for cache budgeting>
tags: [<freeform tags>]
namespace: <prefix used in Pipeline.variables, e.g. SEIFA>
---

# <name>

<one-paragraph description>

## Source / Update cadence / Granularity / Schema / Fetch notes / etc.
```

The full template is at `datasets/_template.md`.

### 20.2 Registry

`src/census_augment/datasets/_registry.py` parses every `datasets/*.md` file
on import and indexes by `namespace` and by `id`. Variable resolution checks
the registry first (e.g. `SEIFA.irsd_decile` → `seifa` dataset →
field `irsd_decile`); falls back to the existing GCP catalog for anything
matching the `<TABLE_ID>.<column>` shape.

Programmatic API:

```python
from census_augment.datasets import registry

registry.list_datasets()           # all registered datasets
registry.get("seifa")         # single dataset by id
registry.resolve_variable("SEIFA.irsd_decile")
                                   # -> (dataset, field) tuple
```

### 20.3 Per-dataset fetcher

Each registered dataset implements a small fetcher class with the same
shape as `BoundariesDataSource` / `DataPacksDataSource`:

```python
class DatasetFetcher(Protocol):
    def fetch(self, refresh: bool = False) -> Path: ...
    def load(self) -> pd.DataFrame: ...   # SA2-keyed
```

The fetcher's `load()` returns a DataFrame indexed by `sa2_code_2021`
exposing the columns the spec declares. The pipeline only needs the
DataFrame to do the join — no per-dataset code touches the enrichment path.

### 20.4 Release pinning

Each dataset has a release identifier (year, quarter, monthly, ...).
`Pipeline.create(...)` accepts a `releases` dict for reproducibility:

```python
pipeline = Pipeline.create(
    releases={
        "dss_payments": "2024-Q4",
        "erp_by_sa2": 2024,
    },
)
```

Default behaviour: fetch the latest available release per dataset. Cache
keys include the resolved release identifier so a re-run after a new
release drops doesn't silently keep returning stale data.

### 20.5 Discovery

`census-augment discover` extends with dataset and feature flags:

```bash
census-augment discover --datasets                # list all registered datasets
census-augment discover --dataset seifa      # show schema of one
census-augment discover --search income           # search across all variables
```

### 20.6 Initial registry (v1.3)

| id | namespace | source | cadence | size | status |
|---|---|---|---|---|---|
| `gcp` | `G01..G62` | ABS GCP DataPack (2016 + 2021 releases) | per-census | ~35-40 MB / release | active (2016 in v1.6 / F.4) |
| `seifa` | `SEIFA` | ABS SEIFA SA2 workbook (2016 .xls, 2021 .xlsx) | per-census | ~150-700 KB | active (v1.3 / 2016 in v1.5) |
| `erp_by_sa2` | `ERP` | ABS Regional Population XLSX | annual | ~3 MB | active (v1.3) |
| `dss_payments` | `DSS` | DSS data.gov.au CKAN | quarterly | ~5 MB / quarter | active (v1.3) |
| `ato_personal_income` | `ATO` | ABS Personal Income XLSX | annual | ~4 MB | active (v1.3) |

### 20.7 Deferred backlog

Tracked but not in v1.3 scope:

- **Non-SA2-native (sub-SA2 or cross-SA2 aggregation):** AIHW Health Atlases (SA3-native), ABS Building Approvals (LGA-native), Geoscape Buildings (point-level), ABS National Health Survey (state/capital city level only).
- **Single-state datasets:** NSW BOCSAR, VIC Crime Statistics, NSW Education NAPLAN, state land-titles. State-by-state stitching is a separate engineering problem.
- **Licensing / effort:** CommBank Spending Insights (proprietary), AEC polling-place data (booth-to-SA2 aggregation methodology choice non-trivial).
- **Scope (different tool):** BoM weather / climate (station-keyed; sibling tool `abs-weather-augmentor`).

---

## 21. Derived Features (PRESETs) (v1.3, pipeline-integrated in v1.4)

Curated ratios that combine variables into a single output, with the right
denominator pre-baked. The motivating problem: every downstream consumer
re-derives `pct_drive_to_work` etc., and the denominator choice is the
silent failure mode (e.g. dividing employed-driving-to-work by total
population instead of total-employed-15+ under-states by 30+ percentage
points).

### 21.1 Feature spec format

One file per feature, lives at `features/<id>.md`. Same YAML-front-matter
+ markdown-body shape as datasets (§20.1):

```markdown
---
id: pct_drive_to_work
status: proposed | active | deprecated
output_kind: percentage | ratio | rate | scalar | index
bounds: [0, 100]
dataset: gcp
default: false
tags: [transport, employment]
numerator:
  expression: field | sum | weighted_sum
  fields:
    - <namespace>.<field>
    - <namespace>.<field>
denominator:
  expression: field | sum
  field: <namespace>.<field>
edge_cases:
  zero_denominator: null | zero | error
  perturbation_tolerance: warn_only | strict
  out_of_bounds_behaviour: clip | warn | error
sources:
  - url: <URL>
    note: <citation context>
---

# <feature_id>

<one-paragraph description>

## Why this denominator / Why not <obvious-but-wrong> / Edge cases / Bounds / Sources
```

### 21.2 Variable reference

Features are referenced via the `PRESET.<id>` namespace alongside any
other variable namespace:

```yaml
variables:
  pop_total:           G01.Tot_P_P
  pct_drive_to_work:   PRESET.pct_drive_to_work
  pct_renters:         PRESET.pct_renters
  irsd_decile:         SEIFA.irsd_aus_decile
```

**v1.4 pipeline integration.** `CensusEnricher.build_lookup()` recognises
`PRESET.<id>` directly. For each PRESET it:

1. Looks the id up in the `FeatureRegistry`.
2. Walks numerator + denominator to collect every underlying source
   ref (`spec.source_fields()`).
3. Auto-loads those sources through the existing GCP / registered-dataset
   dispatch — deduplicated across PRESETs, so two PRESETs sharing
   `G01.Tot_P_P` only fetch G01 once.
4. Runs `FeatureEvaluator` against a workspace DataFrame that has the
   source columns under their bare `<NAMESPACE>.<field>` names.
5. Surfaces the result as `<output_prefix><friendly>` and drops the
   synthetic source columns from the final lookup.

The standalone `FeatureEvaluator` API (v1.3) is unchanged and still
available for analysis code that has its own SA2-keyed DataFrame and
doesn't need geocoding.

### 21.3 Edge case rules

- `zero_denominator: null` (default) → output column is null when the denominator
  is zero. `zero` and `error` modes are available for callers who want
  different semantics.
- **Suppressed source counts** (ABS perturbation, DSS small-cell
  suppression) → propagate as null. Don't substitute a midpoint; surface a
  WARNING once per feature per release.
- **Bounds:** `clip` clamps to the declared bounds, `warn` (default) logs a
  WARNING when out of bounds, `error` raises. `bounds: warn` is the right
  default because clipping silently masks denominator-mismatch bugs.

### 21.4 PRESET catalogue

GCP-only features (the originals that motivated this design):

- `pct_drive_to_work` — sum of G62 motor-vehicle modes / G62.Tot_P
- `motor_vehicles_per_dwelling` — G34.Total_motor_vehicles / G34.Total_dwellings
- `pct_renters` — G37.R_Tot / G37.OPDs_Total
- `pct_employed_full_time` — G43 employed-FT / G43 labour-force-15+
- `pct_aged_65_plus` — G04 aged-65+ / G01.Tot_P_P
- `pct_one_parent_family` — G29 one-parent-with-kids / G29 total-families-with-kids

Cross-dataset features (sourced from `dss_payments` + `erp_by_sa2`),
landed once the ERP age/sex columns shipped (see CHANGELOG entry for
"ERP wishlist"):

- `pct_age_pension_recipients` — DSS.age_pension_recipients / ERP.population_65_plus
- `pct_jobseeker_recipients` — DSS.jobseeker_payment_recipients / ERP.population_15_64
- `pct_disability_support_pension_recipients` — DSS.disability_support_pension_recipients / ERP.population_15_64
- `pct_parenting_payment_recipients` — sum of DSS parenting-payment streams / ERP.population_15_64
- `pct_youth_allowance_recipients` — sum of DSS youth-allowance streams / ERP.population_15_64
- `pct_commonwealth_rent_assistance_recipients` — DSS.commonwealth_rent_assistance_recipients / ERP.population_total
- `pct_carer_payment_recipients` — DSS.carer_payment_recipients / ERP.population_15_64
- `welfare_density_index` — sum of nine DSS payment-type recipient counts / ERP.population_total

These exercise the `dataset:` front-matter accepting a list — the
namespace-based dispatch in `enrich.py` fans out to multiple
registered fetchers transparently.

### 21.5 Versioning to GCP release

Field codes change between Census releases (2016 vs 2021 differ). Feature
specs declare the dataset they're sourced from via the `dataset:` front-matter
field; that pins them to a specific GCP release. A future 2026 PRESET catalogue
would land as `features/2026/pct_drive_to_work.md` referencing
`dataset: gcp_2026`.

---

## 22. Migration notes for v1.0 → current (v1.4.1)

The releases since v1.0 are **all additive** for the common path: existing
`Pipeline.augment(df, variables={"median_age": "G02.Median_age_persons"})`
configurations continue to work. The variable string `<TABLE>.<column>`
still resolves through the `gcp` registered dataset, which exposes
the same fetcher and parser as before. The output schema also stays
stable from v1.0 onwards (the breaking change was the v0.9 → v1.0 step
documented in §14 #28).

### 22.1 What's new (additive across v1.1 – v1.4.1)

- **v1.1 / v1.2.x.** G-NAF Core as the primary geocoder; tiered match
  surface (`gnaf_exact` → `gnaf_component` → `gnaf_fuzzy` →
  `nominatim_*`); MB-fast-path SA2 resolution; output gains
  `geo_match_score` and `sa2_resolution` columns.
- **v1.3.** Pluggable dataset registry. New variable namespaces:
  `SEIFA.*`, `ERP.*`, `DSS.*`, `ATO.*`, `PRESET.*`. New CLI flags on
  `census-augment discover`: `--datasets` (list registered datasets),
  `--dataset <id>` (show one dataset's schema), `--features` (list
  PRESET catalogue). New constructor kwargs on `Pipeline.create`:
  `releases={...}` for pinning. Standalone `FeatureEvaluator` for
  analysis code that has its own SA2-keyed DataFrame.
- **v1.4.** `PRESET.<id>` is now first-class in any config — the
  pipeline auto-loads each PRESET's source columns and runs
  `FeatureEvaluator` transparently. New helper:
  `FeatureSpec.source_fields()` for downstream tooling that wants to
  inspect a PRESET's source dependencies.
- **v1.4.1.** Wheel-install fix only. No API surface change. The
  `pyproject.toml` build config now bundles `datasets/*.md` and
  `features/*.md` into the wheel under
  `census_augment/datasets/_specs/` and `census_augment/_features/` so
  registries populate on `pip install ...@git+...` (not just source
  checkouts). See §14 #32.

### 22.2 Breaking changes

None since v1.0. Internal refactors during v1.3 (the dispatch in
`CensusEnricher`) and v1.4 (PRESET expansion in `build_lookup`) are
invisible to library and CLI callers. Anything that ever surfaces is
documented in `CHANGELOG.md`.

### 22.3 Removed

Nothing.
