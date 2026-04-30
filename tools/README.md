# tools/

Scripts for verifying our parsers against **real** ABS data. These are NOT part of the test suite — they're a deliberate, opt-in path to ground-truth validation.

The pytest suite is hermetic (every external interaction mocked). Real-world validation lives here so:
- CI stays fast and offline-safe.
- Real ABS uptime can't make tests flaky.
- Future devs / AI agents have a discoverable path to ground-truth (`tools/README.md`).

## Workflow

```bash
# 1. Download real ABS files into data/ (gitignored)
python tools/fetch_real_data.py

# 2. Validate the parsers against them
python tools/verify_real_parsers.py
```

## What `fetch_real_data.py` downloads

| Path | Source | Size |
|---|---|---|
| `data/boundaries/SA2_2021_AUST_SHP_GDA2020/...` | ABS ASGS Edition 3 | ~50 MB |
| `data/census/2021_GCP_SA2_for_AUS_short-header/...` | ABS Census 2021 GCP | ~40 MB |
| `data/nominatim_sample.json` | Nominatim public API (1 query) | <1 KB |

Pass `--refresh` to force re-download. Pass `--skip-nominatim` to skip the geocoder query (e.g. offline run).

## What `verify_real_parsers.py` checks

- Boundary load: row count, schema, CRS.
- DataPack list_tables / load_metadata / load_table: count + spot-check known columns.
- Nominatim sample (if present): response shape preserved.

Exits non-zero on any failure.

## When to run

- After initial dev environment setup.
- After ABS publishes new versions of the boundaries / DataPacks (e.g. when 2026 Census lands).
- Whenever code touches the parsers.
