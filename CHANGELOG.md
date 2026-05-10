# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

For *design* decisions and rationale, see [`spec.md`](spec.md) §14
(Resolved Decisions). This changelog is the user-facing complement.

## [Unreleased]

## [1.4.1] - 2026-05-10

### Fixed — wheel install ships dataset/feature spec markdown (closes #19)

v1.3 / v1.4 wheels were built with `[tool.hatch.build.targets.wheel]
packages = ["src/census_augment"]`, which copies only the `*.py` files
under the package. The 13 markdown spec files at `datasets/*.md` and
`features/*.md` (the actual content of the pluggable framework) sat at
the repo root and never made it into the wheel — so anyone installing
via `pip install abs-census-augmentor @ git+...` ended up with empty
registries:

```python
>>> from census_augment.datasets import registry
>>> list(registry.list_datasets())
[]   # should be 5: gcp_2021, seifa_2021, erp_by_sa2, dss_payments, ato_personal_income
>>> from census_augment.features import features
>>> list(features.list_features())
[]   # should be 6 PRESETs
```

Source checkouts and `pip install -e .` worked because the runtime
resolver's primary path looks at the repo-root `datasets/` /
`features/` directories. The wheel-install fallback path (private
mirrors at `<package>/datasets/_specs/` and `<package>/_features/`)
existed in the resolver code but the build didn't put any files there.

Fixed by adding `force-include` to `pyproject.toml`:

```toml
[tool.hatch.build.targets.wheel.force-include]
"datasets" = "census_augment/datasets/_specs"
"features" = "census_augment/_features"
```

The new wheel ships all 13 markdown files at the paths the resolver
already expects. No code changes needed — only the build config.

Verified end-to-end against a fresh isolated venv: `pip install
abs-census-augmentor==1.4.1` (wheel-only, no source checkout) produces
all 5 registered datasets and all 6 PRESET features.

### Added — Wheel-install regression test + CI step

`tests/test_wheel_bundles_specs.py` adds two `pyproject.toml`
lock-down checks (force-include destinations match what the runtime
resolver expects) plus one end-to-end test that builds a wheel,
installs it in a subprocess venv, and confirms both registries
populate. The E2E test is gated on `WHEEL_E2E=1` because it's slow
(~10s) — local `pytest` runs skip it, but CI sets the flag and runs
it on every push.

## [1.4.0] - 2026-05-10

### Added — PRESET features as first-class pipeline variables

`PRESET.<id>` is now a usable variable reference in any
config — alongside the existing `G\d+.<col>` (GCP), `SEIFA.<field>`,
`ERP.<field>`, `DSS.<field>`, and `ATO.<field>` namespaces.

```yaml
variables:
  pop_total:    G01.Tot_P_P
  renters_pct:  PRESET.pct_renters         # NEW: PRESET as first-class ref
  drove_pct:    PRESET.pct_drive_to_work
  irsd_decile:  SEIFA.irsd_aus_decile
```

Behind the scenes, `CensusEnricher.build_lookup()` now:

1. Detects every `PRESET.<id>` in the configured variables.
2. Looks each one up in the `FeatureRegistry` and walks
   numerator + denominator to collect the underlying source columns
   (e.g. `G37.R_Tot`, `G37.OPDs_Total` for `pct_renters`).
3. Auto-loads those source columns through the existing GCP /
   registered-dataset dispatch — deduplicated across PRESETs, so two
   PRESETs that share `G01.Tot_P_P` only fetch it once.
4. Runs `FeatureEvaluator` against the workspace and surfaces the
   derived column as `<output_prefix><friendly>` (e.g.
   `sa2_renters_pct`).
5. Drops the synthetic source columns from the result so callers see
   only the variables they explicitly asked for.

In v1.3 the same workflow was possible but required the user to
manually request the source columns and apply `FeatureEvaluator`
themselves; `examples/library_with_preset_features.py` showed the
manual recipe. v1.4 makes both `Pipeline.run()` (file in/out via the
CLI) and `Pipeline.augment(df)` (library in/out) treat
`PRESET.<id>` like any other variable namespace.

The standalone `FeatureEvaluator` / `FeatureRegistry` API is unchanged
and still available for direct use against an existing
SA2-keyed DataFrame.

### Added — `FeatureSpec.source_fields()` helper

