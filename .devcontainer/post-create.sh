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
#
# Notes on what apt vs. GitHub-release installs:
#
# - ffmpeg + bsdmainutils ARE in Debian bookworm main, so apt.
# - ttyd is NOT in Debian bookworm main. The previous version of
#   this script tried `apt-get install ttyd` and failed with
#   "no installation candidate". ttyd's upstream ships a static
#   binary per arch on each GitHub release (verified against
#   https://github.com/tsl0922/ttyd/releases — assets are
#   `ttyd.x86_64`, `ttyd.aarch64`, etc.); we curl that.
# - vhs is also not in Debian repos; same GitHub-release pattern.
if ! command -v vhs >/dev/null 2>&1; then
    echo "==> Installing VHS for native demo rendering..."

    # apt-installable deps first.
    sudo apt-get update >/dev/null
    # ffmpeg: VHS encodes captured frames into the output GIF.
    # bsdmainutils: provides `column`, used by demo tapes to align
    #               the projected output table.
    sudo apt-get install -y --no-install-recommends \
        ffmpeg bsdmainutils >/dev/null

    # Resolve the architecture once for both ttyd and vhs.
    arch="$(uname -m)"
    case "$arch" in
        x86_64)
            vhs_arch="x86_64"
            ttyd_arch="x86_64"
            ;;
        aarch64)
            vhs_arch="arm64"
            ttyd_arch="aarch64"
            ;;
        *)
            echo "  WARNING: unknown arch $arch; skipping VHS install." \
                 "render.sh will fall back to Docker." >&2
            arch=""
            ;;
    esac

    if [[ -n "$arch" ]]; then
        # ttyd: VHS uses it to host the recorded terminal session.
        # Single static binary per arch from the upstream GitHub
        # release; no archive to extract.
        ttyd_version="1.7.7"
        echo "  Installing ttyd ${ttyd_version} (${ttyd_arch})..."
        tmp_ttyd="$(mktemp)"
        curl -fsSL \
            "https://github.com/tsl0922/ttyd/releases/download/${ttyd_version}/ttyd.${ttyd_arch}" \
            -o "$tmp_ttyd"
        sudo install -m 0755 "$tmp_ttyd" /usr/local/bin/ttyd
        rm -f "$tmp_ttyd"

        # vhs: the renderer itself. Tarball with vhs binary inside.
        vhs_version="0.11.0"
        echo "  Installing vhs ${vhs_version} (${vhs_arch})..."
        tmp_vhs="$(mktemp -d)"
        curl -fsSL \
            "https://github.com/charmbracelet/vhs/releases/download/v${vhs_version}/vhs_${vhs_version}_Linux_${vhs_arch}.tar.gz" \
            | tar xz -C "$tmp_vhs"
        sudo install -m 0755 "$tmp_vhs"/vhs_*/vhs /usr/local/bin/vhs
        rm -rf "$tmp_vhs"

        # Smoke-test both binaries actually run.
        ttyd --version >/dev/null
        vhs --version >/dev/null
        echo "  $(vhs --version) + ttyd $(ttyd --version 2>&1 | head -n 1)"
    fi
fi

echo
echo "==> Devcontainer ready."
echo "   Run 'uv run pytest' to verify the full suite."
echo "   Run 'tools/demo/render.sh' to render a demo GIF (uses local vhs"
echo "        if available, else falls back to host Docker)."
