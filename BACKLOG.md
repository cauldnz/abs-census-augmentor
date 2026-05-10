# Backlog

Tracking ideas worth doing but not in the current release. Items here
have been thought-through enough to be actioned by anyone who picks
them up; they're not raw brainstorms.

## VHS terminal recordings (animated demos)

The README embeds VHS-rendered GIFs to show what the tool actually
does. Tape files live at `tools/demo/<slug>.tape`; rendering is
one-command via `tools/demo/render.sh` (or `.ps1`).

### Authored, ready to render

These have tape files committed. Render them via the dev container
(see `.devcontainer/`) or any host with Docker reachable. Output
lands at `docs/<slug>.gif`.

- **`docs/demo.gif`** — headline demo. Shows mixing GCP + SEIFA in one
  config. Replaces the v1.0-era headline demo.
- **`docs/discover-datasets.gif`** — walks `census-augment discover
  --datasets` / `--dataset seifa_2021` / `--features` and shows the
  underlying markdown spec format. No augmentation run, so cache is
  unused.

### Deferred

- **`docs/preset-features.gif`** — gated on
  [#23](https://github.com/cauldnz/abs-census-augmentor/issues/23).
  PRESETs reference column names that don't exist in the real GCP
  DataPack, so the demo can't be rendered until those refs are
  fixed. Tape design is sketched in #23's discussion; re-add the
  `tools/demo/preset-features.tape` + `preset-config.yaml` files
  once the underlying issue lands.

- **`docs/seifa-augmentation.gif`** — superseded by `docs/demo.gif`
  (the headline) which already shows SEIFA in action; revisit if
  there's appetite for a SEIFA-specific deep-dive demo with
  inner-Sydney-vs-regional contrast.

- **`docs/dss-payments-resolution.gif`** — niche. Shows DSS quarterly
  release resolution via CKAN. Lower priority than the headline
  demos.

### Notes for whoever renders these

- `tools/demo/render.ps1` (Windows) and `tools/demo/render.sh` (macOS
  / Linux / WSL / devcontainer) take an optional slug arg picking
  the tape (default: `demo`). Each new demo needs a matching
  `.tape` file alongside.
- Keep individual GIFs to 20–30 seconds and < 500 KB.
- The cache pre-warm pattern in the render scripts handles
  registered datasets; the tape's own `Hide`/`Show` blocks handle
  any final cache touch-ups.

## Future datasets (deferred from #15)

Tracked but not in v1.3 scope. Each is a meaningful piece of work
(per-source fetcher, schema bookkeeping, real-data validation) and
should land as its own PR when the upstream demand surfaces.

- AIHW Geographic Health Atlases (SA3-native primarily; needs SA3
  support first).
- ABS Building Approvals (LGA-native; concordance handling is the
  interesting bit).
- Geoscape Buildings (point-level; aggregation strategy choice).
- ABS National Health Survey (state / capital city level — exception
  rather than rule for SA2 data).
- NSW BOCSAR / VIC Crime Statistics / etc. (single-state datasets;
  state-by-state stitching is a separate engineering problem).

## Future PRESET features (deferred from #11)

The v1.3 catalog ships six PRESETs sourced entirely from `gcp_2021`,
and v1.4 (#18) lands first-class pipeline integration so any config
can write `variables: {pct_renters: PRESET.pct_renters}` directly.
Cross-dataset features (e.g. `pct_age_pension_recipients` =
`DSS.age_pension_recipients` / `ERP.population_65_plus`) are now
implementable end-to-end — the only remaining blocker is:

1. ERP exposing age-band breakdowns (currently total population
   only; spec example file describes age-band columns but the real
   ERP DS0003 is total-only).

When ERP gets age bands, cross-dataset PRESETs become a markdown-only
authoring exercise.

## Other deferred items

- Automated G-NAF S3 fetch is shipped (v1.1). The `gnaf-loader`
  bucket is the canonical default; users who want a different mirror
  can override `data_sources.gnaf_s3_https_endpoint` and
  `data_sources.gnaf_parquet_filter`.
- Messy/fuzzy address test corpus for exercising G-NAF Tiers 2/3
  against real-world ugly inputs. Sourcing AU addresses from a
  *non*-G-NAF source so we test the matcher rather than test G-NAF
  against itself is the design problem.