A new `set[str]` accessor on `FeatureSpec` that returns every
`<NAMESPACE>.<field>` ref the evaluator will look up — used internally
by the pipeline integration above and useful for downstream tooling
that wants to introspect what GCP / dataset columns a PRESET depends
on.

### Verified

`tools/verify_real_parsers.py` confirmed against the live ABS /
data.gov.au endpoints that all v1.3 datasets (SEIFA, ERP, DSS, ATO)
still parse cleanly post-#16. No fixtures or schemas changed in
v1.4.

## [1.3.0] - 2026-05-09

### Fixed — G-NAF remote/cache mode against the real ``minus34.com`` bucket (closes #17)

The v1.2.2 (#9) and v1.2.3 (#14) fixes both targeted the wrong
subdirectory inside the gnaf-loader bucket. Both releases worked
against synthetic test fixtures but failed against the real bucket
with a DuckDB BinderException — the previously-targeted
``address_principal_census_<year>_boundaries/`` carries only
boundary-ID columns (``mb_code_<year>``, ``sa1_code_<year>``, ...,
``lga_code_<year>``, ``poa_code_<year>``, ...) and **no address column,
no lat/lon**.

The actual G-NAF Core data lives in ``address_principals/``, which
carries one row per address with all the columns the geocoder needs
(``gnaf_pid``, ``address``, ``latitude``, ``longitude``, ``postcode``,
``mb_2016_code``, ``mb_2021_code``). v1.3 targets that directory
correctly.

While in the area, two related fixes shipped in the same change:

- **Regional endpoint for dotted bucket names.** v1.2.2's fix for the
  ``minus34.com`` TLS-cert mismatch used path-style on the *global*
  endpoint (``https://s3.amazonaws.com/{bucket}/{key}``). That works
  for boto3 (which follows the 301 redirect to the bucket's region)
  but fails under DuckDB ``httpfs`` which doesn't follow redirects.
  v1.3 resolves the bucket's region once via ``head_bucket`` and
  uses ``https://s3.{region}.amazonaws.com/{bucket}/{key}`` directly
  (path-style, regional, no redirect needed).

- **Full ADDRESS_LABEL via component concatenation.** The
  ``address_principals.address`` column carries only the street
  portion (e.g. ``"115 LAWRENCE ROAD"``) — locality, state, and
  postcode are separate columns. The view's SELECT clause now
  ``CONCAT_WS(' ', address, locality_name, state, postcode)`` to
  produce a normalised label that matches what
  :func:`normalize_address` produces from user input. Without this,
  Tier 1 exact-match would silently miss for almost every input.

Two new regression tests use moto fixtures with the production
bucket's full sibling layout (``address_principals/`` +
``address_principal_admin_boundaries/`` +
``address_principal_census_2016_boundaries/`` +
``address_principal_census_2021_boundaries/``) and assert that the
parser picks the right subdirectory. Plus an explicit-failure test
that fails loudly when only boundary subdirectories are present
(rather than letting DuckDB surface the BinderException).

Verified end-to-end against the live ``minus34.com`` bucket
(``geoscape-202602`` release): 15,015,573 rows in the ``gnaf`` view;
sample query for "GEORGE STREET" addresses in postcode 2000 returns
correctly-formatted ADDRESS_LABEL strings with MB_CODE / lat / lon
all populated.

### Added — pluggable dataset framework (closes #15)

The pipeline (geocode → SA2 resolve → enrich) keeps the same shape, but
the *enrich* stage now dispatches across a registry of datasets rather
than hard-coding the GCP DataPack. The 2021 GCP DataPack is now one
entry in the registry alongside four new datasets:

- **`seifa_2021`** *(closes #10)* — ABS Socio-Economic Indexes for
  Areas. 4 indexes (IRSD, IRSAD, IER, IEO) × 7 flavours (score, Aus
  rank/decile/percentile, State rank/decile/percentile) + URP +
  state_abbreviation. ~2,366 SA2s, ~150 KB compressed source.
- **`erp_by_sa2`** — ABS Estimated Resident Population by SA2 (annual,
  long-history series 2001 onwards). Latest year's `population_total`
  + per-year `population_history_<year>` columns.
- **`dss_payments`** — DSS Payment Demographic Data (quarterly).
  Recipients per payment type (Age Pension, JobSeeker, etc.) with a
  `release_quarter` column carrying the YYYY-Qn snapshot identifier.
- **`ato_personal_income`** — ABS Personal Income (Table 1 SA2
  summary). Median / mean / sum total income + earners count + median
  age of earners.

Variable namespaces (`SEIFA.*`, `ERP.*`, `DSS.*`, `ATO.*`) route
through the registry. The existing GCP convention (`G02.foo`,
`G62.bar`, ...) is unchanged.

Each dataset is described by a markdown spec at `datasets/<id>.md`
with YAML front-matter declaring custodian / licence / cadence /
schema (spec §20.1). The `census-augment discover --datasets`
command lists registered datasets; `--dataset <id>` shows one's
schema.

### Added — derived features / PRESETs (closes #11)

Curated catalog of ratios with the right denominator pre-baked. Six
PRESETs ship in v1.3, each as a markdown spec at `features/<id>.md`:

- `pct_drive_to_work`, `pct_renters`, `pct_aged_65_plus`,
  `pct_employed_full_time`, `pct_one_parent_family`,
  `motor_vehicles_per_dwelling`.

The `FeatureEvaluator` (in `src/census_augment/features.py`) reads a
spec and computes the ratio against a SA2-keyed DataFrame of source
columns. Edge-case handling matches the spec's
`zero_denominator: null|zero|error` and
`out_of_bounds_behaviour: clip|warn|error` knobs. Default
`out_of_bounds_behaviour: warn` rather than `clip` so
denominator-mismatch bugs surface rather than being silently masked.

`census-augment discover --features` lists the PRESET catalogue.

### Changed

- **`CensusEnricher.build_lookup()` dispatches across datasets.**
  GCP refs (`G\d+.<col>`) keep the existing path through
  `VariableCatalog` + `DataPacksDataSource`. Non-GCP refs route to
  the registered dataset's fetcher. Mixed configs (GCP + SEIFA + ATO
  in one `variables` dict) work transparently.
- **`Pipeline.from_config()`** now pre-validates only GCP-shape
  variables against the catalog. Non-GCP variables are validated
  lazily when the enricher hits each dataset's fetcher.
- **CLI `discover`** gained `--datasets`, `--dataset <id>`, and
  `--features` flags.

### Internals

- New module: `src/census_augment/datasets/` with `_spec.py`,
  `_registry.py`, `_protocol.py`, plus per-dataset fetchers
  (`_seifa.py`, `_erp.py`, `_dss.py`, `_ato.py`).
- New module: `src/census_augment/features.py` (FeatureSpec,
  FeatureEvaluator, FeatureRegistry).
- `CensusEnricher` constructor gained an optional `data_dir`
  argument (required when non-GCP variables are present, since
  per-dataset cache directories live under it).

### Tests

501 hermetic tests pass (was 422 in v1.2.3; +79 new tests for
specs, registry, fetchers, evaluator, and dispatch). Real-network
smoke verified during development against ABS / data.gov.au:

- SEIFA real source: 2,366 SA2s, all 4 indexes parsed.
- DSS real source: 2,454 SA2s, 22 payment-type columns, latest
  release auto-resolved (2025-Q4 at time of testing).
- ERP real source: 2,454 SA2s, 25-year history series, latest
  reference year auto-detected.
- ATO real source: 2,450 SA2s, FY 2022-23 release, summary stats
  (sample SA2 "Braidwood" median income $49,963 — plausible).

### Breaking changes

v1.3 is **mostly non-breaking** for existing v1.2.x callers, but a few
internal contracts shifted in service of the registry refactor:

- `CensusEnricher.__init__()` gained an optional `data_dir` parameter.
  Existing callers without it continue to work for **GCP-only**
  variable configs. Non-GCP variables now require the parameter; an
  explicit error is raised if it's missing. v1.2.x downstreams that
  use only GCP keep working unchanged.
- `enrich.py` no longer exports a public per-table loader API — the
  registry is the public surface for variable resolution. v1.2.x
  callers that imported internal helpers from `enrich.py` (none in
  the public API) may need to migrate.

### Deferred to v1.4

- **PRESET integration into the pipeline.** v1.3 ships PRESETs as a
  standalone API (`FeatureEvaluator`); using one in the pipeline
  config requires the user to also request the underlying
  numerator/denominator source columns in the same `variables` dict.
  v1.4 will auto-load the source columns so users can write
  `variables: {pct_renters: PRESET.pct_renters}` without thinking
  about the underlying GCP fields.
- **Cross-dataset features** (e.g. DSS recipients / ERP population).
  The format supports `dataset:` as a list, but the evaluator's
  multi-dataset handling needs the auto-load step to land first.
- **Tables 2–9 of ATO Personal Income** (age/sex breakdowns, income
  distribution, employee/investment/super/own-business income).
- **`Pipeline.create(releases=...)`** for per-dataset release pinning
  (spec §20.4) — defaults work for now (latest of each).

## [1.2.3] - 2026-05-07

### Fixed

- **G-NAF remote / cache mode now reads from the actual gnaf-loader
  layout.** Follow-up to #8 / v1.2.2. The previous fix assumed G-NAF
  Core lived as flat parquets at the root of `geoparquet/` with ABS
  boundary tables in subdirectories. The real bucket layout is the
  opposite: every dataset (including G-NAF) is in a subdirectory,
  and the v1.2.2 "no subdirectories" filter rejected everything,
  failing with `No .parquet files found at .../geoparquet/`. (Resolves
  #12.)

  v1.2.3 auto-detects two layouts:

  * **gnaf-loader** *(default for the production bucket)*: data lives
    at `geoparquet/address_principal_census_{year}_boundaries/` —
    gnaf-loader's denormalised join of address principals with the
    ABS census boundary IDs. Column names are PostgreSQL-lowercase
    (`gnaf_pid`, `address`, `latitude`, `mb_{year}_code`, ...); the
    `gnaf` view aliases them to the uppercase
    `ADDRESS_DETAIL_PID` / `MB_CODE` etc. that the geocoder queries.
  * **legacy / bring-your-own** *(fallback)*: a flat parquet at the
    release root with already-uppercase columns. Used by the
    existing test fixtures and by users who pre-build their own
    G-NAF parquet from the official Geoscape PSV.

  Detection runs on every `open_connection()` — listing the
  appropriate paths in S3 or the local cache. If neither layout is
  present, a clear `RuntimeError` lists what was tried.

### Added

- **`census_year` parameter on `GnafDataSource`** (default `2021`),
  also wired through as `cfg.census.year` in YAML configs. Selects
  which year's boundaries subdirectory to read under the gnaf-loader
  layout — letting users targeting the 2016 census pull `mb_2016_code`
  instead.
- New tests covering the gnaf-loader path end-to-end against a moto
  S3 server: layout auto-detection, year selection, mixed-layout
  preference (gnaf-loader wins over legacy when both are present),
  cache-mode download preserving subdirectory structure on disk so
  the next run's detector finds it, legacy fallback for buckets
  without the gnaf-loader subdir.

### Changed

- `_validate_schema_local` removed; schema validation now runs as a
  `DESCRIBE gnaf` against the constructed view (cheap — parquet
  footer metadata only). Catches column-mismatch errors after
  aliasing has applied for the gnaf-loader layout, which is what
  callers actually care about.
- `_download_release_from_s3` preserves subdirectory structure when
  it sees a gnaf-loader layout — files land at
  `<release_dir>/address_principal_census_{year}_boundaries/{filename}`
  rather than flat at the release root. Required for cache-mode
  layout detection to find them.
- `data_sources.gnaf_parquet_filter` semantics tightened: still a
  regex against the relative key, but now only consulted under the
  legacy code path. Under gnaf-loader the subdirectory itself does
  the scoping. Setting the regex explicitly also re-enables
  subdirectory parquets in the legacy path (the v1.2.2 default
  flat-only behaviour applies only when no regex is set).

## [1.2.2] - 2026-05-07

### Fixed

- **G-NAF remote (and cache) mode no longer pulls in non-G-NAF
  parquets from the gnaf-loader bucket.** The bucket co-locates
  G-NAF Core flat parquets at the root of `geoparquet/` with ABS /
  OSM boundary tables in named subdirectories (e.g.
  `abs_2016_gccsa/part-00000-*.snappy.parquet`). Previously,
  `_list_parquet_objects_on_s3` returned everything; remote mode
  fed the lot to DuckDB's `read_parquet([...])` and the schema
  validator caught the boundary parquet's `gcc_16code` /
  `gcc_16name` / `geom` columns as a "missing required columns"
  failure. Cache mode had the same bug latent — it'd download all
  the boundary files alongside G-NAF.

  The default filter now accepts only flat parquets directly under
  `geoparquet/`. Override via the new `parquet_filter` constructor
  arg (or `data_sources.gnaf_parquet_filter` in YAML) — a regex
  matched against the relative key — for buckets with a different
  layout. Resolves #8.

- **Dotted bucket names (e.g. `minus34.com`) auto-switch to
  path-style URLs in remote mode.** AWS's wildcard cert
  `*.s3.amazonaws.com` only matches a single subdomain level, so
  `minus34.com.s3.amazonaws.com` (virtual-hosted) fails TLS hostname
  verification — libcurl reports `SEC_E_WRONG_PRINCIPAL` /
  hostname-mismatch. `_build_object_url` now detects dots in the
  bucket name and constructs `https://s3.amazonaws.com/{bucket}/{key}`
  (path-style on the global endpoint, which redirects to the
  bucket's region). Users no longer need to set
  `s3_https_endpoint` manually for the gnaf-loader default.
  Also from #8.

## [1.2.1] - 2026-05-07

### Fixed

- **`pyproject.toml` distribution name now matches the GitHub repo.**
  Was `census-augment`; is now `abs-census-augmentor`. uv (and modern
  pip) enforce name-equality between the requested distribution and
  the metadata reported by the build backend, so `uv add
  abs-census-augmentor @ git+https://github.com/cauldnz/abs-census-augmentor.git`
  was rejected with a name-mismatch error. Resolves #7. The
  CLI command (`census-augment`) and the import name
  (`from census_augment import ...`) are unchanged — distribution
  name is independent of both.
- **`pyproject.toml` `version` field is now in sync with the
  CHANGELOG history.** Was stuck at `1.0.0` while the changelog
  documented 1.1.0 and 1.2.0 features — anyone who installed from
  main between those releases got artefacts metadata-tagged as
  v1.0.0. Bumped straight to `1.2.1` to align with reality and
  treat the name-fix above as the patch release. Going forward,
  CLAUDE.md's contributor conventions require pyproject.toml's
  `version` and the CHANGELOG to move together.

## [1.2.0] - 2026-05-06

### Added

- **`mode: remote` is now implemented.** DuckDB streams G-NAF parquet
  directly from S3 via the `httpfs` extension — no local cache,
  queries pull only the bytes they need. Set `geocoding.gnaf.mode:
  remote` in your config; everything else (release resolution, the
  geocoder tiers, the MB fast path) works unchanged.
- New config knob: `data_sources.gnaf_s3_https_endpoint` lets you
  point at an S3-compatible mirror (MinIO, Cloudflare R2, ...) or a
  test server. When set, path-style addressing is forced; default
  `None` uses AWS's virtual-hosted style.
- `census-augment gnaf-info` now reports remote-mode connectivity
  details (resolved release, configured endpoint, S3 base) instead
  of the cache-mode "not cached" message that's meaningless when
  streaming.

### Changed

- `census-augment fetch --gnaf` now exits with a friendly error in
  remote mode (rather than silently no-opping or downloading) — the
  fetch is meaningless when you're not caching.
- `_resolve_release` ignores local cache when `mode='remote'`. The
  whole point of remote mode is to skip the download; preferring a
  stale local cache for resolution would be confusing.

### Internals

- `GnafDataSource` now dispatches `open_connection` on mode:
  `_open_cache_connection` (existing path) and `_open_remote_connection`
  (new). `_validate_schema` is split into `_validate_schema_local`
  (parquet footer via pyarrow) and `_validate_schema_remote`
  (DuckDB `DESCRIBE gnaf` over httpfs).
- New `_build_object_url` helper — virtual-hosted by default,
  path-style when an endpoint override is configured.
- New `_list_parquet_objects_on_s3` shared between cache (download)
  and remote (URL construction) paths.
- `moto[s3,server]>=5` (was `moto[s3]>=5`): the `server` extra
  pulls in Flask, needed for `ThreadedMotoServer` which the new
  end-to-end remote-mode tests use to exercise DuckDB's httpfs
  against a real HTTP server (moto's `@mock_aws` only intercepts
  boto3, not httpfs's libcurl).

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
