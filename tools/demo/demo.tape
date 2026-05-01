# census-augment README demo
#
# Render with (from the repo root):
#     vhs tools/demo/demo.tape
#
# Output: docs/demo.gif (committed to the repo and embedded in README.md).
#
# Re-run this whenever the CLI's surface or the run-summary format
# changes so the GIF stays in sync.

Output docs/demo.gif

Set Shell "bash"
Set Theme "Dracula"
Set FontSize 16
Set Width 1100
Set Height 620
Set TypingSpeed 50ms
Set Padding 30

# ---- off-camera: ensure the ABS cache is populated -----------------------
# `census-augment fetch` is idempotent: instant on a warm cache, ~15 s on
# a cold one. Either way the viewer never sees this — Hide stops frame
# capture, so the GIF length is the sum of the visible (Show'd) durations.
Hide
Type "census-augment fetch --config tools/demo/config.yaml --boundaries --census >/dev/null 2>&1"
Enter
Sleep 60s
Type "clear"
Enter
Sleep 500ms
Show

# ---- scene 1: show the input --------------------------------------------
Type "# 5 famous Australian locations:"
Enter
Sleep 800ms
Type "cat tools/demo/input.csv"
Enter
Sleep 4s
Type "clear"
Enter
Sleep 400ms

# ---- scene 2: run the augmentation --------------------------------------
Type "census-augment run --config tools/demo/config.yaml"
Enter
Sleep 6s

# ---- scene 3: show the enrichment -------------------------------------
Type "# Each location now carries census variables for its SA2:"
Enter
Sleep 800ms
Type "cut -d, -f1,9,11,12,13 tools/demo/output.csv | column -s, -t"
Enter
Sleep 6s
