# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

For *design* decisions and rationale, see [`spec.md`](spec.md) §14
(Resolved Decisions). This changelog is the user-facing complement.

## [Unreleased]

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

[Unreleased]: https://github.com/cauldnz/abs-census-augmentor/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/cauldnz/abs-census-augmentor/releases/tag/v0.1.0
