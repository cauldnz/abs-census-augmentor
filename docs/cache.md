# Cache reference

`census-augment` caches everything it downloads from ABS / data.gov.au / G-NAF so re-runs are fast and offline-friendly. This page documents what's on disk, how big it gets, and how to clear it.

← [back to docs index](index.md)

## Default locations

Both ABS downloads and the geocoding cache live in the platform user cache by default:

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

## What gets cached, where, and how big

The tool splits cache into two roots: `data/` (everything downloaded from upstream sources) and `cache/` (geocoding lookups). Sub-tree breakdown:

### `<data_dir>/` — ABS / G-NAF downloads

ASGS-edition-keyed subdirs let multiple boundary editions coexist for
temporal-mode runs that touch more than one. The configured
`census.year` (currently 2021 by default) selects which subdir gets
populated during cross-sectional runs.

| Subdir | Contents | Approximate size | Invalidation |
|---|---|---|---|
| `boundaries/<year>/` | ASGS SA2 shapefile ZIP (`SA2_<year>_AUST_SHP_<datum>.zip`) + extracted `.shp` / `.dbf` / `.prj` / `.shx` + a `<shp>.feather` sidecar. | ~50 MB per edition | `census-augment fetch --refresh --boundaries`. |
| `census/<year>/` | GCP DataPack ZIP + extracted CSVs (one per G## table) + metadata XLSX + a `<metadata-xlsx>.<descriptor>.parsed.pkl` sidecar for fast openpyxl-skipping re-reads. | ~40 MB per edition | `census-augment fetch --refresh --census`. |
| `mb/<year>/` | Mesh Block correspondence shapefile (MB→SA2 fast-path resolver). | ~50 MB per edition | `census-augment fetch --refresh --boundaries`. |
| `seifa/` | SEIFA SA2 workbooks per release (`seifa-2021.xlsx`, `seifa-2016.xls`) + parsed parquet sidecars. | ~150 KB (2021) / ~700 KB (2016) | Dataset-level refresh. |
| `erp_by_sa2/` | ERP SA2 XLSX (`erp-sa2-{year}.xlsx`) per release + parsed parquet sidecar. | ~1 MB per release | Same. |
| `dss_payments/` | DSS quarterly XLSX (`dss-{YYYY-Qn}.xlsx`) per release + parsed parquet sidecar. | ~3 MB per release | Same. |
| `abs_personal_income/` | ABS Personal Income Table 1 XLSX (`abs-personal-income-{FY}.xlsx`) per release + parsed parquet sidecar. | ~500 KB per release | Same. |
| `gnaf/{YYYYMM}/` | G-NAF parquet files (only when `geocoding.gnaf.mode: cache`). | **~10 GB per release** | `census-augment fetch --gnaf --refresh`. |

**G-NAF is by far the biggest item.** If you're disk-constrained:

- Set `geocoding.gnaf.mode: remote` so DuckDB streams from S3 instead of downloading. See [G-NAF setup](gnaf-setup.md).
- Or set `geocoding.providers: [nominatim]` to skip G-NAF entirely (~slower for any non-trivial input set, but zero local data).

### `<cache_dir>/geocoding/` — geocoding lookups

| Contents | Approximate size | Invalidation |
|---|---|---|
| Hash-keyed JSON shards per Nominatim lookup (`<hash>.json`). Each shard holds one geocoded address's normalised form + lat/lon + provider tier. | ~100 bytes per address — typically <10 MB even for large workloads. | Delete the directory to clear; the next run re-geocodes from scratch. Stale entries also get evicted lazily if the address normaliser's output for the input changes. |

G-NAF lookups don't hit this cache — they query the local parquet (or remote httpfs) directly.

## Sidecar caches (perf optimisation; not separately invalidatable)

Two sidecar files sit next to the heaviest parsed artefacts and short-circuit subsequent loads (issue #43):

- `<metadata-xlsx>.<descriptor>.parsed.pkl` — pickled `DataPackMetadata` next to the DataPack metadata Excel. Reading the parsed result back is ~94% faster than re-running openpyxl over all 119 tables.
- `<boundary>.feather` — geopandas-native feather of the SA2 GeoDataFrame next to the ASGS `.shp`. ~84% faster than re-reading the shapefile.

Both are keyed on the source file's mtime — `fetch(refresh=True)` re-extracts the ZIPs, bumps the mtimes, and the sidecars invalidate automatically. Corrupt or schema-mismatched sidecars are silently ignored at load time; the parser falls back to the canonical source and overwrites the sidecar.

## Clearing the cache

For a clean slate of everything:

```bash
# Linux / macOS
rm -rf ~/.cache/census-augment

# Windows
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\census-augment\Cache"
```

For a selective refresh (preferred over `rm -rf`):

```bash
# Re-pull the SA2 boundaries (forces a fresh ABS download)
census-augment fetch --config config.yaml --boundaries --refresh

# Re-pull the GCP DataPack
census-augment fetch --config config.yaml --census --refresh

# Re-pull G-NAF
census-augment fetch --config config.yaml --gnaf --refresh
```

`--refresh` only affects the targeted sources; everything else stays cached.

## When to clear

You generally **don't need to**. The atomic-rename download pattern means an aborted fetch leaves no half-files, and the sidecar caches invalidate automatically when their source mtimes change. Clearing manually is only useful when:

- ABS has published a new edition you want to pick up immediately (rather than waiting for the next quarterly release the upstream landing page surfaces).
- You're debugging a parsing issue and want to verify the cached XLSX isn't corrupt.
- You want to free disk space and don't currently need G-NAF (the 10 GB item is the only one worth specifically targeting).

## Inspecting cache state

```bash
# Total cache size on disk
du -sh ~/.cache/census-augment

# G-NAF release + path + size
census-augment gnaf-info --config config.yaml
```

For a one-glance overview of the four registered datasets' cache state, the dataset modules expose `is_cached` properties (library use):

```python
from census_augment.datasets._seifa import SeifaDataSource
from pathlib import Path

ds = SeifaDataSource(root=Path.home() / ".cache" / "census-augment" / "data" / "seifa")
print(ds.is_cached)  # bool
```
