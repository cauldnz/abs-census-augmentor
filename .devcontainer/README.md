# Dev Container

VSCode Dev Container that gives you a Linux Python 3.11 environment with the
project's dev tooling (uv, ruff, mypy, pytest), GitHub CLI, and a native VHS
install for demo rendering. Takes ~3-5 minutes to build the first time;
subsequent attaches are seconds. Container-runtime agnostic — works on
Docker Desktop, Podman Desktop, Colima, anything VSCode's Dev Containers
extension can talk to.

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
| Demo renderer | Native VHS + ttyd + ffmpeg installed by `post-create.sh` (no host-Docker bind needed) |

VSCode extensions auto-installed: Python + Pylance + Ruff + Mypy +
Even-Better-TOML + GitHub PR + GitLens.

## First-time setup

1. **Install prerequisites on the host:**
   - VSCode + the [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers).
   - A container runtime — one of:
     - Docker Desktop (Windows / macOS) or Docker Engine (Linux), or
     - [Podman Desktop](https://podman-desktop.io/) (drop-in alternative;
       configure VSCode's Dev Containers extension to use the Podman
       socket — see "Podman Desktop" below).
   - On Windows: WSL2 enabled, with the chosen runtime's WSL integration
     on for your distro.
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
devcontainer. The render script auto-detects this and uses local
vhs:

```bash
./tools/demo/render.sh                       # docs/demo.gif (headline)
./tools/demo/render.sh discover-datasets     # docs/discover-datasets.gif
./tools/demo/render.sh preset-features       # docs/preset-features.gif
./tools/demo/render.sh --all                 # render every tape in one go
```

Renders take ~30 s wall-clock per demo.

The `--docker` mode in `render.sh` is **host-only** — the devcontainer
no longer mounts a Docker / Podman socket (see "Why no Docker socket?"
below). If you specifically want to exercise the `tools/demo/Dockerfile`
path, run `./tools/demo/render.sh --docker` from the host shell where
your container runtime CLI lives, not from inside the devcontainer.

See [`tools/demo/README.md`](../tools/demo/README.md) for the full
mode matrix and timing details.

## Troubleshooting

- **"docker: command not found" inside the container**: expected — the
  devcontainer doesn't ship a Docker CLI any more. Render demos from the
  host (`./tools/demo/render.sh --docker`) or use the native `--local`
  path inside the container, which is the default. See "Why no Docker
  socket?" below.
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

## Podman Desktop

[Podman Desktop](https://podman-desktop.io/) works as a drop-in for
Docker Desktop here — same VSCode Dev Containers extension, same
`devcontainer.json`, no project-side changes needed. Point the
extension at the Podman socket via:

- VSCode setting `dev.containers.dockerPath`: `podman` (or the full
  path on Windows: `C:\Program Files\RedHat\Podman\podman.exe`).
- VSCode setting `dev.containers.mountWaylandSocket`: `false` (avoids
  a Wayland UNC-path probe Podman doesn't support on Windows hosts).
- On Linux, export `DOCKER_HOST=unix://$XDG_RUNTIME_DIR/podman/podman.sock`
  in your shell profile so other tooling agrees on the socket.

Podman runs rootless by default. The `--security-opt seccomp=unconfined`
runArg in `devcontainer.json` still applies; rootless Podman uses its
own seccomp profile but honours the same override flag.

If chromium inside the container complains about user namespaces
under rootless Podman, the host needs `kernel.unprivileged_userns_clone=1`
(default on most modern distros; check with `sysctl
kernel.unprivileged_userns_clone`).

## Why no Docker socket?

Earlier versions of this devcontainer mounted the host Docker socket via
the `ghcr.io/devcontainers/features/docker-outside-of-docker` feature so
`./tools/demo/render.sh --docker` was usable from inside the container.
That feature was removed because:

1. **Nothing in `src/`, `tests/`, or CI talks to Docker.** The project's
   Python code, the test suite (515+ hermetic tests), and the GitHub
   Actions workflow under `.github/workflows/test.yml` make zero
   subprocess calls to `docker`, use no `docker-py`, and ship no
   testcontainers. The socket was never load-bearing for the core dev
   loop.
2. **`render.sh`'s default path inside the container is `--local`.**
   `post-create.sh` installs native VHS / ttyd / ffmpeg, so the render
   script's auto mode resolves to `--local` and never opens the socket.
3. **The `--docker` mode is a maintainer-only diagnostic.** Its only
   use is to exercise the `tools/demo/Dockerfile` path for testing
   that the Docker image still builds and renders. A maintainer doing
   that can run it from the host shell where Docker / Podman already
   live — no need to do it from inside the devcontainer.
4. **Container-runtime portability.** Hard-coding `/var/run/docker.sock`
   broke under Podman, where the host socket sits in a different place.
   Dropping the mount makes the devcontainer host-runtime agnostic.

Trade-off: from inside the devcontainer, `./tools/demo/render.sh --docker`
will fail with "Docker isn't reachable". This is by design — run it from
the host. Documented in `spec.md` §14 #33.

## Why the local Dockerfile?

`devcontainer.json` builds from `Dockerfile` (in this directory) rather
than referencing the Microsoft Python image directly. The Dockerfile is
two lines: it bases on `mcr.microsoft.com/devcontainers/python:1-3.11-
bookworm` and removes `/etc/apt/sources.list.d/yarn.list` before any
`apt-get update` runs. Yarn's apt repo signing key was rotated upstream
and the cached key in Microsoft's image no longer validates — feature
installs (which run `apt-get update`) fail without this workaround. See
the comment at the top of `Dockerfile` for the full rationale.
