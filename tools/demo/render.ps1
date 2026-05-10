# Renders one of the README demo GIFs using a Docker-isolated VHS.
#
# Usage (from the repo root):
#     .\tools\demo\render.ps1                                  # demo.tape -> docs/demo.gif (headline)
#     .\tools\demo\render.ps1 discover-datasets                # discover-datasets.tape -> docs/discover-datasets.gif
#     .\tools\demo\render.ps1 preset-features                  # preset-features.tape -> docs/preset-features.gif
#
# The arg picks the tape under tools/demo/ (no path, no extension).
# Output filename mirrors the tape name.
#
# Requires: Docker Desktop running.
#
# What it does:
#   1. Builds (or reuses, if cached) a custom VHS image with
#      census-augment pre-installed. See tools/demo/Dockerfile.
#   2. Mounts your repo at /vhs and your local ABS cache (boundaries +
#      DataPacks + any registered-dataset data the demo touches) at
#      the container's expected path. No network needed during
#      rendering as long as the cache is populated.
#   3. Runs vhs against the chosen tape - the GIF lands under docs/.

param(
    [string]$Slug = "demo"
)

$ErrorActionPreference = "Stop"

$tapePath   = "tools/demo/${Slug}.tape"
$outputPath = "docs/${Slug}.gif"

if (-not (Test-Path $tapePath)) {
    Write-Host "Tape file not found: $tapePath" -ForegroundColor Red
    Write-Host "Available tapes:"
    Get-ChildItem -Path tools/demo -Filter '*.tape' | ForEach-Object { "  $($_.Name)" }
    exit 1
}

# Sanity: must run from the repo root so that paths in the tape resolve.
if (-not (Test-Path "tools/demo/demo.tape")) {
    Write-Error "Run this script from the repo root (so 'tools/demo/' is visible)."
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

# Pre-run the demo's config once on the host so registered-dataset
# caches (SEIFA / ERP / DSS / ATO depending on what the demo's config
# references) are populated before VHS hits Record. This avoids
# capturing 'downloading <dataset>...' messages in the visible part of
# the GIF.
if (Test-Path "tools/demo/config.yaml") {
    Write-Host "Pre-warming registered-dataset caches via a host-side run..." -ForegroundColor Cyan
    uv run census-augment run --config tools/demo/config.yaml *> $null
    # Don't fail the render if the pre-warm hits a transient — the
    # tape's own run inside Docker will surface any real error.
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
Write-Host "Rendering ${tapePath} -> ${outputPath} ..." -ForegroundColor Cyan
docker run --rm `
    -v "${PWD}:/vhs" `
    -v "${hostCache}:/root/.cache/census-augment" `
    census-augment-vhs `
    $tapePath

if ($LASTEXITCODE -ne 0) { Write-Error "vhs render failed." }

Write-Host "Done. Inspect ${outputPath} and 'git add' it when you're happy." -ForegroundColor Green
