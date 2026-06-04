---
id: gcp
name: ABS Census General Community Profile DataPack
status: active
custodian: Australian Bureau of Statistics
licence: CC-BY-4.0
update_cadence: per_census
geography_level: SA2
geography_edition: 2021_ASGS_Edition_3
geography_native: true
join_key: sa2_code_2021
landing_page: https://www.abs.gov.au/census/find-census-data/datapacks
fetch_size_compressed: ~35-40 MB
tags: [census, demographics, population, housing, employment, transport]
namespace: G
temporal:
  cadence: per_census
  cover_basis: census_reference_date
  release_id_format: "YYYY (Census year)"
  available_releases:
    - "2016"
    - "2021"
  asgs_edition_by_release:
    "2016": 2
    "2021": 3
---

# ABS Census General Community Profile DataPack

The DataPack the augmentor was originally built around. ABS's headline
Census product, registered as a single dataset with two releases:

- **2021** — ASGS Edition 3, SA2 codes valid Jul 2021 – Jun 2026. Current.
- **2016** — ASGS Edition 2, SA2 codes valid Jul 2016 – Jun 2021.

Each release is published as a ZIP of ~60 tables (G01–G59 in 2016,
G01–G62 in 2021) plus a metadata workbook. Covers population, housing,
families, education, employment, transport, language and ancestry —
the standard cross-sectional demographics most modelling work wants
for an SA2.

This is the only registered dataset whose `namespace` is a *prefix*
(`G`) rather than a fixed string — variable refs are written
`G02.Median_age_persons` rather than `G.G02_Median_age_persons`,
preserving the per-table breakdown that matches how ABS publishes the
data and the existing v1.0–v1.2 contract.

The short-header column codes are stable across the two releases for
the columns sampled (G01 totals, G02 medians, etc.) — existing
variable references like `G02.Median_tot_hhd_inc_weekly` resolve
identically in both 2016 and 2021. New tables in 2021 (G60–G62) are
unavailable for 2016 rows and will fail variable resolution; the
catalog reports the unsupported table in the error.

## Source

ABS publishes each release as `{year}_GCP_SA2_for_AUS_short-header.zip`
at `https://www.abs.gov.au/census/find-census-data/datapacks/download/`.
The URL pattern is identical between releases — only the leading
year changes. The augmentor downloads via the existing
:class:`DataPacksDataSource` fetcher.

Only the `short-header` descriptor variant is hosted for the 2016
release — the `long-header` and `sequential` variants return HTTP 404
for 2016. The config validator requires `short-header` when
`year=2016`; the 2021 release supports all three descriptors.

## Update cadence

Once per Census (five-yearly). Existing releases (2016, 2021) won't
change. The next addition will be 2026 once ABS publishes it.

## Granularity

SA2 native:
- 2021 release: 2021_ASGS_Edition_3.
- 2016 release: 2016_ASGS_Edition_2.

The DataPack also publishes at SA1, SA3, SA4, GCCSA, LGA — but the
augmentor pulls only the SA2 file by default. In temporal mode the
pipeline reads the 2016 release using the ASGS Edition 2 SA2
boundaries, and the 2021 release using ASGS Edition 3 — the
`<dataset_id>_sa2_code_source` column carries the source-edition SA2
code when the row's release straddles an edition transition.

## Schema (variables exposed by the augmentor)

GCP exposes ~3000 columns across ~60 tables. Rather than enumerating
every column here, the dataset routes resolution through the existing
:class:`VariableCatalog` (see `src/census_augment/catalog.py`), which
parses the per-release `Metadata_{year}_GCP_DataPack*.xlsx` for the
canonical column list. Variable refs use the existing
`<TABLE_ID>.<column_name>` shape:

| Variable | Type | Description | 2016 | 2021 |
|---|---|---|---|---|
| `G01.Tot_P_P` | int | Total persons (place of usual residence) | ✅ | ✅ |
| `G02.Median_age_persons` | int | Median age, persons | ✅ | ✅ |
| `G02.Median_tot_hhd_inc_weekly` | int | Median total household income (weekly $) | ✅ | ✅ |
| `G04.Age_65_yr_above_P` | int | Persons aged 65+ | ✅ | ✅ |
| `G29.OneP_F_C_Tot` | int | One-parent families with children | ✅ | ✅ |
| `G29.Tot_F_C_Tot` | int | Total families with children | ✅ | ✅ |
| `G34.Total_motor_vehicles` | int | Total motor vehicles per dwelling | ✅ | ✅ |
| `G34.Total_dwellings` | int | Total dwellings (G34 row total) | ✅ | ✅ |
| `G37.R_Tot` | int | Tenure: rented, total | ✅ | ✅ |
| `G37.OPDs_Total` | int | Occupied private dwellings, total | ✅ | ✅ |
| `G43.E_FT_15ov_M` | int | Employed full-time aged 15+, male | ✅ | ✅ |
| `G43.E_FT_15ov_F` | int | Employed full-time aged 15+, female | ✅ | ✅ |
| `G43.LF_15ov_M` | int | Labour force aged 15+, male | ✅ | ✅ |
| `G43.LF_15ov_F` | int | Labour force aged 15+, female | ✅ | ✅ |
| `G62.OneMethod_CarAsDriver_P` | int | Travelled to work by car as driver, total persons | ❌ | ✅ |
| `G62.Tot_P` | int | Travelled to work, total persons | ❌ | ✅ |

