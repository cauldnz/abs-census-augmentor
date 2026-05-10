# Dev Container

VSCode Dev Container that gives you a Linux Python 3.11 environment with the
project's dev tooling (uv, ruff, mypy, pytest), GitHub CLI, and host-Docker
access for VHS demo rendering. Takes ~3-5 minutes to build the first time;
subsequent attaches are seconds.

## When to use it

- You're on Windows and want the project to behave like CI does (it's the
  same Linux environment).
- You're running on macOS / Linux but want a clean, reproducible env that
  doesn't touch your system Python.
- You want to render the demo GIFs and don't want to install VHS / Docker /
  Python locally.

## What's inside

| | |
|---|---|
| Base image | `mcr.microsoft.com/devcontainers/python:1-3.11-bookworm` (via local `Dockerfile` — see workaround note below) |
| Package manager | `uv` (installed by `post-create.sh`) |
| Dev deps | `pytest`, `pytest-mock`, `responses`, `moto`, `ruff`, `mypy`, `types-*` (synced via `uv sync --all-extras`) |
| Extras | GitHub CLI, build-essentials, zsh + oh-my-zsh |
| Docker | Host's Docker socket mounted (no Docker-in-Docker — saves disk + boot time) |

VSCode extensions auto-installed: Python + Pylance + Ruff + Mypy +
Even-Better-TOML + GitHub PR + GitLens.

## First-time setup

1. **Install prerequisites on the host:**
   - VSCode + the [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers).
   - Docker Desktop (Windows / macOS) or Docker Engine (Linux) — running.
   - On Windows: WSL2 enabled, Docker Desktop's WSL integration on for your
     distro.
2. **Open the repo in VSCode**, then `F1` → `Dev Containers: Reopen in
   Container`.
3. **Wait for the build.** The post-create script will install uv and run
   `uv sync --all-extras`. Output appears in the VSCode terminal.
4. **(Optional) Auth GitHub CLI** if you'll be making PRs from inside the
   container:
   ```bash
   gh auth login --web
   ```
   This persists across rebuilds.

Once the container is up, everything works the way CI runs:

```bash
uv run pytest                  # full test suite
uv run ruff check .            # lint
uv run mypy src/ tools/        # type-check
uv run census-augment --help   # CLI is on PATH via the venv
```

## Rendering demos

The existing `tools/demo/render.sh` works inside the devcontainer because the
host Docker socket is mounted:

```bash
./tools/demo/render.sh                       # docs/demo.gif (headline)
./tools/demo/render.sh discover-datasets     # docs/discover-datasets.gif
./tools/demo/render.sh preset-features       # docs/preset-features.gif
```

The script pulls the VHS image (`ghcr.io/charmbracelet/vhs:latest`) and
mounts the repo into it. Renders take ~30 s of wall-clock per demo.

## Troubleshooting

- **"docker: command not found"**: the docker-outside-of-docker feature
  failed to install. Rebuild: `F1` → `Dev Containers: Rebuild Container`.
- **`gh` says "not authenticated"**: run `gh auth login --web` once. Token
  persists in the container until you `Rebuild Container`; for a longer-
  lived setup, mount your host's `~/.config/gh` into the container.
- **`uv sync` fails with "permission denied" on `.venv/`**: an old `.venv/`
  from a host run is still mounted. Delete it on the host and rebuild.
- **Demo render hangs**: Docker Desktop probably isn't running on the host.
  Start it and rerun.
- **Build fails with `NO_PUBKEY 62D54FD4003F6525`**: this is the upstream
  yarn apt-source GPG key rotation. The `Dockerfile` in this directory
  removes the broken yarn source as the first step of the build, so the
  failure should not be reachable. If it ever resurfaces, check the
  Dockerfile is still being picked up (i.e. devcontainer.json's
  `build.dockerfile` field still points at `Dockerfile`).

## Why the local Dockerfile?

`devcontainer.json` builds from `Dockerfile` (in this directory) rather
than referencing the Microsoft Python image directly. The Dockerfile is
two lines: it bases on `mcr.microsoft.com/devcontainers/python:1-3.11-
bookworm` and removes `/etc/apt/sources.list.d/yarn.list` before any
`apt-get update` runs. Yarn's apt repo signing key was rotated upstream
and the cached key in Microsoft's image no longer validates — feature
installs (which run `apt-get update`) fail without this workaround. See
the comment at the top of `Dockerfile` for the full rationale.
