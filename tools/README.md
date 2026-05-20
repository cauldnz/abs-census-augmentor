# tools/

Scripts for verifying our parsers against **real** ABS data. These are NOT part of the test suite — they're a deliberate, opt-in path to ground-truth validation.

The pytest suite is hermetic (every external interaction mocked). Real-world validation lives here so:
- CI stays fast and offline-safe.
- Real ABS uptime can't make tests flaky.
- Future devs / AI agents have a discoverable path to ground-truth (`tools/README.md`).

## Workflow

```bash
# 1. Download real ABS files into the user cache (per spec §9).
#    The same cache is shared with the library (Pipeline) and the CLI,
#    so this also primes them — no duplicate downloads.
python tools/fetch_real_data.py

# 2. Validate the parsers against them
python tools/verify_real_parsers.py
```

## What `fetch_real_data.py` downloads

| Item | Source | Size |
|---|---|---|
| `<data_dir>/boundaries/SA2_2021_AUST_SHP_GDA2020/...` | ABS ASGS Edition 3 | ~50 MB |
| `<data_dir>/census/2021_GCP_SA2_for_AUS_short-header/...` | ABS Census 2021 GCP | ~40 MB |
| `<data_dir>/nominatim_sample.json` | Nominatim public API (1 query) | <1 KB |

`<data_dir>` defaults to the platform user cache (e.g. `~/.cache/census-augment/data/` on Linux, `%LOCALAPPDATA%\census-augment\Cache\data\` on Windows). Override with the `CENSUS_AUGMENT_DATA_DIR` env var. See spec §9.

Pass `--refresh` to force re-download. Pass `--skip-nominatim` to skip the geocoder query (e.g. offline run).

## What `verify_real_parsers.py` checks

Anchor sources (v1.0):

- Boundary load: row count, schema, CRS.
- DataPack list_tables / load_metadata / load_table: count + spot-check known columns.
- Mesh Block correspondence: MB → SA2 lookup populated.
- Nominatim sample (if present): response shape preserved.

G-NAF (v1.1):

- G-NAF fetch from the gnaf-loader S3 bucket.
- Schema + view detection for both gnaf-loader and legacy layouts.

Registered datasets (v1.3):

- SEIFA 2021 fetch + parse: 4 indexes × 10 fields per SA2.
- ERP by SA2 fetch + parse: yearly population history.
- DSS payments fetch + parse: quarterly demographic data.
- ATO personal income fetch + parse: financial-year income / earner counts.

PRESET features (v1.4):

- Each registered PRESET resolves its source columns against the
  loaded GCP DataPack metadata (catches silently-renamed columns
  in a new GCP release).

Exits non-zero on any failure.

## When to run

- After initial dev environment setup.
- After ABS publishes new versions of the boundaries / DataPacks (e.g. when 2026 Census lands).
- Whenever code touches the parsers.

## One-off discovery probes (Phase F.3 / F.4)

Two scripts hand-rolled to capture the schema of a *new* historical
release before its fetcher is written, per CLAUDE.md "Real Data First".
Each downloads (or accepts) one file and dumps everything the parser
will need to know — sheet names, header row position, column codes,
sample data rows — so the maintainer-facing PR can build a fetcher
against a known shape rather than guess.

| Script | Phase | What it does |
|---|---|---|
| `inspect_seifa_2016.py` | F.3 | Fetches the live SEIFA 2016 SA2 `.xls` from the ABS legacy catalogue page and dumps every sheet's preamble + header + 3 sample rows. Requires `xlrd==1.2.0` for `.xls` support (`uv pip install 'xlrd==1.2.0'`). |
| `inspect_gcp_2016.py <zip-path>` | F.4 | Takes a path to a locally-downloaded 2016 GCP DataPack ZIP and dumps the internal layout, descriptor xlsx structure, and a representative table CSV's header + sample rows. The 2016 GCP isn't reachable via static URL — see the script's docstring for download options. |

Run via `uv run python tools/inspect_<name>.py [args]`, paste the stdout
into the chat / PR thread driving the Phase F.3 or F.4 fetcher PR.
Both scripts are idempotent (cache the artefact under `data/` so reruns
don't re-fetch unless `--refresh` is passed).

Once the fetcher lands, the equivalent post-fetch shape check moves into
`verify_real_parsers.py` as a permanent drift detector and the
`inspect_*.py` scripts retire.
