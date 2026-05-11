# census-augment headline README demo (v1.4 era).
#
# Render with (from the repo root, host or devcontainer):
#     ./tools/demo/render.sh                 # macOS / Linux / WSL / devcontainer
#     .\tools\demo\render.ps1                # Windows PowerShell
#
# Output: docs/demo.gif (committed to the repo and embedded in README.md).
#
# The render script pre-warms the host's ABS cache (boundaries + GCP
# + SEIFA) so this tape itself never sees a network call.
#
# Note: PRESETs are intentionally absent from the headline demo
# config until #23 lands. Once that's fixed this tape should be
# extended with a `PRESET.pct_renters` row to show the third
# namespace.

Output docs/demo.gif

# Per-scene PNG snapshots get captured via `Screenshot` directives
# below (one per scene, just before the screen clears). The PNGs
# land in docs/frames/ alongside the animated GIF and are useful
# for static embeds where GIF animation isn't reliable.

Set Shell "bash"
Set Theme "Dracula"
Set FontSize 16
Set Width 1100
Set Height 720
Set TypingSpeed 50ms
Set Padding 30

# Force Python's stdout/stderr to flush per-line. Without this,
# `census-augment` invocations (typer + pandas + geopandas imports)
# can produce no visible output until the process exits, because
# Python detects vhs's chromium PTY as non-TTY and goes fully
# buffered. The result: a screenshot fired mid-run captures only
# the typed command line, no output. Setting PYTHONUNBUFFERED
# forces eager flushing regardless of TTY detection.
Env PYTHONUNBUFFERED "1"

# ---- off-camera: warm the cache ----------------------------------------
# The render scripts already pre-warm SEIFA via a host-side
# census-augment run, so this Hide block is just belt-and-braces in
# case someone runs vhs against the tape directly.
Hide
Type "census-augment fetch --config tools/demo/config.yaml --boundaries --census >/dev/null 2>&1"
Enter
Sleep 30s
Type "clear"
Enter
Sleep 500ms
Show

# ---- scene 1: the input -------------------------------------------------
Type "# Five famous Australian locations:"
Enter
Sleep 700ms
Type "cat tools/demo/input.csv"
Enter
Sleep 3500ms
Screenshot docs/frames/demo-1-input.png
Type "clear"
Enter
Sleep 300ms

# ---- scene 2: the config ------------------------------------------------
Type "# Mix Census GCP and SEIFA variables in one config:"
Enter
Sleep 800ms
Type "cat tools/demo/config.yaml"
Enter
Sleep 6500ms
Screenshot docs/frames/demo-2-config.png
Type "clear"
Enter
Sleep 300ms

# ---- scene 3: run -------------------------------------------------------
# Sleep 40s because `census-augment run` measured ~35 s wallclock
# in the dev container on warm cache (16% CPU, ~29 s of I/O wait
# on top of ~6 s of actual compute). See issue #43 for the perf
# investigation — for now we just sleep through it so the demo
# captures real output. If issue #43 lands a faster runtime, we
# can dial this down again.
Type "census-augment run --config tools/demo/config.yaml"
Enter
Sleep 40s
Screenshot docs/frames/demo-3-run.png

# ---- scene 4: the enriched output ---------------------------------------
# 10s after the cut command. `cut | column` on a 5-row CSV is
# sub-second; this is mostly visual breathing room.
Type "# Each row carries Census + SEIFA columns for its SA2:"
Enter
Sleep 800ms
Type "cut -d, -f1,9,11,12,13,14 tools/demo/output.csv | column -s, -t"
Enter
Sleep 10s
Screenshot docs/frames/demo-4-output.png
