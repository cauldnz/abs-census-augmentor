# Demo frame snapshots

Per-scene PNG screenshots captured during VHS demo rendering. One
PNG per scene of each tape in `tools/demo/`, written via the
`Screenshot` directive at the moment each scene's content is
fully laid out (just before the next `clear`).

## What's here

| File | Source tape | Scene |
| --- | --- | --- |
| `demo-1-input.png`        | `demo.tape` | `cat input.csv` (the 5 locations) |
| `demo-2-config.png`       | `demo.tape` | `cat config.yaml` (variable mix) |
| `demo-3-run.png`          | `demo.tape` | `census-augment run` output |
| `demo-4-output.png`       | `demo.tape` | Projected output table |
| `discover-datasets-1-list.png`    | `discover-datasets.tape` | `--datasets` output |
| `discover-datasets-2-schema.png`  | `discover-datasets.tape` | `--dataset seifa_2021` output |
| `discover-datasets-3-spec.png`    | `discover-datasets.tape` | `head -25 datasets/seifa_2021.md` |
| `discover-datasets-4-presets.png` | `discover-datasets.tape` | `--features` output |
| `preset-features-1-spec.png`      | `preset-features.tape` | `head -22 features/pct_renters.md` |
| `preset-features-2-config.png`    | `preset-features.tape` | `cat preset-config.yaml` |
| `preset-features-3-run.png`       | `preset-features.tape` | `census-augment run` output |
| `preset-features-4-output.png`    | `preset-features.tape` | Projected output table |

## When these get regenerated

Whenever a tape is re-rendered. `tools/demo/render.sh` (or `.ps1`)
runs `vhs <tape>`, which produces both the `.gif` (in `docs/`) and
the per-scene `.png`s (here) in one pass.

Re-render all of them after any tape edit:

```bash
make demos          # or `./tools/demo/render.sh --all`
git add docs/*.gif docs/frames/*.png
git commit -m "Refresh demo GIFs + frames"
```

## Why both GIFs and PNGs?

- **GIFs** are the README's animated explainer — they show motion
  through a sequence of scenes. Good for "what does this tool feel
  like to use?".
- **PNGs** are static and universally embeddable. Useful for:
  - Blog posts / Slack previews / Discord embeds where GIF
    animation doesn't always autoplay.
  - Sharing a specific frame ("look at the SEIFA schema output")
    without forcing the reader to wait for the GIF to loop.
  - Letting an LLM agent read what's in the demo (I literally
    can't watch a GIF — single frames I can see).
