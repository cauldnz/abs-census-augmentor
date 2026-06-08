---
name: real-data-first
description: >-
  Use this skill whenever the user asks you to write, modify, or test code that
  reads, fetches, parses, or maps data from an external source they don't
  control — a REST/HTTP API (Stripe, NOAA, a vendor API), a CSV/TSV/Excel/
  Parquet/JSON file, a shapefile, a CKAN or data.gov dataset, or a drop in an
  S3/GCS/Azure bucket. It covers building clients, fetchers, loaders, parsers,
  scrapers, connectors, ingestion/ETL steps, pydantic or dataframe models,
  schema-aware config, and the test fixtures or mocks that stand in for such
  data. Trigger especially when the request names specific upstream columns,
  fields, keys, resource ids, file paths, sheet names, or encodings to extract
  or map (e.g. "pull amount and currency", "read sku and qty_on_hand", "extract
  the SA2 code"), or when someone hits "column not found" / "field not in
  response" / "file not in bucket" / a wrong field name. The discipline: fetch a
  REAL sample of the artifact first and build BOTH the production code AND its
  test fixtures from what you actually observe — never invent column names, field
  paths, filenames, sheet names, or encodings from documentation, intuition, or
  naming conventions. Do NOT trigger for purely in-memory data the user already
  holds, the user's own internal schemas (their own database, their own app's
  export), generating fake/synthetic data, or refactors that don't touch an
  external artifact's shape.
---

# Real Data First

## The rule

When code depends on the **shape** of an external artifact at runtime, fetch a
**real** sample of that artifact *before* writing the code, and build both the
production code **and** the test fixtures off that real sample. Never invent the
schema from documentation, naming conventions, intuition, or what "obviously
must" be there.

"Shape" means any detail your code reads but doesn't own: column names, field
paths, the keys in a JSON response, sheet names, the row a header sits on, file
and folder names inside an archive, the layout of a cloud bucket, a code's digit
count, a column's encoding, a date format, sentinel/pseudo rows. If your code
would break when that detail is different from what you assumed, it's shape.

## Why this exists

The failure mode is quiet and expensive. You read the upstream docs, you write a
parser against the schema they describe, and you write a synthetic fixture that
*also* encodes that schema. The tests pass — green, all the way — because the
fixture and the parser agree with each other. They were written from the same
assumption. Then a real user runs the code against the real artifact, the schema
is subtly different, and it dies with `KeyError`, "column not found", "file not
in bucket", or — worse — it silently produces wrong numbers.

The fixture didn't catch it because **the fixture was the bug**: it certified a
schema you'd guessed, not one you'd seen. Documentation lies, lags, or describes
a different version. Naming conventions get broken by the one team that didn't
read the convention. The only authority on an artifact's shape is the artifact.

This isn't hypothetical. The catalogue of real schema-detail bugs in
[references/failure-modes.md](references/failure-modes.md) — a join key that was
a different geographic grain than the docs implied, a data sheet named `Table 1`
(space) where its sibling used `Table_1` (underscore), a file that was
Windows-1252 where everything assumed UTF-8 — are all things no amount of reading
would have revealed, and all things one real fetch reveals in seconds. Read that
file when you want the concrete failure shapes; the lesson is that each was a
one-line detail that passed every test and broke every real run.

## When this applies

Trigger this discipline *before* you write or modify any of these:

- A **fetcher / downloader / connector** for an HTTP API, S3 / GCS / Azure
  bucket, FTP, database export, or file drop.
- A **parser / loader / reader** for CSV/TSV, Excel (`.xlsx`/`.xls`), JSON,
  Parquet/Arrow, shapefile/GeoJSON, XML, ZIP/tar archives, CKAN or other dataset
  APIs.
- An **ETL / ingestion** step that joins, reshapes, or maps an external dataset.
- A **schema-aware config, spec, or mapping** that names upstream columns,
  fields, paths, filenames, or codes (e.g. a YAML that says "join on column X").
- A **test fixture or mock** that stands in for an external artifact.

If you're about to type a literal column name, JSON key, file path, sheet name,
or encoding that originates upstream — stop and check you've *seen* it.

## The workflow

### 1. Fetch one real sample

Run a single real fetch of the actual artifact. Not the whole dataset — a
representative slice is enough: one file from the bucket, one page of the API,
the first few thousand rows of the CSV, the metadata workbook from the ZIP.

