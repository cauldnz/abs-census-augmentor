#!/usr/bin/env bash
# Renders one (or every) README demo GIF using a Docker-isolated VHS.
# macOS / Linux / WSL / devcontainer equivalent of render.ps1.
#
# Usage from the repo root:
#
#     ./tools/demo/render.sh                       # demo.tape       -> docs/demo.gif (headline)
#     ./tools/demo/render.sh discover-datasets     # discover-datasets.tape -> docs/discover-datasets.gif
#     ./tools/demo/render.sh preset-features       # preset-features.tape   -> docs/preset-features.gif
#     ./tools/demo/render.sh --all                 # every *.tape in tools/demo/
#
# The arg picks the tape file under tools/demo/ (no path, no
# extension). Output filename mirrors the tape name. With `--all`,
# every tape is rendered in lexical order. Pre-warm and image build
# happen once for the batch (Docker-cached on subsequent renders).

set -euo pipefail

# ---- preflight (shared by all modes) -----------------------------------

if [[ ! -f tools/demo/demo.tape ]]; then
    echo "Run this script from the repo root (so 'tools/demo/' is visible)." >&2
    exit 1
fi

if ! docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
    echo "Docker isn't reachable. Start Docker (Docker Desktop / dockerd) and retry." >&2
    exit 1
fi

# ---- host cache pre-warm -----------------------------------------------
# `platformdirs` resolves the cache root the same way census-augment
# itself does at runtime. Bind-mount this into the Docker render so
# expensive downloads (SEIFA, GCP, boundaries) only happen once.
case "$(uname -s)" in
    Darwin) host_cache="$HOME/Library/Caches/census-augment" ;;
    *)      host_cache="${XDG_CACHE_HOME:-$HOME/.cache}/census-augment" ;;
esac

# Pick any demo config to drive `census-augment fetch` for the
# boundaries + GCP DataPack — these are the same regardless of which
# config a tape references.
fetch_config="tools/demo/config.yaml"
if [[ ! -f "$fetch_config" ]]; then
    fetch_config="$(find tools/demo -maxdepth 1 -name '*.yaml' | head -n 1)"
fi

if [[ ! -d "$host_cache/data/boundaries" && -n "$fetch_config" ]]; then
    echo "ABS cache not yet populated — running 'census-augment fetch' on the host first..."
    uv run census-augment fetch --config "$fetch_config" --boundaries --census
fi

# Pre-run every demo config on the host so registered-dataset caches
# (SEIFA, ERP, etc.) are populated before VHS records any tape. We
# loop across every *.yaml in tools/demo/ rather than hardcoding one
# config so adding a new tape with its own config doesn't require a
# script edit. Errors are swallowed — the tape's own run inside
# Docker will surface any real problem.
echo "Pre-warming registered-dataset caches via host-side runs..."
for cfg in tools/demo/*.yaml; do
    [[ -f "$cfg" ]] || continue
    echo "  -> $cfg"
    uv run census-augment run --config "$cfg" 2>/dev/null >/dev/null || true
done

# ---- build the VHS image once ------------------------------------------

echo "Building census-augment-vhs image (cached layers reused if source unchanged)..."
docker build -f tools/demo/Dockerfile -t census-augment-vhs . >/dev/null

mkdir -p docs

# ---- render one tape ---------------------------------------------------

render_one() {
    local slug="$1"
    local tape_path="tools/demo/${slug}.tape"
    local output_path="docs/${slug}.gif"

    if [[ ! -f "$tape_path" ]]; then
        echo "Tape file not found: $tape_path" >&2
        echo "Available tapes:" >&2
        find tools/demo -maxdepth 1 -name '*.tape' -printf '  %f\n' | sort >&2 || true
        return 1
    fi

    echo "Rendering ${tape_path} -> ${output_path} ..."
    docker run --rm \
        -v "$PWD:/vhs" \
        -v "$host_cache:/root/.cache/census-augment" \
        census-augment-vhs \
        "$tape_path"
}

# ---- arg dispatch ------------------------------------------------------

if [[ "${1:-}" == "--all" ]]; then
    rendered=()
    for tape in tools/demo/*.tape; do
        slug="$(basename "$tape" .tape)"
        render_one "$slug"
        rendered+=("docs/${slug}.gif")
    done
    echo
    echo "Rendered ${#rendered[@]} GIFs:"
    for gif in "${rendered[@]}"; do
        echo "  - $gif"
    done
    echo "Inspect each and 'git add' the ones you're happy with."
else
    slug="${1:-demo}"
    render_one "$slug"
    echo "Done. Inspect docs/${slug}.gif and 'git add' it when you're happy."
fi
