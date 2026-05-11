# discover-datasets demo: introduce the v1.3 pluggable framework.
#
# Render with:
#     ./tools/demo/render.sh discover-datasets       # macOS / Linux / WSL / devcontainer
#     .\tools\demo\render.ps1 discover-datasets      # Windows PowerShell
#
# Output: docs/discover-datasets.gif (~25 s, embedded in README's
# "what's available" section).

Output docs/discover-datasets.gif

# Per-scene PNG snapshots — see comment in demo.tape.

Set Shell "bash"
Set Theme "Dracula"
Set FontSize 16
Set Width 1100
Set Height 720
Set TypingSpeed 50ms
Set Padding 30

# See demo.tape for why this is necessary — chromium PTY makes
# Python's default buffering trap fire.
Env PYTHONUNBUFFERED "1"

# ---- scene 1: list registered datasets ---------------------------------
# Sleeps that invoke `census-augment` (here and in scenes 2 + 4)
# bumped to 12s. Even the lightweight `discover` commands pay a
# full Python+typer+pandas+geopandas startup cost (~4-6 s under
# the chromium PTY) before producing output. The previous 5-6s
# windows captured only the typed command line. Pure-bash scenes
# (scene 3's `head -25`) keep their short Sleeps since they
# return in microseconds.
Type "# What ABS datasets does the augmentor know about?"
Enter
Sleep 800ms
Type "census-augment discover --config tools/demo/config.yaml --datasets"
Enter
Sleep 12s
Screenshot docs/frames/discover-datasets-1-list.png
Type "clear"
Enter
Sleep 300ms

# ---- scene 2: zoom into one dataset ------------------------------------
Type "# Each dataset has a markdown spec — let's look at SEIFA's schema:"
Enter
Sleep 800ms
Type "census-augment discover --config tools/demo/config.yaml --dataset seifa_2021"
Enter
Sleep 12s
Screenshot docs/frames/discover-datasets-2-schema.png
Type "clear"
Enter
Sleep 300ms

# ---- scene 3: show the spec file format --------------------------------
Type "# That schema came from this single markdown file:"
Enter
Sleep 800ms
Type "head -25 datasets/seifa_2021.md"
Enter
Sleep 6s
Screenshot docs/frames/discover-datasets-3-spec.png
Type "clear"
Enter
Sleep 300ms

# ---- scene 4: list PRESETs ---------------------------------------------
Type "# And six curated PRESET ratios with the right denominators baked in:"
Enter
Sleep 800ms
Type "census-augment discover --config tools/demo/config.yaml --features"
Enter
Sleep 12s
Screenshot docs/frames/discover-datasets-4-presets.png
