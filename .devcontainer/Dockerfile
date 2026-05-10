# Dev container base image, with one workaround applied.
#
# Why this Dockerfile exists: Microsoft's Python devcontainer base
# image (`mcr.microsoft.com/devcontainers/python:1-3.11-bookworm`)
# ships a yarn apt source at `/etc/apt/sources.list.d/yarn.list`
# whose upstream signing key (`62D54FD4003F6525`) was rotated by the
# yarn project. The pre-baked key in the image no longer validates,
# so every `apt-get update` run during devcontainer feature
# installation fails with:
#
#     The following signatures couldn't be verified because the public
#     key is not available: NO_PUBKEY 62D54FD4003F6525
#
# Removing the yarn source line ahead of the feature build sidesteps
# the issue. Nothing in this project uses yarn; if that ever
# changes, install it explicitly via a devcontainer feature or `npm
# install -g yarn` rather than reinstating this apt source.
#
# This file can be deleted once Microsoft refreshes the upstream
# image with the new yarn key. Track via:
#   https://github.com/devcontainers/images/issues
FROM mcr.microsoft.com/devcontainers/python:1-3.11-bookworm

RUN rm -f /etc/apt/sources.list.d/yarn.list
