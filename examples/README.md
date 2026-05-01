# Examples

Runnable scripts and a CLI sample.

| Path | What |
|---|---|
| [`library_basic.py`](library_basic.py) | Smallest possible library use: `Pipeline.create()` + `augment(df)`. |
| [`library_with_overrides.py`](library_with_overrides.py) | Per-call column-name overrides, custom output prefix, `AugmentResult` masks. |
| [`cli/`](cli/) | CLI walkthrough: `config.yaml` + `input.csv` + [`RUN.md`](cli/RUN.md). |

## How to run them

From the repo root:

```bash
# Library examples
python examples/library_basic.py
python examples/library_with_overrides.py

# CLI example
census-augment run --config examples/cli/config.yaml
```

All examples talk to real ABS endpoints. **First run downloads ~90 MB** into the platform user cache (~50 MB SA2 boundaries + ~40 MB Census 2021 GCP DataPack); subsequent runs are instant.

To put the cache somewhere specific:

```bash
export CENSUS_AUGMENT_DATA_DIR=/path/to/data
export CENSUS_AUGMENT_CACHE_DIR=/path/to/cache
```

See [`spec.md` §9](../spec.md) for the full caching story.
