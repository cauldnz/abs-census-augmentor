# Renders one (or every) README demo GIF using a Docker-isolated VHS.
#
# Usage (from the repo root):
#     .\tools\demo\render.ps1                                  # demo.tape -> docs/demo.gif (headline)
#     .\tools\demo\render.ps1 discover-datasets                # discover-datasets.tape -> docs/discover-datasets.gif
#     .\tools\demo\render.ps1 preset-features                  # preset-features.tape -> docs/preset-features.gif
#     .\tools\demo\render.ps1 --all                            # every *.tape in tools/demo/
#
# The arg picks the tape under tools/demo/ (no path, no extension).
# Output filename mirrors the tape name. With --all, every tape is
# rendered in lexical order; pre-warm and image build happen once
# for the batch.
#
# Requires: Docker Desktop running.

param(
    [string]$Slug = "demo"
)

$ErrorActionPreference = "Stop"

# ---- preflight (shared by all modes) -----------------------------------

if (-not (Test-Path "tools/demo/demo.tape")) {
    Write-Error "Run this script from the repo root (so 'tools/demo/' is visible)."
}

try { docker version --format '{{.Server.Version}}' | Out-Null } catch {
    Write-Error "Docker isn't reachable. Start Docker Desktop and try again."
}

# ---- host cache pre-warm -----------------------------------------------

$hostCache = Join-Path $env:LOCALAPPDATA "census-augment\census-augment\Cache"

# Pick any demo config to drive `census-augment fetch` for the
# boundaries + GCP DataPack - these are the same regardless of which
# config a tape references.
$fetchConfig = "tools/demo/config.yaml"
if (-not (Test-Path $fetchConfig)) {
    $first = Get-ChildItem -Path tools/demo -Filter '*.yaml' | Select-Object -First 1
    if ($first) { $fetchConfig = $first.FullName }
}

if (-not (Test-Path (Join-Path $hostCache "data\boundaries")) -and (Test-Path $fetchConfig)) {
    Write-Host "ABS cache not yet populated - running 'census-augment fetch' on the host first..." -ForegroundColor Yellow
    uv run census-augment fetch --config $fetchConfig --boundaries --census
    if ($LASTEXITCODE -ne 0) {
        Write-Error "census-augment fetch failed. Resolve the error above and re-run."
    }
}

# Pre-run every demo config on the host so registered-dataset caches
# (SEIFA, ERP, etc.) are populated before VHS records any tape. We
# loop across every *.yaml in tools/demo/ rather than hardcoding one
# config so adding a new tape with its own config doesn't require a
# script edit. Errors are swallowed - the tape's own run inside
# Docker will surface any real problem.
Write-Host "Pre-warming registered-dataset caches via host-side runs..." -ForegroundColor Cyan
Get-ChildItem -Path tools/demo -Filter '*.yaml' | ForEach-Object {
    Write-Host "  -> $($_.Name)" -ForegroundColor Cyan
    uv run census-augment run --config $_.FullName *> $null
    # Reset $LASTEXITCODE so a transient pre-warm failure doesn't
    # poison the later docker build / docker run checks.
    $global:LASTEXITCODE = 0
}

# ---- build the VHS image once ------------------------------------------

Write-Host "Building census-augment-vhs image (cached layers reused if source unchanged)..." -ForegroundColor Cyan
docker build -f tools/demo/Dockerfile -t census-augment-vhs . *> $null
if ($LASTEXITCODE -ne 0) {
    docker build -f tools/demo/Dockerfile -t census-augment-vhs .
    Write-Error "Docker build failed. See output above."
}

New-Item -ItemType Directory -Force -Path docs | Out-Null

# ---- render one tape ---------------------------------------------------

function Render-Tape {
    param([string]$Slug)

    $tapePath   = "tools/demo/${Slug}.tape"
    $outputPath = "docs/${Slug}.gif"

    if (-not (Test-Path $tapePath)) {
        Write-Host "Tape file not found: $tapePath" -ForegroundColor Red
        Write-Host "Available tapes:"
        Get-ChildItem -Path tools/demo -Filter '*.tape' | ForEach-Object { "  $($_.Name)" }
        throw "tape missing"
    }

    Write-Host "Rendering ${tapePath} -> ${outputPath} ..." -ForegroundColor Cyan
    docker run --rm `
        -v "${PWD}:/vhs" `
        -v "${hostCache}:/root/.cache/census-augment" `
        census-augment-vhs `
        $tapePath

    if ($LASTEXITCODE -ne 0) { Write-Error "vhs render failed for $Slug." }
}

# ---- arg dispatch ------------------------------------------------------

if ($Slug -eq "--all") {
    $rendered = @()
    Get-ChildItem -Path tools/demo -Filter '*.tape' | ForEach-Object {
        $tapeSlug = [System.IO.Path]::GetFileNameWithoutExtension($_.Name)
        Render-Tape -Slug $tapeSlug
        $rendered += "docs/${tapeSlug}.gif"
    }
    Write-Host ""
    Write-Host "Rendered $($rendered.Count) GIFs:" -ForegroundColor Green
    foreach ($gif in $rendered) { Write-Host "  - $gif" }
    Write-Host "Inspect each and 'git add' the ones you're happy with."
} else {
    Render-Tape -Slug $Slug
    Write-Host "Done. Inspect docs/${Slug}.gif and 'git add' it when you're happy." -ForegroundColor Green
}
