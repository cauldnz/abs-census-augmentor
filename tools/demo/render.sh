#!/usr/bin/env bash
# Renders one (or every) README demo GIF. macOS / Linux / WSL /
# devcontainer equivalent of render.ps1.
#
# Two rendering modes:
#
#   --local : run vhs natively (requires `vhs`, `ttyd`, `ffmpeg`,
#             `column` on PATH). Fast; no Docker dependency. The
#             dev container's post-create installs all four so this
#             mode is the default inside the dev container.
#
#   --docker: build a custom VHS Docker image and run it with the
#             repo and ABS cache mounted. Works on any host with
#             Docker reachable (typical Windows / macOS path).
#
# The default is auto: prefer --local if `vhs` is on PATH, else
# fall back to --docker. Pass either flag to force one mode.
#
# Usage from the repo root:
#
#     ./tools/demo/render.sh                       # demo.tape       -> docs/demo.gif (auto mode)
#     ./tools/demo/render.sh discover-datasets     # discover-datasets.tape -> docs/discover-datasets.gif
#     ./tools/demo/render.sh preset-features       # preset-features.tape   -> docs/preset-features.gif
#     ./tools/demo/render.sh --all                 # every *.tape in tools/demo/
#     ./tools/demo/render.sh --local --all         # render every tape via local vhs
#     ./tools/demo/render.sh --docker preset-features
#
# Slug args pick the tape under tools/demo/ (no path, no extension).
# Output filename mirrors the tape name. With --all, every tape is
# rendered in lexical order; pre-warm and image build (in --docker
# mode) happen once for the batch.

set -euo pipefail

# ---- arg parsing -------------------------------------------------------
# Recognise --local / --docker / --all anywhere in the args; everything
# else is treated as a tape slug. The last slug wins; if none, default
# to "demo". `--all` overrides any slug.

mode="auto"
slug=""
all=0

for arg in "$@"; do
    case "$arg" in
        --local)  mode="local" ;;
        --docker) mode="docker" ;;
        --all)    all=1 ;;
        --*)
            echo "Unknown flag: $arg" >&2
            echo "Valid flags: --local, --docker, --all" >&2
            exit 2
            ;;
        *)        slug="$arg" ;;
    esac
done
slug="${slug:-demo}"

# ---- preflight (shared by all modes) -----------------------------------

if [[ ! -f tools/demo/demo.tape ]]; then
    echo "Run this script from the repo root (so 'tools/demo/' is visible)." >&2
    exit 1
fi

# ---- mode resolution ---------------------------------------------------

