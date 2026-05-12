# G-NAF setup

`census-augment` ships two G-NAF distribution modes (set via `geocoding.gnaf.mode`); pick whichever matches your environment.

← [back to docs index](index.md)

| Mode | What happens | Best for |
| --- | --- | --- |
| `cache` *(default)* | First call downloads the [gnaf-loader](https://github.com/minus34/gnaf-loader) snapshot (~10 GB across ~50 parquet files) from `s3://minus34.com/opendata/` to your user cache. Subsequent calls run entirely offline. | Production runs, large workloads, anywhere bandwidth is cheaper than disk-divided-by-time. |
| `remote` | DuckDB queries the same parquet files directly over HTTPS via its `httpfs` extension. **No download.** Each query pulls only the parquet metadata + the columns/rows it needs. | Prototyping, CI, disk-constrained environments, occasional one-off queries. |

## Remote mode

```yaml
geocoding:
  providers: [gnaf, nominatim]
  gnaf:
    mode: remote
    release: latest        # or "202602"
```

That's it. No prefetch step. Open a notebook, run `Pipeline.augment(df)`, DuckDB does the rest.

**Trade-offs of remote mode:**

- *Speed.* Each query is HTTPS-bound — single Tier-1 lookup is ~100ms (parquet metadata fetch + ranged read). Tier 2/3 (postcode-bucket scans) read more bytes. Fine for thousands of addresses; not ideal for hundreds of thousands.
- *Bandwidth.* Cumulative reads can get pricey. A workload that does ~10k geocodes might pull ~500 MB across queries; if you'll re-run that workload many times, cache mode pays off.
- *Offline use.* Doesn't work without network. If your laptop's spotty, prefer cache.
- *No local schema validation up-front* — the `httpfs` extension itself has to be installable (DuckDB downloads it once on first use, then caches in `~/.duckdb/extensions/`).

**Bucket layout auto-detection.** Two layouts are recognised:

1. *gnaf-loader* (the production [gnaf-loader](https://github.com/minus34/gnaf-loader) bucket): G-NAF data lives in named subdirectories. The geocoder reads from `geoparquet/address_principal_census_{year}_boundaries/` — gnaf-loader's denormalised join of address principals with the ABS census boundary IDs. Source columns (`gnaf_pid`, `address`, `latitude`, `mb_{year}_code`, ...) are aliased to the uppercase schema the geocoder expects. Set `census.year` to pick `2016` vs `2021` boundaries (default `2021`).
2. *Legacy / bring-your-own*: a flat parquet at the release root with already-uppercase columns. Used by users who pre-build G-NAF from the official Geoscape PSV.

Detection runs on every `open_connection()`; gnaf-loader wins when both layouts coexist. For non-default layouts on self-hosted mirrors (MinIO, R2, ...), combine `data_sources.gnaf_s3_https_endpoint` with `data_sources.gnaf_parquet_filter` (regex against the relative key — only consulted under the legacy code path).

## One-shot prefetch (recommended for cache mode)

Pull the data ahead of your first run so it isn't on the critical path of your first augmentation:

```bash
census-augment fetch --config config.yaml --gnaf
```

This:

1. Anonymously lists `s3://minus34.com/opendata/geoscape-*/` to find the latest release (or honours `geocoding.gnaf.release: "202602"` if you've pinned one).
2. Downloads every `*.parquet` under `.../geoparquet/` to `<data_dir>/gnaf/{YYYYMM}/` with atomic-rename semantics — interrupted runs resume from the partial cache, no half-files left behind.
3. Fetches the small (~50 MB) Mesh Block correspondence shapefile alongside, since the `mb_code → SA2` fast path depends on it.

## Refreshing to a newer release

```bash
census-augment fetch --config config.yaml --gnaf --refresh
```

With `release: "latest"` (the default), `--refresh` re-checks S3 to pick up any newer quarterly that's dropped since you last fetched. With an explicit `release: "202602"`, `--refresh` re-downloads that same release.

## Inspecting the cache

```bash
census-augment gnaf-info --config config.yaml
```

Prints the resolved release, the on-disk path, and the cached size in MB.

## Pinning a specific release

For reproducibility (e.g. running the same pipeline against the same data at different times):

```yaml
geocoding:
  gnaf:
    release: "202602"   # default is "latest"
```

## Bringing your own G-NAF parquet

If your organisation builds G-NAF from the official Geoscape PSVs (data.gov.au) instead of using gnaf-loader, drop your own `*.parquet` files into `<data_dir>/gnaf/{YYYYMM}/` — the auto-download is skipped when the cache is already populated.

Two ways to lay out the file(s):

- **Match the gnaf-loader convention** (preferred): place the parquet at `<data_dir>/gnaf/{YYYYMM}/address_principal_census_{year}_boundaries/your-file.parquet` with lowercase columns (`gnaf_pid`, `address`, `latitude`, `longitude`, `postcode`, `mb_{year}_code`). The view aliases them to the uppercase schema for you.
- **Legacy flat layout**: place the parquet at `<data_dir>/gnaf/{YYYYMM}/your-file.parquet` with already-uppercase columns. Required columns: `ADDRESS_DETAIL_PID`, `ADDRESS_LABEL` (the pre-formatted "1 GEORGE STREET SYDNEY NSW 2000" string), `LATITUDE`, `LONGITUDE`, `MB_CODE` (11-digit ABS Mesh Block), `POSTCODE`. The parser raises loudly if any are missing.

## Opting out of G-NAF entirely

If you'd rather not deal with a 10 GB cache, switch to Nominatim-only:

```yaml
geocoding:
  providers: [nominatim]
  nominatim:
    user_agent: "..."
```

Nominatim is rate-limited (1 req/sec default), so this is much slower than G-NAF for any non-trivial input set, but it requires zero local data.

## Attribution

> Incorporates or developed using G-NAF © Geoscape Australia licensed by the Commonwealth of Australia under the Open Geo-coded National Address File (G-NAF) End User Licence Agreement.

The Open G-NAF EULA permits this kind of geocoding-and-enrichment use. It does *not* permit using the data to generate or compile addresses for sending mail unless each address has been verified against a secondary source.