If you can't fetch right now, jump to [Can't fetch right
now](#cant-fetch-right-now) — do **not** proceed on a guess.

### 2. Dump and read its real schema

Look at what actually came back. The bundled
[scripts/probe_artifact.py](scripts/probe_artifact.py) does this for the common
types — point it at a URL or local path and it prints the things that bite you:

- **JSON / API**: the top-level keys, nested structure, and the *types* of leaf
  values (is `lat` a number or a string? is `results` a list or wrapped in a
  `data` envelope?).
- **CSV / TSV**: the detected encoding (it tries `utf-8-sig`, `utf-8`, `cp1252`,
  `latin-1` and reports which worked), the real header row, sample rows, and a
  guess at the delimiter.
- **Excel**: every sheet name *verbatim* (punctuation and spacing included), and
  for each sheet the first ~20 rows so you can see where the real header sits
  under any preamble.
- **ZIP / archive**: the internal directory layout, every entry with its size,
  and a peek inside a representative member.
- **Parquet / Arrow**: the column names and Arrow dtypes.
- **Shapefile / GeoJSON**: the attribute (field) columns, the CRS, and geometry
  types.

```bash
python probe_artifact.py "https://example.org/data/export.csv"
python probe_artifact.py ./sample.xlsx
python probe_artifact.py ./bundle.zip
```

Read the output. The column names, sheet names, encoding, key paths, and code
formats you build against are **whatever this prints** — copy them, don't retype
from memory.

### 3. Save the sample somewhere reviewable

So the next person (including future you) can re-derive the schema and so it's
clear the code was built from observation, not assumption. Pick one:

- A **probe / fetch script in `tools/`** (or equivalent) that re-fetches the
  slice reproducibly — the gold standard, because it survives the artifact
  changing. `probe_artifact.py` is a starting template; adapt its source list.
- A **fixture file** checked into the repo (e.g. `tests/fixtures/`) when the
  artifact is small and stable.
- A **gitignored data dir** for large artifacts, *with the re-fetch script
  checked in* so anyone can repopulate it.

### 4. Build fixtures that mirror the real schema exactly

This is the step that actually closes the loop. Your synthetic test fixtures
must use the **exact** column names, key paths, sheet names, encodings, and code
formats from the real sample. Synthetic *values* are fine and encouraged (don't
ship real PII or huge files into tests) — synthetic *schema* is the bug. A
fixture with a plausible-but-wrong column name is worse than no fixture: it turns
a guess into a green checkmark.

### 5. Add a re-runnable verify step

Leave behind something that exercises the **live** source and asserts the schema
you built against still holds — a `verify`/probe script, a smoke test gated on a
network marker, a scheduled check. This catches upstream drift the day it lands
instead of the day a user hits it, and gives the next person a known-good probe.
Keep it out of the unit-test suite (which must stay hermetic and offline) — it's
a separate, opt-in command.

## The acid test

For **any** external column, field, key path, file path, filename, sheet name,
code format, or encoding your code or config mentions, you should be able to
point at one of:

- a **fixture / sample file** checked into the repo,
- a **`tools/` (or equivalent) script** that re-fetches the live artifact
  reproducibly, or
- a **verify / probe step** that exercises the live source.

If you can't point at one of those, you're guessing. Stop and fetch — or stop and
ask.

## Can't fetch right now

Sometimes you genuinely can't get a real sample: you're offline, the source is
paywalled, you lack credentials, the endpoint is down, or the artifact only
exists in an environment you can't reach.

When that happens, **say so explicitly and ask the user.** Name what you'd need
(a credential, a sample file, a VPN, the endpoint to come back up) and offer to
proceed only on the parts that don't depend on the unseen schema. Do **not** ship
code that names columns / fields / paths / encodings you haven't seen, and don't
bury the assumption in a passing synthetic test.

**If you must scaffold before a sample exists, never let a green test certify the
guess.** Sometimes work shouldn't fully block, so you sketch a parser against the
prose-described schema anyway. Fine — but quarantine it so a passing run can't be
misread as "schema confirmed". This is the single most valuable habit in the
whole discipline, because the default failure is *green-but-wrong*:

- Mark every test that depends on the assumed schema as skipped / `xfail` with an
  explicit marker (e.g. `unverified_schema`), so a default run reports it as
  *deselected*, never *passed*.
- Keep the default suite asserting only what you've actually observed — and that
  the code **fails loudly**, naming the real columns/sheets/keys it found, when
  an assumption is wrong. A loud failure on bad input is worth more than a quiet
  pass on a guess.
- Leave the probe and a one-line "when a real sample arrives, do this" checklist
  next to the code, so confirming the schema is a single command away.

Green should mean *observed*, never *assumed*. That distinction is the whole game.

The economics are lopsided and worth stating plainly: getting the schema wrong
costs a silent bug that passes every test and surfaces in a real run later
(expensive, far from where it was introduced, often hitting a user first); asking
costs one chat turn. Ask.

## Bundled resources

- [scripts/probe_artifact.py](scripts/probe_artifact.py) — a project-agnostic
  probe that fetches an HTTP/file artifact and dumps its real schema (JSON keys,
  CSV encoding + header, Excel sheet names + header detection, ZIP layout,
  Parquet/shapefile columns). Adapt the source list and paste the output back
  when building the parser. Dependency-light: stdlib + `requests`; richer types
  (Excel/Parquet/shapefile) light up when `pandas`/`pyarrow`/`pyogrio` are
  installed, and degrade with a clear "install X" message otherwise.
- [references/failure-modes.md](references/failure-modes.md) — a catalogue of the
  schema-detail bugs that only real data reveals, each framed as a general class
  with the symptom, what a real fetch shows, and the lesson. Read it when you
  want concrete examples of *why* this discipline pays off.
