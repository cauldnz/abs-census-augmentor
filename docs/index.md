# Handbook

Detailed documentation for `census-augment`. The [README](../README.md) is the elevator pitch; this is the operating manual.

## How to use the tool

- [Library usage](usage-library.md) — `Pipeline.augment(df)`, `AugmentResult`, notebook patterns.
- [CLI usage](usage-cli.md) — full `census-augment` command reference.
- [Configuration](configuration.md) — `config.yaml` schema, env vars, cache locations.

## Data sources

- [G-NAF setup](gnaf-setup.md) — cache vs remote mode, prefetch, bring-your-own parquet, attribution.

## Working on the tool

- [Development](development.md) — Makefile targets, dev container, the test suite.

## Reference

- [`../spec.md`](../spec.md) — design specification; the source of truth for behavioural decisions.
- [`../CLAUDE.md`](../CLAUDE.md) — contributor and AI-agent conventions.
- [`../CHANGELOG.md`](../CHANGELOG.md) — version-by-version user-facing change log.
- [`../tools/README.md`](../tools/README.md) — real-data verification harness (opt-in; not part of CI).
- [`../examples/`](../examples/) — runnable usage scripts.

## Demos

The README embeds three demos directly. The source GIFs and per-scene PNG frames live here:

- `demo.gif` — end-to-end CSV augmentation.
- `discover-datasets.gif` — `census-augment discover` over the registered datasets and PRESET features.
- `preset-features.gif` — declaring a `PRESET.<id>` ratio in a config and running it.
- `frames/` — per-scene PNGs that back the README's collapsible scene strips.

Re-render via `make demos`. The README's scene strips are kept in sync automatically by [`../tools/demo/refresh_readme_frames.py`](../tools/demo/refresh_readme_frames.py).
