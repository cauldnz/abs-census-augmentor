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
# Three of this tape's four scenes invoke `census-augment` — all
# pay the chromium-PTY Python startup tax (~5-10 s before output
# even though the actual work is microseconds). Same Hide / Show
# jump-cut pattern as demo.tape's run scene: type the command,
# brief pause, then hide while Python boots, show against the
# populated terminal, brief pause, screenshot. See demo.tape
# scene 3 for the full rationale.
#
# Scene 3 (`head -25 datasets/seifa_2021.md`) is pure bash and
# returns in microseconds — keeps its existing Sleep 6 s with
# no Hide / Show.
Type "# What ABS datasets does the augmentor know about?"
Enter
Sleep 800ms
Type "census-augment discover --config tools/demo/config.yaml --datasets"
Enter
Sleep 1s
Hide
Sleep 12s
Show
Sleep 3s
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
Sleep 1s
Hide
Sleep 12s
Show
Sleep 3s
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
Sleep 1s
Hide
Sleep 12s
Show
Sleep 3s
Screenshot docs/frames/discover-datasets-4-presets.png
