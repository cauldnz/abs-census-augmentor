# README demos

VHS recordings for the README's animated explainers. All demos share
the rendering infrastructure (`Dockerfile`, `render.sh`,
`render.ps1`); only the tape and the supporting config differ.

## What's here

| File | Used by | What |
| --- | --- | --- |
| `input.csv` | `demo.tape` | Five famous Australian locations (Opera House, MCG, Bondi, Story Bridge, Adelaide Central Market) with lat/lon. |
| `config.yaml` | `demo.tape` | Headline demo — mixes Census GCP (`G02.*`) and SEIFA (`SEIFA.*`) variables to show v1.3 / v1.4 multi-namespace dispatch. |
| `demo.tape` | renders → `docs/demo.gif` | Headline README GIF. |
| `discover-datasets.tape` | renders → `docs/discover-datasets.gif` | Walks the `census-augment discover` CLI: list datasets, drill into one, list PRESETs. No augmentation run, so cache is unused. |
| `Dockerfile` | all demos | Custom VHS image with `census-augment` + unix tools (`cut`, `column`) baked in. |
| `render.sh` / `render.ps1` | all demos | One-command entry points. Take an optional slug arg picking the tape (default: `demo`). |
| `output.csv` *(generated, gitignored)* | host-side pre-warm + the tape's recorded run | Last-rendered headline output. |

### Deferred (blocked on issue #23)

A third demo, `preset-features.gif`, is planned to show off PRESET
features (the v1.4 first-class PRESET pipeline integration). It's
gated on [#23](https://github.com/cauldnz/abs-census-augmentor/issues/23)
— the v1.3 PRESET catalogue references column names that don't exist
in the real GCP DataPack, so PRESETs can't be exercised end-to-end
yet. Once #23 lands, the demo's tape + config will be added back to
this directory and rendered.

## Rendering

From the **repo root**:

```bash
# macOS / Linux / WSL / devcontainer
./tools/demo/render.sh                        # docs/demo.gif (headline)
./tools/demo/render.sh discover-datasets      # docs/discover-datasets.gif

# Windows PowerShell
.\tools\demo\render.ps1
.\tools\demo\render.ps1 discover-datasets
```

The script:

1. Verifies Docker is reachable.
2. Pre-warms the host's ABS cache (`census-augment fetch` for boundaries
   + GCP, then a `census-augment run` against the demo's config so
   any registered-dataset caches the tape touches — SEIFA, etc. — are
   populated before VHS starts recording).
3. Builds (or reuses, if cached) the `census-augment-vhs` Docker image.
4. Runs vhs against the chosen tape with the repo and the host's ABS
   cache mounted into the container.
5. Drops the rendered GIF at `docs/<slug>.gif`.

### Why Docker?

Native VHS on Windows is fragile — it relies on `bash.exe` and
`ttyd.exe` being discoverable, which often hangs in initialisation.
Going through Docker bypasses every Windows-toolchain issue: the vhs
image is Linux, all unix tooling Just Works, and the same `Dockerfile`
reproduces identically on macOS / Linux / Windows / WSL /
devcontainer. One workflow for all maintainers.

### Timing

| Run | Wallclock | Why |
| --- | --- | --- |
| First ever | ~3–5 min | Pull base vhs image (~150 MB) + apt-install Python + pip-install census-augment deps |
| Subsequent (no source change) | ~30 s | All Docker layers cached; only the recording actually runs |
| After source change | ~1–2 min | The `COPY` layers re-run, so `pip install` repeats; downstream layers stay cached |

The visible portion of each GIF is ~20–30 s regardless.

### Prerequisites

- **Docker Desktop** running (Windows / macOS) or `dockerd` reachable
  (Linux). The dev container in `.devcontainer/` mounts the host's
  Docker socket so renders work from inside the container too.
- **`uv`** on PATH (the script uses `uv run census-augment fetch` and
  `uv run census-augment run` for the host-side pre-warm steps).

### Commit the result

```bash
git add docs/demo.gif docs/discover-datasets.gif
git commit -m "Refresh README demo GIFs"
git push
```

## Tweaking demos

VHS tapes are plain text with
[VHS commands](https://github.com/charmbracelet/vhs#vhs-command-reference).
Common edits:

| Want to... | Change |
| --- | --- |
| Slow down the typing | `Set TypingSpeed 80ms` (default 50 ms) |
| Larger / smaller font | `Set FontSize 18` |
| Different colours | `Set Theme "Catppuccin Mocha"` (any [tinted-theme name](https://github.com/charmbracelet/vhs/blob/main/themes.json)) |
| Wider / taller frame | `Set Width 1300` / `Set Height 800` |
| Add a new scene | `Type "..." Enter Sleep Ns` between the existing ones |

If you change the input data or the config's variables, regenerate
the relevant `output.csv` once with the matching `census-augment run`
so the tape's `cut` column indices stay aligned with the schema.

## Why lat/lon-only inputs?

The headline demo deliberately avoids exercising any geocoder so it's
reproducible without a populated G-NAF cache and without making live
Nominatim calls (which are rate-limited at 1 req/sec). All five rows
take the spatial-join path — fast, deterministic, and offline once
the boundary file is cached.

`discover-datasets.tape` doesn't run augmentation at all, so it's
even simpler: it just calls `census-augment discover` against the
local markdown specs. No network, no cache.

## Running VHS natively (not recommended)

If you really want to skip Docker, install VHS plus its `ttyd`
dependency directly. This works on macOS and Linux. On Windows,
expect headaches around `bash.exe` discovery — the tapes use unix
one-liners (`cut`, `column`) that are easier to consume from a Linux
shell.

```bash
# macOS
brew install vhs ttyd
vhs tools/demo/demo.tape
vhs tools/demo/discover-datasets.tape

# Linux
go install github.com/charmbracelet/vhs@latest
sudo apt-get install -y ttyd bsdmainutils
vhs tools/demo/demo.tape
```
