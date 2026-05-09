---
id: <stable_snake_case_id>
name: <human-readable name>
status: proposed                     # proposed | active | deprecated
custodian: <organisation that publishes the dataset>
licence: <SPDX-style id, e.g. CC-BY-4.0>
update_cadence: <annual | quarterly | monthly | adhoc | one-shot>
geography_level: SA2                 # SA1 | SA2 | LGA | ...
geography_edition: 2021_ASGS_Edition_3
geography_native: true               # true if published natively at this level; false if interpolated/concorded
join_key: sa2_code_2021
landing_page: <URL>
fetch_size_compressed: <approximate, for cache budgeting>
tags: [<freeform tags>]
namespace: <prefix used in Pipeline.variables, e.g. DSS, ERP, ATO>
---

# <name>

<One-paragraph description: what this dataset is, why someone would use it, what
gap it fills relative to the GCP DataPack and other registered datasets. Be specific
about the value proposition — coverage, recency, granularity, bias profile.>

## Source

<Source URL, fetch path, file format, how the augmentor downloads it. Include
specifics on:>

- Landing page or API endpoint
- File formats (XLSX, CSV, JSON, parquet)
- Whether there's a stable URL pattern or whether the augmentor needs to scrape
  the landing page to find the latest release
- Authentication / API keys required (ideally none for open datasets)
- Approximate download size

## Update cadence

<When new releases happen, how to detect them, whether old releases stay accessible.
Note any lag between reference period and publication.>

## Granularity

<Native level — SA1, SA2, LGA — and whether/how it rolls up to SA2.
Note ASGS edition the data is published on, and whether ABS/source rebases historical
data to the current edition automatically or whether the augmentor needs to apply a
concordance.>

## Schema (variables exposed by the augmentor)

| Variable | Type | Description |
|---|---|---|
| `<namespace>.<var1>` | int | <description> |
| `<namespace>.<var2>` | float | <description> |
| `<namespace>.<var3>` | str | <description> |

<Note any default behaviour: e.g. "augmentor returns the latest available period
by default; users can pin via Pipeline.create(..., <namespace>_release=...)".>

## Fetch notes

<Implementation hints, gotchas, schema drift between releases. Specifically:>

- Schema changes across releases (column renames, new variables, dropped variables)
- Aggregate / total rows mixed into the data and how to filter them
- Pagination if applicable
- Cache invalidation strategy

## Suppression / privacy notes

<Does the source perturb counts? Apply small-cell suppression? How are suppressed
cells encoded — null, "<X", "np", -1? How should the augmentor surface them?>

## Suggested derived features

<List of `features/<id>.md` files that use this dataset, with one-line rationale.
Cross-dataset features (using this dataset and others jointly) are valid here too.>

- `<feature_id>` — <one-line rationale>
- `<feature_id>` — <one-line rationale>

## Sources / citations

- <Primary technical paper / methodology document>
- <Metadata file or data dictionary>
- <Licence URL>
