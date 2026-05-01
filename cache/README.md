# cache/

Optional project-local cache for geocoded address lookups (sharded JSON).

> **By default, the geocoding cache lives in your platform's user cache** —
> e.g. `~/.cache/census-augment/cache/` on Linux,
> `~/Library/Caches/census-augment/cache/` on macOS,
> `%LOCALAPPDATA%\census-augment\Cache\cache\` on Windows.
> See [`spec.md`](../spec.md) §9 for the full table.
>
> This directory is only used if you explicitly point the tool at it
> with `--cache-dir ./cache` (CLI), `cache_dir=Path("cache")` (library),
> or `CENSUS_AUGMENT_CACHE_DIR=./cache` (env var).

If used, contents are gitignored — only this README and `.gitignore`
are checked in.

See [`spec.md`](../spec.md) §7.2 (geocoding cache) and §9 (caching strategy).
