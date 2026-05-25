# CLI usage

`census-augment` is a Typer-based CLI for augmenting CSVs end-to-end without touching Python. Same engine as the library API; just files in, files out.

← [back to docs index](index.md)

## Common commands

```bash
# Augment a CSV end-to-end
census-augment run --config config.yaml

# Discover what variables the DataPack offers
census-augment discover --config config.yaml --search income
census-augment discover --config config.yaml --table G02

# List all registered datasets and PRESET features
census-augment discover --config config.yaml --datasets
census-augment discover --config config.yaml --features

# Inspect a single dataset's resolved schema
census-augment discover --config config.yaml --dataset seifa

# Validate a config (with --full also checks variable refs against the DataPack)
census-augment validate --config config.yaml --full

# Pre-fetch ABS data (saves the first --run from doing the download)
census-augment fetch --config config.yaml --boundaries --census --gnaf

# Inspect the resolved G-NAF release / cache size
census-augment gnaf-info --config config.yaml
```

Run `census-augment --help` for the full subcommand list, or `census-augment <subcommand> --help` for per-command flags.

## Sample CLI invocation

A complete config + input CSV + walkthrough is in [`examples/cli/`](../examples/cli/).

## Config file

The full config schema (with every option documented inline) is in [`config.example.yaml`](../config.example.yaml). For a structured tour of the major sections — variables, geocoding, data sources, output — see [Configuration](configuration.md).
