# CLI example

A small mixed-input dataset and config showing `census-augment run` end-to-end.

## Run it

From the repo root:

```bash
# Install (one-off)
uv pip install -e ".[dev]"

# Run the pipeline. First run downloads ~90 MB of ABS data into the
# user cache; subsequent runs are instant.
census-augment run --config examples/cli/config.yaml
```

The pipeline writes the enriched CSV to `examples/cli/output.csv`. Open it to see the original columns plus `geo_*`, `sa2_*`, and the configured enrichment columns.

## What's in the input

`input.csv` has six rows that exercise every code path:

| Row | Demonstrates |
|---|---|
| Sydney Opera House | Input lat/lon, falls in a Sydney SA2. |
| Melbourne MCG | Input lat/lon, falls in East Melbourne SA2. |
| Brisbane CBD | Input lat/lon, falls in Brisbane City SA2. |
| Sydney via address | Address-only — geocoded via Nominatim. |
| Open ocean | Has lat/lon but the point falls outside any SA2 polygon (water). |
| Bad row no locator | No address, no lat/lon — flagged as a geocoding failure. |

The run summary at the end categorises each row.

## Heads up about the placeholder user-agent

`config.yaml` ships with `user_agent: "census-augment-example/0.1 (someone@example.com)"`. Nominatim's policy ([https://operations.osmfoundation.org/policies/nominatim/](https://operations.osmfoundation.org/policies/nominatim/)) requires a *real* contact in the UA, and they sometimes 403 placeholder values. Before running, replace `someone@example.com` with your actual email — or just stick to lat/lon-only inputs (the lat/lon rows still show the full pipeline).

## Other commands to try

```bash
# Search the metadata for variables matching "income"
census-augment discover --config examples/cli/config.yaml --search income

# List all variables in table G02
census-augment discover --config examples/cli/config.yaml --table G02

# Validate that every variable in the config exists in the real DataPack
census-augment validate --config examples/cli/config.yaml --full

# DEBUG-level logging on any command
census-augment --verbose run --config examples/cli/config.yaml
```

## Notes

- The `output.csv` produced by the run isn't checked into git — `.gitignore` ignores it.
- The geocoding cache means re-running this example will *not* hit Nominatim again for the same addresses.
- See `spec.md` §11 for the full CLI reference, or run `census-augment --help`.
