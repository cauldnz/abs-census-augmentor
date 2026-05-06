# README demo

This directory holds the inputs for the animated demo embedded at the top of the project README.

## What's here

- **`input.csv`** — five famous Australian locations (Opera House, MCG, Bondi, Story Bridge, Adelaide Central Market) with lat/lon. Recognisable enough that the SA2-name output reads naturally.
- **`config.yaml`** — minimal config: lat/lon-only inputs (no Nominatim calls), three census variables (median age, median household income, population).
- **`demo.tape`** — the [VHS](https://github.com/charmbracelet/vhs) script that drives the recording.
- **`Dockerfile`** — a custom VHS image with `census-augment` and the unix tools the tape uses (`cut`, `column`) baked in.
- **`render.ps1`** / **`render.sh`** — one-command entry points that build the image and run vhs against the tape with the right mounts.
- **`output.csv`** *(generated, gitignored)* — produced when you render the demo.

## Re-rendering the GIF

Whenever the CLI surface or run-summary format changes, re-render so the README GIF stays accurate.

### Why Docker?

Native VHS on Windows is fragile — it relies on `bash.exe` and `ttyd.exe` being discoverable, which often hangs in initialisation. Going through Docker bypasses every Windows-toolchain issue: the vhs image is Linux, all unix tooling Just Works, and the same `Dockerfile` reproduces identically on macOS / Linux / Windows. One workflow for all maintainers.

### One-command render

From the **repo root**:

```powershell
# Windows (PowerShell)
.\tools\demo\render.ps1
```

```bash
# macOS / Linux
./tools/demo/render.sh
```

That's it. The script:

1. Verifies Docker is reachable.
2. Pre-warms the host's ABS cache (`uv run census-augment fetch`) if it's not already populated, so the demo runs offline.
3. Builds (or reuses, if cached) the `census-augment-vhs` Docker image.
4. Runs vhs against `tools/demo/demo.tape` with the repo and the host's ABS cache mounted into the container.
5. Drops the rendered GIF at `docs/demo.gif`.

### Timing

| Run | Wallclock | Why |
| --- | --- | --- |
| First ever | ~3–5 min | Pull base vhs image (~150 MB) + apt-install Python + pip-install census-augment deps (pandas, geopandas, pyarrow, …) |
| Subsequent (no source change) | ~30 s | All Docker layers cached; only the recording actually runs |
| After source change | ~1–2 min | The COPY layers re-run, so pip install repeats; downstream layers stay cached |

The visible GIF is ~20 s regardless.

### Prerequisites

- **Docker Desktop** running (Windows / macOS) or `dockerd` reachable (Linux).
- **`uv`** on PATH (the script uses `uv run census-augment fetch` if the cache needs populating).

### Commit the result

```bash
git add docs/demo.gif
git commit -m "Refresh README demo GIF"
git push
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

If you change the input data, regenerate `output.csv` once with `uv run census-augment run --config tools/demo/config.yaml` to confirm the projected columns (`cut -d, -f1,9,11,12,13`) still pick the right fields.

## Why lat/lon-only inputs?

The demo deliberately avoids exercising any geocoder so the GIF is reproducible without a populated G-NAF cache and without making live Nominatim calls (which are rate-limited at 1 req/sec). All five rows take the spatial-join path — fast, deterministic, and offline once the boundary file is cached.

## Running VHS natively (not recommended)

If you really want to skip Docker, install VHS plus its `ttyd` dependency directly. This works on macOS and Linux. On Windows, expect headaches around `bash.exe` discovery — the tape uses unix one-liners (`cut`, `column`) that are easier to consume from a Linux shell.

```bash
# macOS
brew install vhs ttyd
vhs tools/demo/demo.tape

# Linux
go install github.com/charmbracelet/vhs@latest
sudo apt-get install -y ttyd bsdmainutils
vhs tools/demo/demo.tape
```
