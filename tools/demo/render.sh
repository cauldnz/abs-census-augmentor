#!/usr/bin/env bash
# Renders one of the README demo GIFs using a Docker-isolated VHS.
# macOS / Linux / WSL / devcontainer equivalent of render.ps1.
#
# Usage from the repo root:
#
#     ./tools/demo/render.sh                       # demo.tape   -> docs/demo.gif (headline)
#     ./tools/demo/render.sh discover-datasets     # discover-datasets.tape -> docs/discover-datasets.gif
#     ./tools/demo/render.sh preset-features       # preset-features.tape -> docs/preset-features.gif
#
# The arg picks the tape file under tools/demo/ (no path, no
# extension). Output filename mirrors the tape name.

set -euo pipefail

# ---- arg handling ------------------------------------------------------

slug="${1:-demo}"
tape_path="tools/demo/${slug}.tape"
output_path="docs/${slug}.gif"

if [[ ! -f "$tape_path" ]]; then
    echo "Tape file not found: $tape_path" >&2
    echo "Available tapes:" >&2
    find tools/demo -maxdepth 1 -name '*.tape' -printf '  %f\n' | sort >&2 || true
    exit 1
fi

# ---- preflight ---------------------------------------------------------

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

if [[ ! -d "$host_cache/data/boundaries" ]]; then
    echo "ABS cache not yet populated — running 'census-augment fetch' on the host first..."
    uv run census-augment fetch --config tools/demo/config.yaml --boundaries --census
fi

# Pre-run the demo's config once on the host so any registered-dataset
# caches the tape touches (SEIFA, etc.) are populated before VHS hits
# Record. This avoids capturing 'downloading SEIFA xlsx...' messages
# in the visible part of the GIF.
#
# We discard the output file — the actual demo run inside Docker
# writes the canonical output.csv that the tape `cat`s.
if [[ -f tools/demo/config.yaml ]]; then
    echo "Pre-warming registered-dataset caches via a host-side run..."
    tmp_out="$(mktemp -t census-augment-prewarm.XXXXXX.csv)"
    trap 'rm -f "$tmp_out"' EXIT
    # Suppress logs from the pre-warm pass.
    uv run census-augment run \
        --config tools/demo/config.yaml \
        2>/dev/null >/dev/null || true
fi

# ---- build + render ----------------------------------------------------

echo "Building census-augment-vhs image (cached layers reused if source unchanged)..."
docker build -f tools/demo/Dockerfile -t census-augment-vhs .

mkdir -p docs

echo "Rendering ${tape_path} -> ${output_path} ..."
docker run --rm \
    -v "$PWD:/vhs" \
    -v "$host_cache:/root/.cache/census-augment" \
    census-augment-vhs \
    "$tape_path"

echo "Done. Inspect ${output_path} and 'git add' it when you're happy."