The above is a small representative subset — many more variables are
available. Use `census-augment discover --search <term>` to find them.
The 2016 / 2021 ticks indicate the variable's existence in each
release's metadata; consult the catalog for definitive resolution.

## Fetch notes

- The metadata workbook has shifted between releases (R1 → R2 within
  2021, and again between 2016 and 2021); the parser tolerates the
  variants through case-insensitive sheet-name candidates and an
  auto-detected header row. See spec §4.2 for the column-name
  conventions.
- 2016 metadata XLSX sheet names use sentence case
  ("Cell descriptors information", "Table number, name, population")
  where 2021 uses Title Case ("Cell Descriptors Information",
  "Table Number, Name, Population"). Both forms are in the parser's
  candidate list.
- The SA2 code column in the CSVs is `SA2_MAINCODE_2016` for 2016 and
  `SA2_MAINCODE_2021` for 2021. Both candidates are in the parser's
  lookup list.
- "Australia" / state-level aggregate rows are mixed into the SA2
  files in some releases. The parser filters strictly by SA2 code
  length (= 9) so they don't leak into the join.

### GCP 2011 — user-supplied ZIP fallback (v2.3.0+)

The 2011 GCP DataPack lives behind ABS's login wall at
``https://www.censusdata.abs.gov.au/datapacks`` with no public direct
URL. The augmentor's auto-fetch can't ride that, but the parser
machinery itself handles the 2011 release fine (same shape as 2016 /
2021). Power users who manually download the ZIP can plug it in two
ways:

**Option 1 — `local_zip` constructor parameter:**

```python
from pathlib import Path
from census_augment.config import CensusConfig
from census_augment.data_sources.datapacks import DataPacksDataSource

ds = DataPacksDataSource(
    census=CensusConfig(year=2011, asgs_edition=1, datum="GDA94"),
    base_url=...,  # doesn't matter; not contacted
    root=<cache_root> / "census" / "2011",
    local_zip=Path("~/Downloads/2011_BCP_SA2_for_AUS_short-header.zip").expanduser(),
)
```

**Option 2 — `CENSUS_AUGMENT_DATAPACK_LOCAL_ZIP` environment variable:**

```bash
export CENSUS_AUGMENT_DATAPACK_LOCAL_ZIP=~/Downloads/2011_BCP_SA2_for_AUS_short-header.zip
census-augment run --config config-2011.yaml
```

**Option 3 — drop it at the expected cache path:**

If the user manually copies the ZIP to
``<cache_root>/census/2011/2011_BCP_SA2_for_AUS_short-header.zip``,
the standard ``fetch()`` skips the download and uses the cached file.
No config changes needed.

In all three cases, the rest of the parser (extract → CSV / metadata
parse → table load) is unchanged from the 2016 / 2021 paths.
``temporal`` mode reads 2011 from the ASGS Edition 1 boundary
(2,214 SA2s, GDA94 / EPSG:4283) — see ``edition_1_spec()`` in
``data_sources/_edition.py``.

## Suppression / privacy notes

- ABS applies random perturbation to small counts in GCP. Sub-totals
  may not exactly match published totals; absorb in calculations,
  don't assert equality.
- Cells with very low counts can come through as zero-perturbed —
  null is *not* used as a sentinel here (zero is the literal value).

## Suggested derived features

- `pct_drive_to_work` — see `features/pct_drive_to_work.md`
- `pct_renters` — see `features/pct_renters.md`
- `pct_aged_65_plus` — see `features/pct_aged_65_plus.md`
- `pct_employed_full_time` — see `features/pct_employed_full_time.md`
- `pct_one_parent_family` — see `features/pct_one_parent_family.md`
- `motor_vehicles_per_dwelling` — see `features/motor_vehicles_per_dwelling.md`

## Sources / citations

- Landing: https://www.abs.gov.au/census/find-census-data/datapacks
- Methodology (2021): https://www.abs.gov.au/methodologies/2021-census-population-housing-methodology
- Methodology (2016): https://www.abs.gov.au/methodologies/2016-census-population-housing-methodology
- Licence: CC-BY-4.0 per ABS terms of use
