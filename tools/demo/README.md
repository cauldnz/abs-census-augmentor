# README demo

This directory holds the inputs for the animated demo embedded at the top of the project README.

## What's here

- **`input.csv`** — five famous Australian locations (Opera House, MCG, Bondi, Story Bridge, Adelaide Central Market) with lat/lon. Recognisable enough that the SA2-name output reads naturally.
- **`config.yaml`** — minimal config: lat/lon-only inputs (no Nominatim calls), three census variables (median age, median household income, population).
- **`demo.tape`** — the [VHS](https://github.com/charmbracelet/vhs) script that drives the recording.
- **`output.csv`** *(generated, gitignored)* — produced when you render the demo.

## Re-rendering the GIF

Whenever the CLI surface or run-summary format changes, re-render so the README GIF stays accurate.

### 1. Install VHS

VHS is a scripted terminal recorder by Charm — no manual screen-recording, no flickering windows.

```bash
# macOS
brew install vhs

# Linux (or Windows via WSL)
go install github.com/charmbracelet/vhs@latest

# Windows (native)
scoop install vhs
# or: winget install charmbracelet.vhs
```

You'll also need a working `ttyd` install (VHS uses it under the hood) — most package managers pull it in automatically.

### 2. Render

From the **repo root** (paths in the tape are repo-relative):

```bash
vhs tools/demo/demo.tape
```

This:

1. Pre-warms the ABS boundary + DataPack cache off-camera (~15 s on a cold cache, instant on a warm one).
2. Records three short scenes: the input CSV, the `run` command, and the projected output.
3. Writes the GIF to `docs/demo.gif`.

Total render time: ~30–60 s on a cold cache, ~25 s on a warm one. The visible GIF length is ~20 s regardless.

### 3. Commit the result

```bash
git add docs/demo.gif
git commit -m "Refresh README demo GIF"
```

## Tweaking the demo

The tape file is plain text with [VHS commands](https://github.com/charmbracelet/vhs#vhs-command-reference). Common edits:

| Want to... | Change |
| --- | --- |
| Slow down the typing | `Set TypingSpeed 80ms` (default 50ms) |
| Larger / smaller font | `Set FontSize 18` |
| Different colours | `Set Theme "Catppuccin Mocha"` (any [tinted-theme name](https://github.com/charmbracelet/vhs/blob/main/themes.json)) |
| Wider / taller frame | `Set Width 1300` / `Set Height 700` |
| Add a new scene | Add `Type "..." Enter Sleep Ns` between the existing ones |

If you change the input data, regenerate `output.csv` once with `census-augment run --config tools/demo/config.yaml` to confirm the projected columns (`cut -d, -f1,9,11,12,13`) still pick the right fields.

## Why lat/lon-only inputs?

The demo deliberately avoids exercising any geocoder so the GIF is reproducible without a populated G-NAF cache and without making live Nominatim calls (which are rate-limited at 1 req/sec). All five rows take the spatial-join path — fast, deterministic, and offline once the boundary file is cached.
