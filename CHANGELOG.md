# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

For *design* decisions and rationale, see [`spec.md`](spec.md) §14
(Resolved Decisions). This changelog is the user-facing complement.

## [Unreleased]

## [1.1.0] - 2026-05-06

### Added

- **Anonymous S3 fetch for G-NAF.** `census-augment fetch --gnaf` now
  actually downloads the `gnaf-loader` GeoParquet snapshot from
  `s3://minus34.com/opendata/` (configurable). v1.0 left this as a
  manual `aws s3 sync --no-sign-request ...` step; the same operation
  is now built in. ~10 GB across ~50 parquet files; idempotent on
  re-run (skips files already on disk, byte-for-byte).
- **`release: "latest"` resolves against S3** when there's no local
  cache. Pick up newly-published quarterly releases without editing
  the config.
- **`fetch --gnaf --refresh` re-checks S3 for newer releases** even
  when a cache exists, so a quarterly drop is one command away.
- **`tools/fetch_real_data.py` now downloads G-NAF too** (with a
  `--skip-gnaf` flag for iterating on the smaller paths).

### Changed

- README's "G-NAF setup" section trimmed: no more "you must populate
  the cache yourself" instructions. The auto-download path is the
  primary flow; bring-your-own-parquet is documented as a fallback.
- `census-augment fetch --gnaf` help text reflects the new behaviour.

### Internals

- `moto[s3]>=5` added to dev dependencies; new tests (`test_gnaf.py`)
  cover S3 listing, atomic-write, partial-resume, and the
  cache-then-fallback-to-S3 logic. Hermetic — no real network.
- New `_parse_s3_url`, `_list_releases_on_s3`,
  `_download_release_from_s3` and `_download_one` helpers on
  `GnafDataSource`. `_make_s3_client` uses anonymous `botocore`
  config (`signature_version=UNSIGNED`).

## [1.0.0] - 2026-05-02

v1.0 implements `spec.md` v1.0 — G-NAF as the primary geocoder with the
Nominatim fallback, and a mesh-block fast path for SA2 resolution.

### ⚠️ Breaking changes (output schema + config)

The output CSV's `geo_source` column now uses provider-prefixed values:

| Before (v0.1) | After (v1.0)                                                 |
| ------------- | ------------------------------------------------------------ |
| `input`       | `input` (unchanged)                                          |
| `cache`       | `nominatim_cache`                                            |
| `fresh`       | `nominatim_fresh` (or `gnaf_exact` / `gnaf_component` / `gnaf_fuzzy`) |
| `failed`      | `failed` (unchanged)                                         |

Two new columns appear in the output CSV: `geo_match_score` (populated
for `gnaf_fuzzy` rows) and `sa2_resolution` (`mb_code` |
`spatial_join` | `unmatched`).

The `geocoding:` section of `config.yaml` has restructured. Old:

```yaml
geocoding:
  provider: nominatim
  user_agent: "..."
  rate_limit_per_second: 1
  cache_enabled: true
```

New (v1.0):

```yaml
geocoding:
  providers: [gnaf, nominatim]
  cache_enabled: true
  gnaf:
    mode: cache
    release: latest
    datum: GDA2020
    fuzzy_threshold: 0.85
  nominatim:
    user_agent: "..."
    rate_limit_per_second: 1
```

To reproduce v0.1 behaviour without G-NAF: `providers: [nominatim]`
plus the new `nominatim:` subsection.

### Added

#### Geocoding
- **G-NAF Core integration** as the primary geocoder. Three offline
  match tiers (spec §19.3):
  - `gnaf_exact` — exact `ADDRESS_LABEL` match after normalisation.
  - `gnaf_component` — postcode-pre-filtered substring match on the
    canonical `<num> <street> <type>` form.
  - `gnaf_fuzzy` — `rapidfuzz` token-set similarity above a configurable
    `fuzzy_threshold`, with the score recorded in `geo_match_score`.
