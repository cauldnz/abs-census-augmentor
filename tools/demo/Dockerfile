# Custom VHS image with census-augment + supporting unix tooling baked in.
#
# Build context is the repo root, *not* this directory — that lets us COPY
# the project into the image so the demo can run `census-augment` without
# a runtime pip install. Use `tools/demo/render.ps1` (Windows) or
# `tools/demo/render.sh` (Unix) to drive the build with the right context.
#
# Rebuild is needed when:
#   * the project's source or pyproject.toml changes (the `pip install` step
#     re-runs because COPY layers' inputs changed)
#   * you want a newer base vhs image — bump the FROM line and rebuild

FROM ghcr.io/charmbracelet/vhs:latest

ENV DEBIAN_FRONTEND=noninteractive

# - python3 / python3-pip — to install census-augment
# - bsdmainutils         — provides `column`, used by the demo to align the
#                          projected output table
# - git                  — pip install -e . inspects git metadata for some
#                          package versioning cases; cheap and avoids surprises
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-pip bsdmainutils git \
    && rm -rf /var/lib/apt/lists/*

# Bake the project into the image. Non-editable install: census-augment
# lands in site-packages and we throw away the build dir, keeping the
# image lean. The repo is mounted at /vhs at runtime for the tape file,
# config, and input CSV; the installed census-augment doesn't reach back
# into it.
#
# README.md is COPY'd because hatchling reads pyproject.toml's
# `readme = "README.md"` field at build time and errors if it's missing.
#
# datasets/ and features/ MUST also be COPY'd because pyproject.toml's
# `[tool.hatch.build.targets.wheel.force-include]` (added in v1.4.1
# closing #19) bundles those markdown spec dirs into the wheel. Without
# them present in the build context, hatchling raises FileNotFoundError
# and the install step fails. Don't drop these COPY lines without also
# updating pyproject.toml.
WORKDIR /work
COPY pyproject.toml README.md /work/
COPY src /work/src/
COPY datasets /work/datasets/
COPY features /work/features/
RUN pip install --break-system-packages --no-cache-dir /work \
    && rm -rf /work

WORKDIR /vhs
# vhs base image already sets ENTRYPOINT ["vhs"], so `docker run <image> tape.tape`
# just runs vhs against the named tape.
