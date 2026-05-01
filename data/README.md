# data/

Optional project-local cache for ABS downloads (boundaries + Census DataPacks).

> **By default, ABS downloads land in your platform's user cache** —
> e.g. `~/.cache/census-augment/data/` on Linux,
> `~/Library/Caches/census-augment/data/` on macOS,
> `%LOCALAPPDATA%\census-augment\Cache\data\` on Windows.
> See [`spec.md`](../spec.md) §9 for the full table.
>
> This directory is only used if you explicitly point the tool at it
> with `--data-dir ./data` (CLI), `data_dir=Path("data")` (library), or
> `CENSUS_AUGMENT_DATA_DIR=./data` (env var).

If used, contents are gitignored — only this README and `.gitignore`
are checked in.

See [`spec.md`](../spec.md) §4 (data sources) and §9 (caching strategy).
