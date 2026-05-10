#!/usr/bin/env bash
# Renders docs/demo.gif using a Docker-isolated VHS. macOS / Linux equivalent
# of render.ps1; same behaviour. Run from the repo root:
#
#     ./tools/demo/render.sh

set -euo pipefail

if [[ ! -f tools/demo/demo.tape ]]; then
    echo "Run this script from the repo root (so 'tools/demo/demo.tape' is visible)." >&2
    exit 1
fi

if ! docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
    echo "Docker isn't reachable. Start Docker (Docker Desktop / dockerd) and retry." >&2
    exit 1
fi

# platformdirs cache dir on macOS / Linux:
case "$(uname -s)" in
    Darwin) host_cache="$HOME/Library/Caches/census-augment" ;;
    *)      host_cache="${XDG_CACHE_HOME:-$HOME/.cache}/census-augment" ;;
esac

if [[ ! -d "$host_cache/data/boundaries" ]]; then
    echo "ABS cache not yet populated — running 'census-augment fetch' on the host first..."
    uv run census-augment fetch --config tools/demo/config.yaml --boundaries --census
fi

echo "Building census-augment-vhs image (cached layers reused if source unchanged)..."
docker build -f tools/demo/Dockerfile -t census-augment-vhs .

mkdir -p docs

echo "Rendering tools/demo/demo.tape -> docs/demo.gif ..."
docker run --rm \
    -v "$PWD:/vhs" \
    -v "$host_cache:/root/.cache/census-augment" \
    census-augment-vhs \
    tools/demo/demo.tape

echo "Done. Inspect docs/demo.gif and 'git add' it when you're happy."
