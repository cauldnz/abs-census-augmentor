# Temporal data — augmenting time-series with the right snapshot

← [back to docs index](index.md)

`census-augment` augments location data with ABS Census and related datasets at the SA2 statistical area level. The tool supports two modes:

- **Cross-sectional mode** — one configured snapshot per dataset for every row. This is the default and remains the case when your input has no date column.
- **Temporal mode** — each row picks the snapshot closest to its own timestamp, looked up at the boundary edition the dataset release was originally compiled against. Set `input.date_column` to enable.

This page covers temporal mode. For the design rationale and the full per-dataset cadence + edition catalog, see [`../spec-temporal.md`](../spec-temporal.md).

## When you need it

- Transaction logs, observational time-series, or any input where each row has a meaningful date.
- You want demographic / income / SEIFA values appropriate to *each row's time*, not a single configured snapshot.
- Your input spans an ABS Statistical Geography Standard (ASGS) edition transition (most relevant around mid-2021, when ASGS Edition 2 → 3 transitioned).

## Quick example

A minimal temporal-mode config:

```yaml
input:
  path: transactions.csv
  latitude_column: lat
  longitude_column: lon
  date_column: transaction_date     # turns on temporal mode

output:
  path: enriched.csv
  prefix: sa2_

geocoding:
  providers: [nominatim]
  nominatim:
    user_agent: "your-app/1.0 (you@example.com)"

# Temporal-mode behaviour. All defaults — block can be omitted.
temporal:
  resolution: closest_at_or_before  # default
  out_of_range: fail                 # default
  reference_edition: 3               # ASGS Edition 3 (2021 boundaries). Default.

variables:
  median_income: G02.Median_tot_hhd_inc_weekly
  irsd_decile: SEIFA.irsd_aus_decile
  payments_age_pension: DSS.age_pension_recipients
```

Each row of `transactions.csv` then gets:

- The Census GCP DataPack release closest to its `transaction_date`.
- The SEIFA release closest to its `transaction_date`.
- The DSS quarterly snapshot closest to its `transaction_date`.

Each of those lookups uses the SA2 boundary file the release was compiled against. The output's canonical `sa2_code` is in the configured `reference_edition`.

## What temporal mode adds to the output

- A `<dataset_id>_release` column per dataset that was used (e.g. `seifa_release`, `erp_by_sa2_release`, `dss_payments_release`).
- A `sa2_code_edition` column naming the reference edition the canonical `sa2_code` is in (constant per run).
- When a row's dataset release was on a different ASGS edition than `reference_edition`, a `<dataset_id>_sa2_code_source` column carrying the source-edition SA2 code (so downstream consumers can do per-dataset groupby if they need to).

Cross-sectional mode output is unchanged.

## Resolution rules

`temporal.resolution` controls how the right release gets picked per row:

| Value | Meaning | Best for |
|---|---|---|
| `closest_at_or_before` (default) | Most recent release whose coverage window starts ≤ row date | Causally-correct "as-of" semantics. "What was the SA2 demographic at the time of this transaction?" |
| `closest` | Release whose coverage-window midpoint is nearest row date | Granular cadence datasets (DSS quarterly) where the "wrong side" of a release boundary misses by at most ~45 days |

You can override per-dataset:

```yaml
temporal:
  resolution: closest_at_or_before
  per_dataset:
    dss_payments:
      resolution: closest          # closer to monthly transaction data
```

## Out-of-range dates

`temporal.out_of_range` controls what happens when a row's date predates any touched dataset's earliest release:

| Value | Meaning |
|---|---|
| `fail` (default) | Abort the run; clear error listing the first affected row indices |
| `nearest` | Clamp to the earliest available release; WARN per affected row; `RunSummary.out_of_range_clamped` counts the affected rows |

## ASGS editions and the boundary-correctness invariant

ABS redrew SA2 boundaries between Census editions:

- **ASGS Edition 1** — SA2 codes valid Jul 2011 – Jun 2016.
- **ASGS Edition 2** — SA2 codes valid Jul 2016 – Jun 2021.
- **ASGS Edition 3** — SA2 codes valid Jul 2021 – Jun 2026. (Current.)

Datasets compiled before mid-2021 (or back-published with older keys) use Edition 2 SA2 codes. Datasets compiled after use Edition 3 codes. About 8% of SA2 boundaries changed between editions — mostly splits in urban growth corridors.

**Critical invariant.** When a row's dataset release was on a different ASGS edition than the tool's `reference_edition`, the spatial lookup for that release's enrichment value uses the **source edition's boundary file**, not the reference edition's. This is why the same row can pull values from two different boundary lookups when its bucket straddles a transition.

Practically: a row dated 2022-06-01 may pull:

- **GCP, SEIFA, ERP, ABS PIA** from ASGS 2021 (post-transition releases)
- **DSS** from ASGS 2016 (DSS transitioned at Q2-2023)

The pipeline does two spatial lookups for that row's bucket; both are point-correct.

## Library usage

```python
import pandas as pd
from census_augment import Pipeline
from census_augment.config import (
    Config, InputConfig, OutputConfig, GeocodingConfig,
    NominatimConfig, TemporalConfig, CensusConfig, DataSourcesConfig,
)

df = pd.DataFrame({
    "label":            ["a", "b", "c"],
    "lat":              [-33.8568, -33.8568, -33.8568],
    "lon":              [151.2153, 151.2153, 151.2153],
    "transaction_date": pd.to_datetime(["2018-06-15", "2021-08-01", "2024-09-01"]),
})

pipeline = Pipeline.create(
    variables={"median_income": "G02.Median_tot_hhd_inc_weekly"},
    user_agent="my-app/1.0 (me@example.com)",
    latitude_column="lat",
    longitude_column="lon",
    date_column="transaction_date",   # NEW kwarg in temporal mode
)

result = pipeline.augment(df)
result.df.head()
# Per-row sa2_code in reference edition, per-row gcp_2021_release, etc.
result.releases_used
# {"gcp_2021": {"2016", "2021"}, ...}
```

## Performance considerations

Temporal mode buckets rows by per-dataset release tuple and runs one Pipeline per bucket. For 5 years of input with annual-cadence datasets, that's typically ~5 buckets. Quarterly cadence (DSS) can multiply this; the `closest` resolution rule helps minimise the bucket count.

Each bucket loads its release's parquet sidecars (per the cache layout) — warm-cache loads are sub-second per bucket. Cold-cache loads trigger downloads as today.

The per-edition spatial index loader is the only meaningful new cost: for a bucket that straddles ASGS editions, the pipeline loads both edition's SA2 boundary geometries. With the `<boundary>.feather` sidecar caches (PR #49) this is ~200ms per edition.

## Limitations

- **Multi-snapshot side-by-side output** (e.g. "give me 2011, 2016, 2021 income for the same row in three columns") is not currently supported. Run the pipeline three times with three configs and merge externally.
- **Cross-edition SA2 aggregation** — if you `df.groupby("sa2_code")` and your rows pulled from datasets across an edition transition, the per-dataset values pooled into each canonical-SA2 bucket may have come from slightly different boundary areas. For point-based analysis this is fine. For aggregation-correctness across editions you'd want Level 3 (value migration via correspondence tables), which is on the roadmap but not yet implemented.
- **Pre-2011 historical datasets** (SEIFA 2001/2006 etc.) are not currently registered. They use pre-ASGS CCD/SLA geographies that don't align with SA2.

## Further reading

- [`../spec-temporal.md`](../spec-temporal.md) — full design spec with per-dataset cadence catalog, edge cases, and the design rationale.
- [`configuration.md`](configuration.md) — `config.yaml` reference including the new `temporal` block.