- Cascading provider chain (spec §7.2): `geocoding.providers` is an
  ordered list; first non-failed result wins.
- AU address normaliser (rules-based, no NLP dependencies). Handles
  AS4590 street-type abbreviations (ST→STREET etc.), state names,
  punctuation, casing.
- DuckDB-backed lookup with native Parquet reads. ~15.86 M addresses
  queried via local GeoParquet files.
- New CLI command: `census-augment gnaf-info` reports the resolved
  release, mode, on-disk path, and cache size.
- New CLI flag: `census-augment fetch --gnaf` validates the cache
  layout and downloads the Mesh Block correspondence shapefile.

#### MB → SA2 fast path (spec §7.3)
- When G-NAF returns an `mb_code`, SA2 is resolved via an O(1) dict
  lookup against the Mesh Block shapefile's `.dbf` attribute table —
  bypassing the spatial-join entirely. Resolution path is recorded
  per-row in the new `sa2_resolution` column.
- Lat/lon-input rows and Nominatim-resolved rows continue using the
  spatial-join fallback.
- `MbCorrespondenceDataSource` builds the lookup lazily by reading
  only the .dbf attribute columns via
  `pyogrio.read_dataframe(read_geometry=False)` — no geometry parsing.

#### RunSummary (spec §7.5)
- Per-tier histogram (`geo_per_tier`) and per-resolution-path counts
  (`sa2_resolution_counts`) joined the existing aggregates.
- Human-readable summary picks up "Per-tier breakdown" and "SA2
  resolution path" sections.

#### Public API
- New top-level exports: `GnafConfig`, `NominatimConfig`.

### Dependencies
- `duckdb` — G-NAF indexing + analytical queries.
- `pyarrow` — GeoParquet I/O for G-NAF.
- `rapidfuzz` — Tier 3 fuzzy matching.
- `boto3` — anonymous S3 access for `s3://minus34.com/opendata/`
  (G-NAF distribution).
- `pyogrio` — promoted from transitive to explicit (we call it
  directly to read the Mesh Block .dbf without geometry).

### Notes
- G-NAF is licensed under Geoscape's Open G-NAF EULA. The README, the
  `tools/fetch_real_data.py` output, and `census-augment fetch --gnaf`
  all carry the required attribution string. See spec §19.5.
- `mode: remote` and `mode: official` for `geocoding.gnaf` raise
  `NotImplementedError` with migration messages — only `mode: cache`
  is shipped in v1.0. Drop GeoParquet files into
  `<data_dir>/gnaf/{YYYYMM}/` (e.g. from
  `s3://minus34.com/opendata/geoscape-{YYYYMM}/geoparquet/`) to
  populate the cache; automated S3 fetching lands in a follow-up.

## [0.1.0] - 2026-05-01

Initial release. v1 implementation against `spec.md` v0.9.

### Added

#### Library API
- `Pipeline.create()` factory for one-line notebook construction.
- `Pipeline.from_config(config)` for full programmatic / YAML-driven setup.
- `Pipeline.augment(df) -> AugmentResult` for DataFrame-in / DataFrame-out
  use without file I/O.
- `AugmentResult` dataclass with the augmented DataFrame, a `RunSummary`,
  `added_columns`, and three boolean Series (`is_fully_enriched`,
  `geocoding_failed`, `sa2_unmatched`) indexed like `df` for natural
  pandas filtering (`result.df[~result.geocoding_failed]`).
- Per-call column overrides on `augment(df)` with three-state semantics:
  omit kwarg → use config default; `"col_name"` → use that column; explicit
  `None` → disable that locator for the call.
- Lenient column resolution: configured-but-absent locator columns are
  dropped with a warning rather than raising; absent columns are listed
  on `RunSummary.unused_configured_columns`.
