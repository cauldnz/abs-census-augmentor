# GCP table schema reference dumps

Snapshot of `census-augment discover --table <id>` output for every
GCP 2021 DataPack table referenced by a registered PRESET in
`features/`. Captured 2026-05-10 as part of the issue #23 fix.

These exist so that:

1. PRESET front-matter (numerator / denominator field references) can
   be reviewed against a checked-in artifact, not against "the live
   DataPack" — the
   [Real Data First](../../../CLAUDE.md#real-data-first) rule's
   reviewability requirement.
2. A future change to a PRESET can `diff` its column refs against
   these dumps before pushing.
3. A future GCP release (2026 onwards) can use these as the v2021
   baseline.

The same data is also exercised live by
`tools/verify_real_parsers.py::verify_preset_resolution()`, which
resolves every PRESET's `source_fields()` against the loaded
catalog and fails if any column doesn't exist.

## Re-generating

```bash
mkdir -p tests/fixtures/gcp-schemas
for t in G01 G02 G04A G04B G29 G34 G37 G43 G62; do
    uv run census-augment discover \
        --config tools/demo/config.yaml \
        --table "$t" > "tests/fixtures/gcp-schemas/${t}.txt"
done
```

Re-generate when adding a new PRESET that touches a previously-unused
table, or when the GCP release changes (e.g. 2026 lands).
