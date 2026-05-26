# Examples

Runnable scripts and a CLI sample. Browse by **what you want to do**:

## Pipeline.augment quick-starts

| Example | What |
|---|---|
| [`library_basic.py`](library_basic.py) | Smallest possible library use: `Pipeline.create()` + `augment(df)` with GCP variables. |
| [`library_with_overrides.py`](library_with_overrides.py) | Per-call column-name overrides, custom output prefix, `AugmentResult` masks. |
| [`library_with_seifa.py`](library_with_seifa.py) | **v1.3** — mix GCP + SEIFA variables in one config. The pipeline dispatches each through the right dataset's fetcher transparently. |
| [`library_with_preset_features.py`](library_with_preset_features.py) | **v1.4** — mix GCP + SEIFA + PRESET ratios (`PRESET.pct_renters`, `PRESET.pct_drive_to_work`, ...) in one config. PRESETs are first-class variable refs; their source columns load transparently. |

## CLI

| Example | What |
|---|---|
| [`cli/`](cli/) | CLI walkthrough: `config.yaml` + `input.csv` + [`RUN.md`](cli/RUN.md). |

## Standalone (no pipeline)

For analysis code that wants SA2-keyed data without the geocoding +
spatial-join + DataFrame-merge dance:

| Example | What |
|---|---|
| [`standalone_dataset_fetchers.py`](standalone_dataset_fetchers.py) | **v1.3** — drive the four new dataset fetchers (SEIFA, ERP, DSS, ATO) directly. Caches a parquet on first call; subsequent loads are instant. |

## How to run them

From the repo root:

```bash
# Library examples
python examples/library_basic.py
python examples/library_with_overrides.py
python examples/library_with_seifa.py
python examples/library_with_preset_features.py
python examples/standalone_dataset_fetchers.py

# CLI example
census-augment run --config examples/cli/config.yaml

# v1.3 — discover registered datasets and PRESET features
census-augment discover --config examples/cli/config.yaml --datasets
census-augment discover --config examples/cli/config.yaml --features
census-augment discover --config examples/cli/config.yaml --dataset seifa
```

All examples talk to real ABS endpoints. **First run downloads ~90 MB** into the platform user cache (~50 MB SA2 boundaries + ~40 MB Census 2021 GCP DataPack). v1.3 dataset examples additionally fetch their respective sources on first use:

| Dataset | First-call download |
|---|---|
| SEIFA 2021 | ~150 KB (single XLSX) |
| ERP by SA2 | ~3 MB (long-history XLSX, 2001 onwards) |
| DSS Payments | ~1 MB per quarter |
| ATO Personal Income | ~1 MB (Table 1 SA2) |

Subsequent calls reuse the cache and are instant.

To put the cache somewhere specific:

```bash
export CENSUS_AUGMENT_DATA_DIR=/path/to/data
export CENSUS_AUGMENT_CACHE_DIR=/path/to/cache
```

See [`spec.md` §9](../spec.md) for the full caching story, and
[`spec.md` §20](../spec.md) for the v1.3 dataset registry design.