- Public API surface (`Pipeline`, `AugmentResult`, `RunSummary`, `Config`,
  `InputConfig`, `OutputConfig`, `CensusConfig`, `DataSourcesConfig`,
  `GeocodingConfig`, `load_config`, `VariableCatalog`, `CatalogError`,
  `Geocoder`) — see spec §18.4.

#### CLI
- `census-augment run --config <yaml>` — file-in / file-out pipeline.
- `census-augment discover --config <yaml> --search <term>` /
  `--table <id>` — search the DataPack metadata.
- `census-augment fetch --config <yaml> --boundaries [--census] [--refresh]`
  — pre-fetch ABS data.
- `census-augment validate --config <yaml> [--full]` — config validation
  (structural always; semantic against the live DataPack with `--full`).
- Global `--verbose` / `-v` toggles DEBUG logging.
- `--data-dir` / `--cache-dir` flags on every command for cache overrides.

#### Data sources & cache
- ASGS SA2 boundary download from the real ABS endpoint
  (`SA2_2021_AUST_SHP_GDA2020.zip`, ~50 MB) — Shapefile, GDA2020.
- Census 2021 GCP DataPack download
  (`2021_GCP_SA2_for_AUS_short-header.zip`, ~40 MB) — CSVs +
  metadata Excel.
- DataPack metadata parser handles real ABS Excel layout (title-row
  prefix, descriptor-mode-aware code lookup, Columnheadingdescription
  field for human-readable names).
- Default cache locations use the platform user cache via `platformdirs`
  (Linux `~/.cache/census-augment/`, macOS `~/Library/Caches/...`,
  Windows `%LOCALAPPDATA%\census-augment\Cache\`). Overridable via
  `CENSUS_AUGMENT_DATA_DIR` / `CENSUS_AUGMENT_CACHE_DIR` env vars.
- `geocoding.cache_enabled = false` now genuinely disables the cache
  (uses `NullCache` no-op).

#### Geocoding
- Nominatim implementation with rate-limit + back-off (HTTP 429/503
  exponential retry up to 3 attempts; failed lookups treated as null
  coords + flagged in summary, not cached).
- Sharded JSON cache keyed by SHA-256 of the normalised address;
  atomic writes (temp file + rename) tolerate crashes mid-write.
- Pluggable `Geocoder` Protocol for future G-NAF / Google / Mapbox
  implementations (see spec §13).

#### Variable catalog
- `VariableCatalog` with `resolve(ref)`, `validate_variables(dict)`,
  `search(term)`, `list_table(table_id)`, plus `suggest_tables` /
  `suggest_codes_in_table` (`difflib`-based near-match suggestions).
- `CatalogError` raised with helpful suggestions for typos like
  `G02.Mediaan_age` → `did you mean: Median_age_persons, ...?`.

#### Verification & docs
- 258 hermetic tests (no real network); ruff + mypy strict clean
  across 18 source files.
- `tools/fetch_real_data.py` + `tools/verify_real_parsers.py` for
  opt-in real-data verification (kept out of the test suite to keep
  CI fast and ABS-uptime-independent).
- GitHub Actions CI on Python 3.11 / 3.12 / 3.13.
- `examples/library_basic.py`, `examples/library_with_overrides.py`,
  `examples/cli/` walkthroughs verified runnable against real ABS data.
- README, CLAUDE.md, full spec.md (v0.9 with 17 logged decisions).

### Known limitations
- v1 supports SA2 only. Architecture supports SA1 / SA3 — see spec §13.
- Census 2021 GCP only. Architecture supports other years and profiles
  — see spec §13. The 2026 Census DataPack format is the next real
  test (open question §15.1).
- CSV input/output only. Other formats deferred per spec §2.
- Geocoding via Nominatim only.
- Computed/derived variables (ratios, percentages) explicitly out of
  scope per spec §14 #3 — that's downstream feature engineering.

[Unreleased]: https://github.com/cauldnz/abs-census-augmentor/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/cauldnz/abs-census-augmentor/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/cauldnz/abs-census-augmentor/releases/tag/v0.1.0
