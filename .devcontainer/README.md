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

Once the container is up, everything works the way CI runs. There's a `Makefile` at the repo root that wraps the common workflows:

```bash
make              # list all targets
make smoke        # quick wire-up check
make check        # lint + typecheck + test (CI-equivalent)
make demos        # render every README demo GIF
```

Or use `uv run` directly:

```bash
uv run pytest                  # full test suite
uv run ruff check .            # lint
uv run mypy src/ tools/        # type-check
uv run census-augment --help   # CLI is on PATH via the venv
```

## Rendering demos

`post-create.sh` installs `vhs`, `ttyd`, `ffmpeg`, and
`bsdmainutils` so demo rendering runs natively inside the
devcontainer — no docker-in-docker needed. The render script
auto-detects this and uses local vhs by default:

```bash
./tools/demo/render.sh                       # docs/demo.gif (headline)
./tools/demo/render.sh discover-datasets     # docs/discover-datasets.gif
./tools/demo/render.sh preset-features       # docs/preset-features.gif
./tools/demo/render.sh --all                 # render every tape in one go
```

Renders take ~30 s wall-clock per demo. The host Docker socket is
also mounted (via the `docker-outside-of-docker` feature) so you can
force the Docker rendering path if you need to test the Dockerfile:

```bash
./tools/demo/render.sh --docker --all
```

See [`tools/demo/README.md`](../tools/demo/README.md) for the full
mode matrix and timing details.

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
- **`render.sh --local` fails with `No usable sandbox!` or
  `Failed to move to new namespace`**: Chromium's sandbox needs to
  create a user namespace, which Docker's default seccomp profile
  blocks. The `runArgs` in `devcontainer.json` lift that restriction
  with `--security-opt seccomp=unconfined --ipc=host`. If the error
  comes back, check those flags are still present — rebuilding the
  container without them produces this exact failure.

## Why the runArgs?

`devcontainer.json` passes `--security-opt seccomp=unconfined` and
`--ipc=host` to Docker via `runArgs`. The first lets chromium's
sandbox create user namespaces (otherwise blocked by Docker's
default seccomp profile, which produces the
"No usable sandbox / Operation not permitted" error). The second is
chromium's recommended IPC mode under Docker per Playwright's
docker docs — without it, chromium hits shared-memory issues
encoding the demo GIFs.

Same posture every Playwright / Puppeteer Docker workflow uses.
The dev container runs trusted user-attached code, so the broader
security surface is fine here; it would **not** be appropriate for
multi-tenant CI running untrusted browser content.

## Why the local Dockerfile?

`devcontainer.json` builds from `Dockerfile` (in this directory) rather
than referencing the Microsoft Python image directly. The Dockerfile is
two lines: it bases on `mcr.microsoft.com/devcontainers/python:1-3.11-
bookworm` and removes `/etc/apt/sources.list.d/yarn.list` before any
`apt-get update` runs. Yarn's apt repo signing key was rotated upstream
and the cached key in Microsoft's image no longer validates — feature
installs (which run `apt-get update`) fail without this workaround. See
the comment at the top of `Dockerfile` for the full rationale.
