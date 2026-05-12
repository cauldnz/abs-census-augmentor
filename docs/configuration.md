# Configuration

`census-augment` is configured via a `config.yaml` file (CLI) or `Pipeline.create(...)` / `Pipeline.from_config(...)` kwargs (library). Both routes feed the same Pydantic models in `src/census_augment/config.py`.

← [back to docs index](index.md)

## Full schema

The annotated source of truth is [`config.example.yaml`](../config.example.yaml). It documents every supported field inline with comments — including the variables block, geocoding providers, data sources, and output options.

## Cache locations

By default both ABS downloads and the geocoding cache live in the platform user cache:

| OS | Path |
|---|---|
| Linux | `~/.cache/census-augment/` |
| macOS | `~/Library/Caches/census-augment/` |
| Windows | `%LOCALAPPDATA%\census-augment\Cache\` |

Override with:

- env vars: `CENSUS_AUGMENT_DATA_DIR`, `CENSUS_AUGMENT_CACHE_DIR`
- CLI flags: `--data-dir`, `--cache-dir`
- library kwargs: `data_dir=`, `cache_dir=`

See [`spec.md` §9](../spec.md) for the full precedence rules.

## Variables

The `variables:` block maps **output column name** → **variable reference**. The variable reference is a dotted ID resolved against the registered dataset catalog. Examples:

```yaml
variables:
  median_age: G02.Median_age_persons
  irsad_decile: SEIFA.irsad_decile
  pct_renters: PRESET.pct_renters
```

- `G<n>.<col>` — Census GCP DataPack columns (table → column).
- `SEIFA.<col>`, `ERP.<col>`, `DSS.<col>`, `ABS_PIA.<col>` — other registered datasets (one entry per registered dataset spec in `datasets/<id>.md`).
- `PRESET.<id>` — curated ratio features defined in `features/<id>.md`. The pipeline auto-loads the underlying numerator + denominator columns from whichever dataset(s) they reference.

Run `census-augment discover --datasets` (and `--features`) for the registered list, or browse [`datasets/`](../datasets/) and [`features/`](../features/) directly.

## Geocoding providers

Common shapes — pick whichever matches your environment:

```yaml
# Default: G-NAF (cache mode) with Nominatim fallback
geocoding:
  providers: [gnaf, nominatim]
  nominatim:
    user_agent: "my-app/1.0 (you@example.com)"
```

```yaml
# G-NAF in remote mode (no 10 GB download; DuckDB streams from S3)
geocoding:
  providers: [gnaf, nominatim]
  gnaf:
    mode: remote
    release: latest
```

```yaml
# No G-NAF at all (Nominatim only — much slower; rate-limited 1 req/sec)
geocoding:
  providers: [nominatim]
  nominatim:
    user_agent: "my-app/1.0 (you@example.com)"
```

See [G-NAF setup](gnaf-setup.md) for cache vs remote trade-offs.

## Temporal mode

Set `input.date_column` to enable per-row dataset-release selection:

```yaml
input:
  path: transactions.csv
  latitude_column: lat
  longitude_column: lon
  date_column: transaction_date    # enables temporal mode

temporal:                           # optional; all fields have defaults
  resolution: closest_at_or_before  # default. Also: closest.
  out_of_range: fail                 # default. Also: nearest.
  reference_edition: 3                # default. ASGS edition for output sa2_code.
  per_dataset:                        # per-dataset resolution overrides
    dss_payments:
      resolution: closest             # quarterly cadence — closest often
                                       # makes more sense than at-or-before
```

See [Temporal data](temporal-data.md) for the user-facing guide. Full
design / rationale in [`spec-temporal.md`](../spec-temporal.md).
