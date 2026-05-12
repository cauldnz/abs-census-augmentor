# Library usage

`census-augment` ships a library API alongside the CLI. Use it from notebooks, data-science pipelines, or any Python code where you have a `pandas.DataFrame` of locations and want SA2-keyed Census columns merged in.

← [back to docs index](index.md)

## Quick start

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

`pipeline.augment(df)` returns an `AugmentResult` containing:

- the augmented `DataFrame`,
- a `RunSummary` with row-level counts,
- three boolean `Series` indexed like the input (`is_fully_enriched`, `geocoding_failed`, `sa2_unmatched`) so you can filter or join back.

See [`spec.md` §18](../spec.md) for the full API surface and return-type fields.

## First-run download

The first call to `pipeline.augment(df)` (or `Pipeline.run()`, or any `census-augment` CLI command that touches the data) downloads ~50 MB of SA2 boundaries and ~40 MB of Census DataPacks into the user cache. Subsequent calls — including across notebooks, scripts, and CLI runs on the same machine — reuse the cache and are instant.

See [Configuration → cache locations](configuration.md#cache-locations).

## G-NAF modes

With `geocoding.gnaf.mode: cache` (default), the first run downloads ~10 GB of parquet locally for offline querying. With `geocoding.gnaf.mode: remote`, DuckDB streams directly from S3 via httpfs — no download, but each query is HTTPS-bound.

To skip G-NAF entirely set `providers: [nominatim]`.

See [G-NAF setup](gnaf-setup.md) for the trade-offs.

## Examples

Runnable scripts in [`examples/`](../examples/):

- [`examples/library_basic.py`](../examples/library_basic.py) — minimal library use.
- [`examples/library_with_overrides.py`](../examples/library_with_overrides.py) — per-call column overrides, custom prefix, mask-based filtering.
- [`examples/library_with_seifa.py`](../examples/library_with_seifa.py) — mixing Census GCP + SEIFA variables.
- [`examples/library_with_preset_features.py`](../examples/library_with_preset_features.py) — declaring `PRESET.<id>` ratios.
- [`examples/standalone_dataset_fetchers.py`](../examples/standalone_dataset_fetchers.py) — using individual dataset fetchers directly.
