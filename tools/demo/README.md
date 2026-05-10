# README demos

VHS recordings for the README's animated explainers. All demos share
the rendering infrastructure (`Dockerfile`, `render.sh`,
`render.ps1`); only the tape and the supporting config differ.

## What's here

| File | Used by | What |
| --- | --- | --- |
| `input.csv` | all tapes | Five famous Australian locations (Opera House, MCG, Bondi, Story Bridge, Adelaide Central Market) with lat/lon. |
| `config.yaml` | `demo.tape` | Headline demo config — mixes Census GCP (`G02.*`) and SEIFA (`SEIFA.*`) variables to show v1.3 / v1.4 multi-namespace dispatch. |
| `preset-config.yaml` | `preset-features.tape` | Three PRESET ratios (`pct_renters`, `pct_drive_to_work`, `pct_aged_65_plus`). |
| `demo.tape` | renders -> `docs/demo.gif` + 4× `docs/frames/demo-*.png` | Headline README GIF. |
| `discover-datasets.tape` | renders -> `docs/discover-datasets.gif` + 4× `docs/frames/discover-datasets-*.png` | Walks the `census-augment discover` CLI: list datasets, drill into one, list PRESETs. No augmentation run, so cache is unused. |
| `preset-features.tape` | renders -> `docs/preset-features.gif` + 4× `docs/frames/preset-features-*.png` | Shows a PRESET spec, then a config that uses three PRESETs, then the computed output. Unblocked once #23 (PRESET column refs) landed in v1.4.2. |
| `Dockerfile` | `--docker` mode only | Custom VHS image with `census-augment` + unix tools (`cut`, `column`) baked in. |
| `render.sh` / `render.ps1` | all demos | One-command entry points. Optional slug arg (default: `demo`). Flags: `--all` (render every tape), `--local` / `--docker` (force a render mode; default auto-detects). |
| `output.csv`, `preset-output.csv` *(generated, gitignored)* | host-side pre-warm + the tape's recorded run | Last-rendered outputs. |
| `.last-render.log` *(generated, gitignored)* | render.sh / render.ps1 | Per-tape vhs stdout/stderr captured during the most recent render. Useful for diagnosing tapes that render with exit 0 but produced a broken-looking GIF (the classic case: a `command not found` inside the recorded subshell). |

Each tape produces a GIF *and* per-scene PNG snapshots via VHS's
`Screenshot` directive. The PNGs land in `docs/frames/` and are
useful for embedding in static contexts (blog posts, Slack
previews, places where GIF animation doesn't reliably autoplay).
See [`docs/frames/README.md`](../../docs/frames/README.md) for the
full mapping.

## Rendering

From the **repo root**:

```bash
# macOS / Linux / WSL / devcontainer
./tools/demo/render.sh                        # docs/demo.gif (headline)
./tools/demo/render.sh discover-datasets      # docs/discover-datasets.gif
./tools/demo/render.sh preset-features        # docs/preset-features.gif
./tools/demo/render.sh --all                  # every tape in lexical order

# Windows PowerShell
.\tools\demo\render.ps1
.\tools\demo\render.ps1 discover-datasets
.\tools\demo\render.ps1 preset-features
.\tools\demo\render.ps1 --all
```

`--all` is the easiest path when you've added or edited a tape and
want every GIF refreshed. Pre-warm and (Docker-mode) image build run
once for the whole batch; only the actual vhs render repeats per
tape (~30 s each on a warm cache).

### Render modes

Two ways to run vhs:

| Mode | Means | When |
| --- | --- | --- |
| `--local` | Run the host's `vhs` binary directly. Requires `vhs`, `ttyd`, `ffmpeg`, `column` on PATH. | Inside the dev container (post-create installs all four), or on a Linux / macOS dev machine that has them. Fastest. |
| `--docker` | Build a custom VHS Docker image and render through it. | Windows / macOS hosts where vhs isn't installed natively. Standalone — no project deps on the host. |

Default is **auto**: if `vhs` is on PATH, use it; else fall back to
Docker. Pass an explicit flag to override (e.g. `--docker` on a
machine with vhs available but where you want to test the
Dockerfile path).

### What the script does

1. Resolves render mode (above).
2. Pre-warms the host's ABS cache (`census-augment fetch` for
   boundaries + GCP, then a `census-augment run` against every
   `*.yaml` in `tools/demo/` so any registered-dataset caches the
   tapes touch — SEIFA, etc. — are populated before VHS starts
   recording).
3. (Docker mode only) Builds the `census-augment-vhs` image; cached
   layers reused if source unchanged.
4. Runs vhs against the chosen tape (or every tape, with `--all`).
5. Drops the rendered GIF at `docs/<slug>.gif`.

### Why both modes?

`--local` is faster and avoids any container layering. It's the
right default inside the dev container, where `post-create.sh`
installs vhs + its deps already.

`--docker` is the cross-platform fallback. Native VHS on Windows
is fragile — it relies on `bash.exe` and `ttyd.exe` being
discoverable, which often hangs in initialisation. The Docker path
bypasses every Windows-toolchain issue: the vhs image is Linux,
all unix tooling Just Works, and the same `Dockerfile` reproduces
identically on macOS / Linux / Windows / WSL / devcontainer. One
workflow for whoever doesn't want vhs on their host.

### Timing

| Mode + run | Wallclock | Why |
| --- | --- | --- |
| `--local`, any | ~30 s per tape | Just vhs running natively |
| `--docker`, first ever | ~3–5 min | Pull base vhs image (~150 MB) + apt-install Python + pip-install census-augment deps |
| `--docker`, subsequent (no source change) | ~30 s per tape | All Docker layers cached |
| `--docker`, after source change | ~1–2 min | `COPY` layers re-run; pip install repeats |

The visible portion of each GIF is ~20–30 s regardless.

### Prerequisites

For `--local`: `vhs`, `ttyd`, `ffmpeg`, `column` (from `bsdmainutils`)
on PATH. The dev container's `post-create.sh` installs all four; on
a host install them however your distro packages them (apt /
homebrew / GitHub release).

For `--docker`: Docker Desktop running (Windows / macOS) or
`dockerd` reachable (Linux).

Both modes require **`uv`** on PATH (used by the host-side pre-warm).

### Commit the result

```bash
git add docs/*.gif docs/frames/*.png
git commit -m "Refresh demo GIFs + frames"
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

The headline demo and the preset-features demo both use lat/lon
inputs to avoid exercising any geocoder, so they're reproducible
without a populated G-NAF cache and without making live Nominatim
calls (which are rate-limited at 1 req/sec). All five rows take the
spatial-join path — fast, deterministic, and offline once the
boundary file is cached.

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
vhs tools/demo/preset-features.tape

# Linux
go install github.com/charmbracelet/vhs@latest
sudo apt-get install -y ttyd bsdmainutils
vhs tools/demo/demo.tape
```
