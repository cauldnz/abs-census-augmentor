# preset-features demo: PRESET ratios as first-class config variables (v1.4).
#
# Render with:
#     ./tools/demo/render.sh preset-features        # macOS / Linux / WSL / devcontainer
#     .\tools\demo\render.ps1 preset-features       # Windows PowerShell
#
# Output: docs/preset-features.gif (~25 s, embedded in README's
# "PRESETs" section).
#
# What this demo conveys: a PRESET like `pct_renters` encapsulates
# both the formula AND the right denominator. Users write
# `PRESET.pct_renters` in their config and the pipeline auto-loads
# the underlying GCP source columns transparently.

Output docs/preset-features.gif

# Per-scene PNG snapshots — see comment in demo.tape.

Set Shell "bash"
Set Theme "Dracula"
Set FontSize 16
Set Width 1100
Set Height 720
Set TypingSpeed 50ms
Set Padding 30

# ---- off-camera: warm the cache ---------------------------------------
# render.sh / render.ps1 already pre-warm via a host-side
# census-augment run, so this Hide block is just a safety net for
# anyone running vhs against the tape directly.
Hide
Type "census-augment fetch --config tools/demo/preset-config.yaml --boundaries --census >/dev/null 2>&1"
Enter
Sleep 30s
Type "census-augment run --config tools/demo/preset-config.yaml >/dev/null 2>&1"
Enter
Sleep 8s
Type "clear"
Enter
Sleep 500ms
Show

# ---- scene 1: show a PRESET spec ---------------------------------------
Type "# PRESET features encode the ratio AND the right denominator:"
Enter
Sleep 800ms
Type "head -22 features/pct_renters.md"
Enter
Sleep 7s
Screenshot docs/frames/preset-features-1-spec.png
Type "clear"
Enter
Sleep 300ms

# ---- scene 2: a config that uses three PRESETs -------------------------
Type "# Use them like any other variable namespace:"
Enter
Sleep 800ms
Type "cat tools/demo/preset-config.yaml"
Enter
Sleep 5500ms
Screenshot docs/frames/preset-features-2-config.png
Type "clear"
Enter
Sleep 300ms

# ---- scene 3: run ------------------------------------------------------
Type "census-augment run --config tools/demo/preset-config.yaml"
Enter
Sleep 7s
Screenshot docs/frames/preset-features-3-run.png

# ---- scene 4: show the computed ratios ---------------------------------
Type "# Each row carries the computed ratios; source columns hidden:"
Enter
Sleep 800ms
Type "cut -d, -f1,9,11,12,13 tools/demo/preset-output.csv | column -s, -t"
Enter
Sleep 7s
Screenshot docs/frames/preset-features-4-output.png