resolve_mode() {
    if [[ "$mode" == "local" ]]; then
        # Forced local: require all tools or fail loudly.
        local missing=()
        command -v vhs    >/dev/null 2>&1 || missing+=(vhs)
        command -v ttyd   >/dev/null 2>&1 || missing+=(ttyd)
        command -v ffmpeg >/dev/null 2>&1 || missing+=(ffmpeg)
        command -v column >/dev/null 2>&1 || missing+=(column)
        if (( ${#missing[@]} > 0 )); then
            echo "Forced --local but these tools are missing: ${missing[*]}" >&2
            echo "Install them or drop --local to fall back to Docker." >&2
            exit 1
        fi
        echo "local"
        return
    fi

    if [[ "$mode" == "docker" ]]; then
        if ! docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
            echo "Forced --docker but Docker isn't reachable. Start Docker and retry." >&2
            exit 1
        fi
        echo "docker"
        return
    fi

    # auto: prefer local if all tools present
    if command -v vhs >/dev/null 2>&1 \
       && command -v ttyd >/dev/null 2>&1 \
       && command -v ffmpeg >/dev/null 2>&1 \
       && command -v column >/dev/null 2>&1; then
        echo "local"
        return
    fi

    if docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
        echo "docker"
        return
    fi

    echo "Neither local vhs nor Docker is available." >&2
    echo "Install vhs (https://github.com/charmbracelet/vhs) and its deps," >&2
    echo "or start Docker, or open this repo in the .devcontainer/ where" >&2
    echo "both are pre-provisioned." >&2
    exit 1
}

resolved_mode="$(resolve_mode)"
echo "Render mode: $resolved_mode"

# ---- host cache pre-warm -----------------------------------------------
# `platformdirs` resolves the cache root the same way census-augment
# itself does at runtime. In --docker mode we bind-mount this into
# the container so expensive downloads only happen once.
case "$(uname -s)" in
    Darwin) host_cache="$HOME/Library/Caches/census-augment" ;;
    *)      host_cache="${XDG_CACHE_HOME:-$HOME/.cache}/census-augment" ;;
esac

# Pick any demo config to drive `census-augment fetch` for the
# boundaries + GCP DataPack - these are the same regardless of which
# config a tape references.
fetch_config="tools/demo/config.yaml"
if [[ ! -f "$fetch_config" ]]; then
    fetch_config="$(find tools/demo -maxdepth 1 -name '*.yaml' | head -n 1)"
fi

if [[ ! -d "$host_cache/data/boundaries" && -n "$fetch_config" ]]; then
    echo "ABS cache not yet populated - running 'census-augment fetch' on the host first..."
    uv run census-augment fetch --config "$fetch_config" --boundaries --census
fi

# Pre-run every demo config on the host so registered-dataset caches
# (SEIFA, ERP, etc.) are populated before VHS records any tape. We
# loop across every *.yaml in tools/demo/ rather than hardcoding one
# config so adding a new tape with its own config doesn't require a
# script edit. Errors are swallowed - the tape's own run inside the
# render will surface any real problem.
echo "Pre-warming registered-dataset caches via host-side runs..."
for cfg in tools/demo/*.yaml; do
    [[ -f "$cfg" ]] || continue
    echo "  -> $cfg"
    uv run census-augment run --config "$cfg" 2>/dev/null >/dev/null || true
done

# ---- docker setup (only in docker mode) -------------------------------

if [[ "$resolved_mode" == "docker" ]]; then
    echo "Building census-augment-vhs image (cached layers reused if source unchanged)..."
    docker build -f tools/demo/Dockerfile -t census-augment-vhs . >/dev/null
fi

mkdir -p docs docs/frames

# Per-tape log files. Each tape's vhs output goes to its own log
# rather than a shared one — necessary for parallel rendering
# (interleaved tee output across tapes would be unreadable). After
# every render an aggregate `.last-render.log` is rebuilt by
# concatenating the per-tape logs in order, so diagnostic UX
# stays the same: `cat tools/demo/.last-render.log` shows
# everything.
log_for_slug() { echo "tools/demo/.last-render-${1}.log"; }
agg_log_path="tools/demo/.last-render.log"

# ---- per-tape render ---------------------------------------------------
#
# render_one() runs entirely silently except for vhs's own output,
# which is appended to that tape's per-tape log file. Stdout/stderr
# of vhs are *not* tee'd to the terminal here — when several
# render_one calls run in parallel under --all, interleaved chunks
# would be unreadable. The caller (sequential or parallel) prints
# its own start/done lines around each render so the user has a
# coherent progress trail.

render_one() {
    local s="$1"
    local tape_path="tools/demo/${s}.tape"
    local output_path="docs/${s}.gif"
    local slug_log
    slug_log="$(log_for_slug "$s")"

    if [[ ! -f "$tape_path" ]]; then
        echo "Tape file not found: $tape_path" >&2
        echo "Available tapes:" >&2
        find tools/demo -maxdepth 1 -name '*.tape' -printf '  %f\n' | sort >&2 || true
        return 1
    fi

    {
        echo "=== ${tape_path} -> ${output_path} @ $(date -Iseconds) ==="
    } > "$slug_log"

    if [[ "$resolved_mode" == "local" ]]; then
        # `uv run` is critical here, not cosmetic. `vhs` spawns its
        # own bash subshell to record the tape; that subshell
        # inherits this process's PATH. Without uv, `.venv/bin/`
        # isn't on PATH, and any tape line invoking `census-augment`
        # fails with `bash: census-augment: command not found` —
        # silent in the GIF (just shows the error) but the user
        # discovers it post-render. `uv run vhs` prepends
        # `.venv/bin/` to PATH for the entire process tree.
        uv run vhs "$tape_path" >>"$slug_log" 2>&1
    else
        docker run --rm \
            -v "$PWD:/vhs" \
            -v "$host_cache:/root/.cache/census-augment" \
            census-augment-vhs \
            "$tape_path" >>"$slug_log" 2>&1
    fi
}

# ---- dispatch ----------------------------------------------------------

if (( all )); then
    # Parallel render: each tape is fully independent of the others
    # (own tape file, own output GIF, own PNG frames, own log) so
    # we spawn render_one for every tape concurrently and wait
    # for all to finish. For 3 tapes this is ~3x faster than
    # sequential. Memory cost: chromium per render (~200-400 MB
    # each), which the dev container handles fine.
    #
    # If you want sequential renders for debugging, render each
    # slug explicitly: `./tools/demo/render.sh demo` etc.
    echo "Rendering all tapes in parallel..."
    declare -A pid_to_slug=()
    for tape in tools/demo/*.tape; do
        s="$(basename "$tape" .tape)"
        echo "  -> ${s} (background)"
        render_one "$s" &
        pid_to_slug[$!]="$s"
    done

    failed=()
    rendered=()
    for pid in "${!pid_to_slug[@]}"; do
        s="${pid_to_slug[$pid]}"
        if wait "$pid"; then
            rendered+=("docs/${s}.gif")
            echo "     [done] $s"
        else
            failed+=("$s")
            echo "     [FAIL] $s — see $(log_for_slug "$s")" >&2
        fi
    done

    # Rebuild the aggregate log from per-tape logs in slug order
    # so `cat .last-render.log` stays a useful diagnostic.
    : > "$agg_log_path"
    for tape in tools/demo/*.tape; do
        s="$(basename "$tape" .tape)"
        slug_log="$(log_for_slug "$s")"
        if [[ -f "$slug_log" ]]; then
            cat "$slug_log" >> "$agg_log_path"
            echo >> "$agg_log_path"
        fi
    done

    echo
    echo "Rendered ${#rendered[@]} GIFs:"
    for gif in "${rendered[@]}"; do
        echo "  - $gif"
    done
    if (( ${#failed[@]} > 0 )); then
        echo
        echo "${#failed[@]} render(s) failed: ${failed[*]}" >&2
        echo "Per-tape logs: $(log_for_slug '<slug>')" >&2
        exit 1
    fi
    echo "Inspect each and 'git add' the ones you're happy with."
    echo "Combined render log: $agg_log_path"
else
    echo "Rendering tools/demo/${slug}.tape -> docs/${slug}.gif ..."
    if render_one "$slug"; then
        slug_log="$(log_for_slug "$slug")"
        cp -f "$slug_log" "$agg_log_path"
        echo "Done. Inspect docs/${slug}.gif and 'git add' it when you're happy."
        echo "Render log: $agg_log_path"
    else
        echo "Render failed — see $(log_for_slug "$slug")" >&2
        exit 1
    fi
fi
