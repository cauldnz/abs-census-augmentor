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

# Bake the project into the image. We install editable from /work so the
# python paths point at /work/src; then we mount the actual repo at /vhs
# at runtime so the tape file, config, and input CSV come from disk.
WORKDIR /work
COPY pyproject.toml /work/
COPY src /work/src/
RUN pip install --break-system-packages --no-cache-dir -e /work

WORKDIR /vhs
# vhs base image already sets ENTRYPOINT ["vhs"], so `docker run <image> tape.tape`
# just runs vhs against the named tape.
