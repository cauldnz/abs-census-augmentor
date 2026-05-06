# Renders docs/demo.gif using a Docker-isolated VHS.
#
# Usage (from the repo root):
#     .\tools\demo\render.ps1
#
# Requires: Docker Desktop running.
#
# What it does:
#   1. Builds (or reuses, if cached) a custom VHS image with census-augment
#      pre-installed. See tools/demo/Dockerfile.
#   2. Mounts your repo at /vhs and your local ABS cache (boundaries +
#      DataPacks) at the container's expected path. No network needed
#      during rendering as long as the cache is populated.
#   3. Runs vhs against tools/demo/demo.tape - the GIF lands at
#      docs/demo.gif on the host.

$ErrorActionPreference = "Stop"

# Sanity: must run from the repo root so that paths in the tape resolve.
if (-not (Test-Path "tools/demo/demo.tape")) {
    Write-Error "Run this script from the repo root (so 'tools/demo/demo.tape' is visible)."
}

# Sanity: Docker reachable?
try { docker version --format '{{.Server.Version}}' | Out-Null } catch {
    Write-Error "Docker isn't reachable. Start Docker Desktop and try again."
}

# Sanity: ABS cache populated? If not, run the host's census-augment first
# so the heavy data is on disk *before* we rope Docker in.
$hostCache = Join-Path $env:LOCALAPPDATA "census-augment\census-augment\Cache"
if (-not (Test-Path (Join-Path $hostCache "data\boundaries"))) {
    Write-Host "ABS cache not yet populated - running 'census-augment fetch' on the host first..." -ForegroundColor Yellow
    uv run census-augment fetch --config tools/demo/config.yaml --boundaries --census
    if ($LASTEXITCODE -ne 0) {
        Write-Error "census-augment fetch failed. Resolve the error above and re-run."
    }
}

# Build the image (cheap when nothing changed - Docker re-uses layers).
Write-Host "Building census-augment-vhs image (cached layers reused if source unchanged)..." -ForegroundColor Cyan
docker build -f tools/demo/Dockerfile -t census-augment-vhs .
if ($LASTEXITCODE -ne 0) { Write-Error "Docker build failed." }

# Make sure docs/ exists for the output.
New-Item -ItemType Directory -Force -Path docs | Out-Null

# Render. Mounts:
#   ${PWD}     -> /vhs                          (repo: tape, config, csv)
#   $hostCache -> /root/.cache/census-augment   (pre-populated ABS data)
Write-Host "Rendering tools/demo/demo.tape -> docs/demo.gif ..." -ForegroundColor Cyan
docker run --rm `
    -v "${PWD}:/vhs" `
    -v "${hostCache}:/root/.cache/census-augment" `
    census-augment-vhs `
    tools/demo/demo.tape

if ($LASTEXITCODE -ne 0) { Write-Error "vhs render failed." }

Write-Host "Done. Inspect docs/demo.gif and 'git add' it when you're happy." -ForegroundColor Green
