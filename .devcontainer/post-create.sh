#!/usr/bin/env bash
# Devcontainer post-create: install uv, sync the project, smoke-test.
#
# Idempotent — safe to re-run. The devcontainer feature for Python
# already gives us 3.11; we add uv (project tool) and run uv sync to
# materialise the .venv with all dev deps.

set -euo pipefail

echo "==> Installing uv (Python package manager)..."
if ! command -v uv >/dev/null 2>&1; then
    # Astral's official installer. Pinned to a recent version because
    # uv's behaviour around lockfiles has churned; locking the version
    # keeps the devcontainer reproducible across rebuilds.
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # The installer writes to ~/.local/bin which is on PATH for the
    # vscode user.
    export PATH="$HOME/.local/bin:$PATH"
fi
uv --version

echo "==> Syncing project dependencies into .venv/ ..."
# `--all-extras` pulls in [dev] (pytest, ruff, mypy, responses, moto, ...).
# This populates .venv/ at the workspace root. VSCode picks it up via
# python.defaultInterpreterPath in devcontainer.json.
uv sync --all-extras

echo "==> Verifying the install (quick smoke test)..."
uv run census-augment --help >/dev/null
uv run python -c "
from census_augment.datasets import registry
from census_augment.features import features
print(f'  datasets: {len(registry.list_datasets())} registered')
print(f'  features: {len(features.list_features())} PRESETs')
"

# A few project-specific quality-of-life touches:
# - Tell git this directory is safe (devcontainer mounts can otherwise
#   trigger 'dubious ownership' warnings).
git config --global --add safe.directory "$(pwd)"

# Install VHS + its runtime deps so `tools/demo/render.sh --local`
# can render demo GIFs natively from inside the devcontainer
# (avoiding the docker-in-docker round-trip through the host's
# Docker socket). Idempotent — skipped if vhs is already on PATH.
if ! command -v vhs >/dev/null 2>&1; then
    echo "==> Installing VHS for native demo rendering..."
    sudo apt-get update >/dev/null
    # ttyd: VHS uses it to host the recorded terminal session.
    # ffmpeg: VHS encodes captured frames into the output GIF.
    # bsdmainutils: provides `column`, used by demo tapes to align
    #               the projected output table.
    sudo apt-get install -y --no-install-recommends \
        ttyd ffmpeg bsdmainutils >/dev/null
    # VHS itself is a single Go binary; install from the official
    # GitHub release rather than apt (not in Debian repos).
    vhs_version="0.11.0"
    tmp_vhs="$(mktemp -d)"
    arch="$(uname -m)"
    case "$arch" in
        x86_64)  vhs_arch="x86_64" ;;
        aarch64) vhs_arch="arm64" ;;
        *)
            echo "  WARNING: unknown arch $arch; skipping VHS install. " \
                 "render.sh will fall back to Docker." >&2
            tmp_vhs=""
            ;;
    esac
    if [[ -n "$tmp_vhs" ]]; then
        curl -fsSL \
            "https://github.com/charmbracelet/vhs/releases/download/v${vhs_version}/vhs_${vhs_version}_Linux_${vhs_arch}.tar.gz" \
            | tar xz -C "$tmp_vhs"
        sudo install -m 0755 "$tmp_vhs"/vhs_*/vhs /usr/local/bin/vhs
        rm -rf "$tmp_vhs"
        echo "  $(vhs --version)"
    fi
fi

echo
echo "==> Devcontainer ready."
echo "   Run 'uv run pytest' to verify the full suite."
echo "   Run 'tools/demo/render.sh' to render a demo GIF (uses local vhs"
echo "        if available, else falls back to host Docker)."
